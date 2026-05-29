"""
order_manager.py

Production-style SINGLE FILE order manager.

KEY DESIGN:
    Broker (AngelOne) is SOURCE OF TRUTH.
    SQLite is ONLY used for:
        - AI signals (read-only)
        - audit logs
        - watchlist
        - cautionary symbols
        - TSL history

FIXES IN THIS VERSION:
    1. Duplicate trades    — in-session placed_symbols set + dedup signals by latest row
    2. Duplicate signals   — GROUP BY symbol, take MAX(run_date) row
    3. Cautionary retries  — in-memory session_cautionary set, max 2 attempts per symbol
    4. Orderbook rate limit — orderbook fetched every ORDERBOOK_INTERVAL_MINS, not every heartbeat
    5. TSL token lookup    — strips -EQ suffix from broker position symbols before lookup

FLOW:
    09:00 -> start
    09:15 -> sync existing broker positions, apply TSL
    09:20 -> place fresh entry orders
    Every HEARTBEAT_SECS:
        - sync positions from broker
        - apply TSL every TSL_INTERVAL_MINS
        - reconcile every RECONCILE_INTERVAL_MINS
        - print P&L snapshot
    15:20 -> EOD report
    15:30 -> exit
"""

import os
import sys
import time
import sqlite3
import traceback
from datetime import datetime, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from brokers.angleone.hist_data import hist_data
from brokers.angleone.executor import AngelOneExecutor


# =========================================================
# CONFIG
# =========================================================

TOTAL_CAPITAL           = float(os.getenv("TOTAL_CAPITAL",           "1000000"))
MAX_TRADES              = int(os.getenv("MAX_TRADES",                 "25"))
MAX_RISK_PCT            = float(os.getenv("MAX_RISK_PCT",             "0.03"))
RR_RATIO                = float(os.getenv("RR_RATIO",                 "3.0"))
POSITION_SIZE_PCT       = float(os.getenv("POSITION_SIZE_PCT",        "0.05"))

ENTRY_OFFSET_PCT        = float(os.getenv("ENTRY_OFFSET_PCT",         "0.0"))
MIN_CONFIDENCE          = float(os.getenv("MIN_CONFIDENCE",           "0.85"))
MAX_GAP_PCT             = float(os.getenv("MAX_GAP_PCT",              "2.0"))

HEARTBEAT_SECS          = int(os.getenv("HEARTBEAT_SECS",             "30"))   # was 5 — reduced API calls
TSL_INTERVAL_MINS       = int(os.getenv("TSL_INTERVAL_MINS",          "30"))
RECONCILE_INTERVAL_MINS = int(os.getenv("RECONCILE_INTERVAL_MINS",   "10"))
ORDERBOOK_INTERVAL_MINS = int(os.getenv("ORDERBOOK_INTERVAL_MINS",   "5"))    # FIX 4: rate limit guard

MAX_CAUTIONARY_RETRIES  = int(os.getenv("MAX_CAUTIONARY_RETRIES",    "2"))    # FIX 3

DB_PATH                 = os.getenv("DB_PATH",      "data/signals.db")
PRODUCT_TYPE            = os.getenv("PRODUCT_TYPE", "DELIVERY")
DRY_RUN                 = os.getenv("DRY_RUN",      "true").lower() == "true"

MARKET_OPEN_TSL         = (9,  15)
MARKET_OPEN_ENTRY       = (9,  20)
MARKET_CLOSE_EOD        = (15, 20)
MARKET_EXIT             = (15, 30)


# =========================================================
# UTILS
# =========================================================

def now():
    return datetime.now()


def log(msg):
    print(f"[{now().strftime('%H:%M:%S')}] {msg}")


def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        f = float(v)
        return default if (f != f) else f  # NaN check
    except Exception:
        return default


def round_to_tick(price, tick=0.05):
    """Round to nearest 5 paise — NSE tick size."""
    return round(round(price / tick) * tick, 2)


def strip_eq(symbol: str) -> str:
    """
    FIX 5: AngelOne position API returns 'HINDALCO-EQ',
    but token_lookup() expects bare 'HINDALCO'.
    """
    return symbol.replace("-EQ", "").replace("-BE", "").strip()


def wait_until(hour, minute):
    target = now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now() >= target:
        return
    while now() < target:
        rem = int((target - now()).total_seconds())
        print(f"\rWaiting for {hour:02d}:{minute:02d} | {rem}s remaining",
              end="", flush=True)
        time.sleep(5)
    print()


# =========================================================
# DATABASE MANAGER
# =========================================================

class DBManager:

    def __init__(self, db_path):
        self.db_path = db_path

    def conn(self):
        return sqlite3.connect(self.db_path)

    def setup(self):
        with self.conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_audit (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol    TEXT,
                    action    TEXT,
                    side      TEXT,
                    price     REAL,
                    quantity  INTEGER,
                    order_id  TEXT,
                    status    TEXT,
                    notes     TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tsl_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol    TEXT,
                    ltp       REAL,
                    old_sl    REAL,
                    new_sl    REAL,
                    notes     TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp  TEXT,
                    symbol     TEXT,
                    decision   TEXT,
                    confidence REAL,
                    reason     TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cautionary_symbols (
                    symbol     TEXT PRIMARY KEY,
                    reason     TEXT,
                    flagged_at TEXT,
                    attempts   INTEGER DEFAULT 1
                )
            """)
            # migrate: add attempts column if missing
            try:
                conn.execute(
                    "ALTER TABLE cautionary_symbols ADD COLUMN attempts INTEGER DEFAULT 1"
                )
            except Exception:
                pass
            conn.commit()

    def get_latest_signals(self) -> pd.DataFrame:
        """
        FIX 2: Deduplicate signals — one row per symbol,
        taking the row with the latest run_date.
        Returns symbols sorted by supervisor_conf DESC.
        """
        with self.conn() as conn:
            try:
                latest = conn.execute("""
                    SELECT date(run_date)
                    FROM   signals
                    WHERE  final_decision IN ('BUY','SELL')
                    ORDER  BY run_date DESC
                    LIMIT  1
                """).fetchone()

                if not latest:
                    return pd.DataFrame()

                signal_date = latest[0]
                log(f"Using signals from {signal_date}")

                # Use MAX(rowid) per symbol to get the latest duplicate row
                df = pd.read_sql("""
                    SELECT s.symbol,
                           s.final_decision,
                           s.avg_confidence,
                           s.supervisor_conf,
                           s.signal_price,
                           s.timeframe,
                           s.reasoning
                    FROM   signals s
                    INNER JOIN (
                        SELECT symbol, MAX(rowid) AS max_rid
                        FROM   signals
                        WHERE  date(run_date) = ?
                        AND    final_decision IN ('BUY','SELL')
                        GROUP  BY symbol
                    ) dedup ON s.symbol = dedup.symbol
                           AND s.rowid  = dedup.max_rid
                    ORDER  BY s.supervisor_conf DESC
                """, conn, params=[signal_date])

                log(f"Loaded {len(df)} unique signals (deduped)")
                return df

            except Exception as e:
                log(f"Signal read error: {e}")
                return pd.DataFrame()

    def add_watchlist(self, symbol, decision, conf, reason):
        with self.conn() as conn:
            conn.execute("""
                INSERT INTO watchlist (timestamp, symbol, decision, confidence, reason)
                VALUES (?,?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"), symbol, decision, conf, reason])
            conn.commit()

    def log_trade(self, symbol, action, side, price, qty, order_id, status, notes=""):
        with self.conn() as conn:
            conn.execute("""
                INSERT INTO trade_audit
                    (timestamp, symbol, action, side, price, quantity, order_id, status, notes)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"), symbol, action,
                  side, price, qty, order_id, status, notes])
            conn.commit()

    def log_tsl(self, symbol, ltp, old_sl, new_sl):
        with self.conn() as conn:
            conn.execute("""
                INSERT INTO tsl_log (timestamp, symbol, ltp, old_sl, new_sl, notes)
                VALUES (?,?,?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"), symbol, ltp, old_sl, new_sl, "TRAIL"])
            conn.commit()

    def mark_cautionary(self, symbol, reason):
        """Persist cautionary flag. Increments attempt count on re-flag."""
        with self.conn() as conn:
            conn.execute("""
                INSERT INTO cautionary_symbols (symbol, reason, flagged_at, attempts)
                VALUES (?,?,?,1)
                ON CONFLICT(symbol) DO UPDATE
                    SET attempts   = attempts + 1,
                        reason     = excluded.reason,
                        flagged_at = excluded.flagged_at
            """, [symbol, reason, now().strftime("%Y-%m-%d %H:%M:%S")])
            conn.commit()

    def get_cautionary_attempts(self, symbol) -> int:
        with self.conn() as conn:
            r = conn.execute("""
                SELECT attempts FROM cautionary_symbols WHERE symbol=?
            """, [symbol]).fetchone()
            return r[0] if r else 0

    def is_cautionary(self, symbol) -> bool:
        """Permanently blocked after MAX_CAUTIONARY_RETRIES attempts."""
        return self.get_cautionary_attempts(symbol) >= MAX_CAUTIONARY_RETRIES


# =========================================================
# BROKER STATE
# =========================================================

class BrokerState:

    def __init__(self, client):
        self.client           = client
        self.positions_cache  = {}   # key = bare symbol (no -EQ)
        self.orderbook_cache  = []
        self._last_ob_refresh = None

    def refresh(self):
        """
        FIX 4: Only fetch orderbook every ORDERBOOK_INTERVAL_MINS.
        Always refresh positions (needed for TSL / duplicate check).
        """
        self._refresh_positions()
        self._refresh_orderbook_if_due()

    def _refresh_positions(self):
        try:
            positions = self.client.angel_obj.position()
            data = positions.get("data") or []
            self.positions_cache = {}
            for p in data:
                qty = abs(int(safe_float(p.get("netqty", 0))))
                if qty <= 0:
                    continue
                raw_symbol = p.get("tradingsymbol", "")
                symbol     = strip_eq(raw_symbol)   # FIX 5: strip -EQ
                self.positions_cache[symbol] = {
                    "symbol":    symbol,
                    "raw":       raw_symbol,
                    "qty":       qty,
                    "side":      "BUY" if safe_float(p.get("netqty")) > 0 else "SELL",
                    "avg_price": safe_float(p.get("averageprice")),
                    "ltp":       safe_float(p.get("ltp")),
                    "pnl":       safe_float(p.get("pnl")),
                }
        except Exception as e:
            log(f"Position refresh failed: {e}")

    def _refresh_orderbook_if_due(self):
        """FIX 4: Rate-limit orderbook calls."""
        if self._last_ob_refresh is not None:
            elapsed = (now() - self._last_ob_refresh).total_seconds() / 60
            if elapsed < ORDERBOOK_INTERVAL_MINS:
                return
        try:
            ob = self.client.angel_obj.orderBook()
            self.orderbook_cache = ob.get("data") or []
            self._last_ob_refresh = now()
        except Exception as e:
            log(f"Orderbook refresh failed: {e}")

    def get_positions(self):
        return self.positions_cache

    def has_position(self, symbol):
        """FIX 1 support: checks bare symbol (no -EQ)."""
        return strip_eq(symbol) in self.positions_cache

    def get_position_symbols(self):
        return list(self.positions_cache.keys())

    def position_count(self):
        return len(self.positions_cache)


# =========================================================
# RISK MANAGER
# =========================================================

class RiskManager:

    def calc_entry(self, signal_price, side):
        offset = ENTRY_OFFSET_PCT / 100
        raw    = signal_price * (1 + offset) if side == "BUY" \
                 else signal_price * (1 - offset)
        return round_to_tick(raw)

    def calc_sl(self, entry, side):
        raw = entry * (1 - MAX_RISK_PCT) if side == "BUY" \
              else entry * (1 + MAX_RISK_PCT)
        return round_to_tick(raw)

    def calc_target(self, entry, side):
        raw = entry * (1 + MAX_RISK_PCT * RR_RATIO) if side == "BUY" \
              else entry * (1 - MAX_RISK_PCT * RR_RATIO)
        return round_to_tick(raw)

    def calc_qty(self, price, available_capital=None):
        cap   = available_capital if available_capital else TOTAL_CAPITAL
        alloc = min(TOTAL_CAPITAL * POSITION_SIZE_PCT, cap)
        return max(int(alloc / price), 1)


# =========================================================
# EXECUTION ENGINE
# =========================================================

class ExecutionEngine:

    def __init__(self, client, executor, db, broker_state, risk):
        self.client       = client
        self.executor     = executor
        self.db           = db
        self.broker_state = broker_state
        self.risk         = risk

        # FIX 1 + FIX 3: in-memory session state
        self.placed_this_session  = set()   # symbols placed today — never retry
        self.cautionary_this_run  = set()   # cautionary hits this run — skip immediately

    def place_entries(self):
        signals = self.db.get_latest_signals()

        if signals.empty:
            log("No signals found")
            return

        placed    = 0
        skipped   = 0
        watchlist = 0

        for _, row in signals.iterrows():
            try:
                symbol       = str(row["symbol"]).strip()
                side         = str(row["final_decision"]).strip()
                conf         = safe_float(row.get("supervisor_conf") or row.get("avg_confidence"))
                signal_price = safe_float(row.get("signal_price"))

                # ── Confidence filter
                if conf < MIN_CONFIDENCE:
                    if conf >= 0.70:
                        self.db.add_watchlist(symbol, side, conf, "Medium confidence")
                        watchlist += 1
                    else:
                        skipped += 1
                    continue

                # ── FIX 3: skip if cautionary hit this run
                if symbol in self.cautionary_this_run:
                    log(f"SKIP {symbol}: cautionary this run")
                    skipped += 1
                    continue

                # ── FIX 3: skip if persisted cautionary (>= MAX_CAUTIONARY_RETRIES)
                if self.db.is_cautionary(symbol):
                    log(f"SKIP {symbol}: persistently cautionary (>={MAX_CAUTIONARY_RETRIES} attempts)")
                    skipped += 1
                    continue

                # ── FIX 1: skip if already placed in this session
                if symbol in self.placed_this_session:
                    log(f"SKIP {symbol}: already placed this session")
                    skipped += 1
                    continue

                # ── FIX 1: skip if broker already has a position
                if self.broker_state.has_position(symbol):
                    log(f"SKIP {symbol}: position already exists at broker")
                    skipped += 1
                    continue

                # ── Max trades
                if self.broker_state.position_count() + placed >= MAX_TRADES:
                    log(f"Max trades ({MAX_TRADES}) reached")
                    break

                # ── Signal price must be valid
                if signal_price <= 0:
                    log(f"SKIP {symbol}: invalid signal_price={signal_price}")
                    skipped += 1
                    continue

                # ── LTP
                try:
                    token = self.client.token_lookup(symbol)
                    ltp   = self.client.get_ltp_data(token, exchange="NSE")
                except Exception as e:
                    log(f"SKIP {symbol}: LTP failed — {e}")
                    skipped += 1
                    continue

                # ── Gap check
                gap_pct = ((ltp - signal_price) / signal_price * 100) if side == "BUY" \
                          else ((signal_price - ltp) / signal_price * 100)

                if abs(gap_pct) > MAX_GAP_PCT:
                    log(f"SKIP {symbol} gap={gap_pct:+.1f}%")
                    skipped += 1
                    continue

                # ── Price calculations (all from signal_price, not LTP)
                entry  = self.risk.calc_entry(signal_price, side)
                sl     = self.risk.calc_sl(entry, side)
                target = self.risk.calc_target(entry, side)
                qty    = self.risk.calc_qty(entry)

                log(f"\n{symbol} {side}"
                    f"\n  Signal=Rs{signal_price:.2f}  LTP=Rs{ltp:.2f}  gap={gap_pct:+.1f}%"
                    f"\n  Entry=Rs{entry:.2f}  SL=Rs{sl:.2f}  Target=Rs{target:.2f}"
                    f"\n  Qty={qty}  Conf={conf:.2f}")

                # ── Execute
                if DRY_RUN:
                    order_id = f"DRY_{symbol}_{now().strftime('%H%M%S')}"
                    status   = "DRY_RUN"
                    log(f"  [DRY RUN] {symbol} {side} {qty}@{entry}")
                else:
                    result   = self.executor.execute(
                        symbol=symbol, signal=side,
                        close_price=entry, lot_size=qty, sl_pct=MAX_RISK_PCT,
                    )
                    status   = result.get("status", "FAILED")
                    order_id = result.get("order_id", "UNKNOWN")

                    if status == "CAUTIONARY":
                        # FIX 3: flag in-session and persist
                        self.cautionary_this_run.add(symbol)
                        self.db.mark_cautionary(symbol, "Broker cautionary AB4036")
                        log(f"CAUTIONARY {symbol} — flagged, skipping")
                        skipped += 1
                        continue

                    if status == "FAILED":
                        log(f"FAILED {symbol}: {result.get('error','unknown')}")
                        skipped += 1
                        continue

                # ── FIX 1: mark placed in session BEFORE logging
                self.placed_this_session.add(symbol)

                self.db.log_trade(
                    symbol=symbol, action="ENTRY", side=side,
                    price=entry, qty=qty, order_id=order_id, status=status,
                    notes=f"SL={sl} TARGET={target} signal={signal_price}"
                )

                placed += 1
                log(f"  OK {symbol} order_id={order_id}")

                time.sleep(0.5)

            except Exception as e:
                log(f"Entry error ({row.get('symbol','?')}): {e}")
                traceback.print_exc()

        log(f"\nEntries placed: {placed} | Skipped: {skipped} | Watchlist: {watchlist}")


# =========================================================
# TSL ENGINE
# =========================================================

class TSLEngine:

    def __init__(self, client, db, broker_state, risk):
        self.client       = client
        self.db           = db
        self.broker_state = broker_state
        self.risk         = risk
        self.last_tsl     = {}      # symbol -> last SL value

    def process(self):
        positions = self.broker_state.get_positions()
        if not positions:
            return
        for symbol, pos in positions.items():
            try:
                self._update_position(symbol, pos)
            except Exception as e:
                log(f"TSL error {symbol}: {e}")
                traceback.print_exc()

    def _update_position(self, symbol, pos):
        # FIX 5: symbol is already stripped by BrokerState._refresh_positions()
        token = self.client.token_lookup(symbol)
        ltp   = self.client.get_ltp_data(token, exchange="NSE")

        side      = pos["side"]
        avg_price = pos["avg_price"]

        # Current SL: use tracked value or recalculate from avg_price
        old_sl = self.last_tsl.get(symbol, self.risk.calc_sl(avg_price, side))

        new_sl = round_to_tick(ltp * (1 - MAX_RISK_PCT)) if side == "BUY" \
                 else round_to_tick(ltp * (1 + MAX_RISK_PCT))

        # Only trail in favour
        should_trail = (side == "BUY" and new_sl > old_sl) or \
                       (side == "SELL" and new_sl < old_sl)

        # Minimum hold: 30 mins after avg_price was set (approximate via last_tsl absence)
        if symbol not in self.last_tsl:
            log(f"  {symbol}: first TSL check — setting baseline SL={old_sl:.2f}")
            self.last_tsl[symbol] = old_sl
            return

        if not should_trail:
            log(f"  {symbol}: LTP=Rs{ltp:.2f} SL=Rs{old_sl:.2f} — no trail")
            return

        log(f"  ^ TSL {symbol}: Rs{old_sl:.2f} -> Rs{new_sl:.2f} (LTP Rs{ltp:.2f})")
        self.last_tsl[symbol] = new_sl
        self.db.log_tsl(symbol, ltp, old_sl, new_sl)

        if DRY_RUN:
            return

        # Modify SL order at broker
        # AngelOne: cancel old SL order then place new one
        # TODO: store sl_order_id in trade_audit and cancel it here
        # For now log only — add cancelOrder + placeOrder when sl_order_id tracking is added
        log(f"    [LIVE] SL modify: place SELL LIMIT {symbol} qty={pos['qty']} @ {new_sl}")


# =========================================================
# RECONCILIATION ENGINE
# =========================================================

class ReconciliationEngine:

    def __init__(self, broker_state, db):
        self.broker_state = broker_state
        self.db           = db

    def reconcile(self):
        positions = self.broker_state.get_positions()
        ob        = self.broker_state.orderbook_cache

        log(f"Reconcile | Open positions={len(positions)} | Orderbook entries={len(ob)}")

        # Check for positions without a corresponding SL order
        if ob:
            open_buy_symbols = {s for s, p in positions.items() if p["side"] == "BUY"}
            sl_symbols = {
                strip_eq(o.get("tradingsymbol", ""))
                for o in ob
                if o.get("transactiontype") == "SELL"
                and o.get("status") in ("open", "trigger pending", "AMO REQ RECEIVED")
            }
            missing_sl = open_buy_symbols - sl_symbols
            if missing_sl:
                log(f"  ! Missing SL orders for: {missing_sl} — place manually or check broker")


# =========================================================
# REPORTING ENGINE
# =========================================================

class ReportingEngine:

    def __init__(self, db, broker_state):
        self.db           = db
        self.broker_state = broker_state

    def print_live_snapshot(self):
        positions = self.broker_state.get_positions()
        if not positions:
            return

        log(f"\n  -- P&L Snapshot {now().strftime('%H:%M')} --")
        total = 0.0
        for symbol, p in positions.items():
            pnl    = safe_float(p["pnl"])
            total += pnl
            log(f"  {symbol:<15} Qty={p['qty']:<5} "
                f"LTP=Rs{p['ltp']:<10.2f} "
                f"Avg=Rs{p['avg_price']:<10.2f} "
                f"PnL=Rs{pnl:+.2f}")
        log(f"  TOTAL PnL: Rs{total:+.2f}\n")

    def eod(self):
        log("\n" + "=" * 60)
        log("EOD REPORT")
        log("=" * 60)
        self.print_live_snapshot()

        # Trade audit summary
        with self.db.conn() as conn:
            try:
                df = pd.read_sql("""
                    SELECT symbol, action, side, price, quantity,
                           order_id, status, notes, timestamp
                    FROM   trade_audit
                    WHERE  date(timestamp) = date('now')
                    ORDER  BY timestamp
                """, conn)
                if not df.empty:
                    log(f"\nToday's trades ({len(df)} entries):")
                    for _, row in df.iterrows():
                        log(f"  {row['timestamp'][11:19]} {row['symbol']:<12} "
                            f"{row['action']:<6} {row['side']:<5} "
                            f"Rs{row['price']:.2f} x{row['quantity']} "
                            f"[{row['status']}] {row['order_id']}")
            except Exception as e:
                log(f"Trade audit read error: {e}")

        # Cautionary list
        with self.db.conn() as conn:
            try:
                rows = conn.execute("""
                    SELECT symbol, attempts, reason FROM cautionary_symbols
                    ORDER BY flagged_at DESC LIMIT 20
                """).fetchall()
                if rows:
                    log(f"\nCautionary symbols ({len(rows)}):")
                    for r in rows:
                        log(f"  {r[0]:<15} attempts={r[1]} — {r[2]}")
            except Exception:
                pass

        log("=" * 60)


# =========================================================
# ORDER MANAGER APP
# =========================================================

class OrderManagerApp:

    def __init__(self):
        self.db           = DBManager(DB_PATH)
        self.db.setup()

        self.client       = hist_data()
        self.client.log_in()

        self.executor     = AngelOneExecutor(self.client)
        self.broker_state = BrokerState(self.client)
        self.risk         = RiskManager()

        self.execution    = ExecutionEngine(
            self.client, self.executor, self.db, self.broker_state, self.risk)
        self.tsl          = TSLEngine(
            self.client, self.db, self.broker_state, self.risk)
        self.reconciler   = ReconciliationEngine(self.broker_state, self.db)
        self.reporter     = ReportingEngine(self.db, self.broker_state)

        self._last_tsl_run        = None
        self._last_reconcile_run  = None

        log("=" * 60)
        log("ORDER MANAGER STARTED")
        log(f"Capital: Rs{TOTAL_CAPITAL:,.0f} | Max trades: {MAX_TRADES}")
        log(f"Position size: {POSITION_SIZE_PCT*100:.0f}% | "
            f"Min conf: {MIN_CONFIDENCE} | Max gap: {MAX_GAP_PCT:.1f}%")
        log(f"SL: {MAX_RISK_PCT*100:.1f}% | "
            f"Target: {MAX_RISK_PCT*RR_RATIO*100:.1f}% (1:{RR_RATIO:.0f})")
        log(f"Product: {PRODUCT_TYPE} | DRY RUN: {DRY_RUN}")
        log(f"Heartbeat: {HEARTBEAT_SECS}s | TSL every: {TSL_INTERVAL_MINS}m | "
            f"Orderbook every: {ORDERBOOK_INTERVAL_MINS}m")
        log("=" * 60)

    def run(self):
        today = now()
        if today.weekday() >= 5:
            log(f"{'Saturday' if today.weekday()==5 else 'Sunday'} — NSE closed")
            return

        # ── 9:15 — sync + TSL for carried positions
        log(f"\nWaiting for {MARKET_OPEN_TSL[0]:02d}:{MARKET_OPEN_TSL[1]:02d} "
            f"— sync + TSL for existing positions")
        wait_until(*MARKET_OPEN_TSL)
        self._sync()
        self._run_tsl()

        # ── 9:20 — place entries
        log(f"\nWaiting for {MARKET_OPEN_ENTRY[0]:02d}:{MARKET_OPEN_ENTRY[1]:02d} "
            f"— placing fresh entries")
        wait_until(*MARKET_OPEN_ENTRY)
        self._sync()
        self.execution.place_entries()

        # ── Main loop until 3:20 PM
        market_close = now().replace(
            hour=MARKET_CLOSE_EOD[0], minute=MARKET_CLOSE_EOD[1], second=0)

        while now() < market_close:
            try:
                self._sync()
                self._run_tsl_if_due()
                self._run_reconcile_if_due()
                self.reporter.print_live_snapshot()
                time.sleep(HEARTBEAT_SECS)

            except KeyboardInterrupt:
                break
            except Exception as e:
                log(f"Main loop error: {e}")
                traceback.print_exc()
                time.sleep(10)

        # ── EOD
        self.reporter.eod()
        log(f"\nDone. Waiting for {MARKET_EXIT[0]:02d}:{MARKET_EXIT[1]:02d}")
        wait_until(*MARKET_EXIT)

    def _sync(self):
        self.broker_state.refresh()

    def _run_tsl(self):
        self.tsl.process()
        self._last_tsl_run = now()

    def _run_tsl_if_due(self):
        if self._last_tsl_run is None:
            self._run_tsl()
            return
        mins = (now() - self._last_tsl_run).total_seconds() / 60
        if mins >= TSL_INTERVAL_MINS:
            self._run_tsl()

    def _run_reconcile_if_due(self):
        if self._last_reconcile_run is None:
            self.reconciler.reconcile()
            self._last_reconcile_run = now()
            return
        mins = (now() - self._last_reconcile_run).total_seconds() / 60
        if mins >= RECONCILE_INTERVAL_MINS:
            self.reconciler.reconcile()
            self._last_reconcile_run = now()


# =========================================================
# MAIN
# =========================================================

def main():
    app = OrderManagerApp()
    app.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Interrupted")
    except Exception as e:
        log(f"Fatal error: {e}")
        traceback.print_exc()