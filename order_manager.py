"""
order_manager.py  —  v7

Imports from:
  om_config.py      — all configuration constants
  om_utils.py       — log, safe_float, safe_int, round_to_tick, strip_eq, wait_until
  broker_client.py  — TokenCache, FillResult, BrokerState, SLPlacer, get_freeze_qty

ALL 8 FIXES IN THIS VERSION:
  1. Duplicate SL During TSL         — mark_sl_replace_pending blocks reconcile
  2. Partial Fill Growth             — reconcile computes uncovered_qty = broker_qty - active_sl_qty
  3. Reconcile SL Qty Summed         — get_active_sl_qty_total sums ALL active SL orders
  4. Stale Orderbook Cache           — force_orderbook_refresh after every SL place/cancel
  5. Broker Position Sync After Fill — sync_position_from_broker() after inject_position
  6. Exchange Freeze Qty             — SLPlacer.place() caps qty to get_freeze_qty()
  7. Kill Switch                     — KILL_SWITCH file checked every heartbeat
  8. Position Exit Detection         — _refresh_positions() logs closed positions + DB audit

PREVIOUS FIXES RETAINED:
  - Signal dedup (MAX rowid per symbol)
  - Duplicate trade guard (placed_this_session + inject_position)
  - Cautionary retries (session set + MAX_CAUTIONARY_RETRIES)
  - Orderbook rate limiting (ORDERBOOK_INTERVAL_MINS)
  - Token cache TTL (TOKEN_CACHE_TTL_MINS)
  - STOPLOSS_LIMIT with correct trigger direction
  - SL only after fill confirmation (actual qty + price)
  - Place new SL before cancelling old (no naked window)
  - TSL rollback on failure (no state mutation on new SL failure)
  - Stale TSL state cleanup when position disappears
  - SQLite WAL + timeout
  - Failure persistence to DB
  - Per-order capital reservations
  - Partial fill handling (actual qty + price from orderbook)
  - Reconcile uses TSL level not original SL
"""

import os
import sys
import sqlite3
import traceback
import time

import pandas as pd

from om_config import (
    TOTAL_CAPITAL, MAX_TRADES, MAX_RISK_PCT, RR_RATIO, POSITION_SIZE_PCT,
    ENTRY_OFFSET_PCT, MIN_CONFIDENCE, MAX_GAP_PCT,
    HEARTBEAT_SECS, TSL_INTERVAL_MINS, RECONCILE_INTERVAL_MINS,
    ORDERBOOK_INTERVAL_MINS, FAILURE_COOLDOWN_MINS, MAX_CAUTIONARY_RETRIES,
    SL_TRIGGER_PCT, TOKEN_CACHE_TTL_MINS,
    DB_PATH, PRODUCT_TYPE, DRY_RUN, KILL_SWITCH_FILE,
    MARKET_OPEN_TSL, MARKET_OPEN_ENTRY, MARKET_CLOSE_EOD, MARKET_EXIT,
)
from om_utils import log, safe_float, safe_int, round_to_tick, strip_eq, now, wait_until
from broker_client import TokenCache, FillResult, BrokerState, SLPlacer, get_freeze_qty
from brokers.angleone.hist_data import hist_data


# =========================================================
# KILL SWITCH  (FIX 7)
# =========================================================

def kill_switch_active() -> bool:
    """
    FIX 7: If file named KILL_SWITCH exists in working directory,
    all activity stops immediately. Create the file to halt trading.
    Delete it to resume (requires restart).
    """
    return os.path.exists(KILL_SWITCH_FILE)


def check_kill_switch():
    """Raise SystemExit if kill switch is active."""
    if kill_switch_active():
        log("!!! KILL SWITCH ACTIVE — halting all trading activity !!!")
        log(f"    Remove '{KILL_SWITCH_FILE}' file and restart to resume.")
        sys.exit(0)


# =========================================================
# DATABASE MANAGER
# =========================================================

class DBManager:

    def __init__(self, db_path):
        self.db_path = db_path

    def conn(self):
        c = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def setup(self):
        with self.conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS trade_audit (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT,
                    symbol        TEXT,
                    action        TEXT,
                    side          TEXT,
                    price         REAL,
                    filled_price  REAL,
                    quantity      INTEGER,
                    filled_qty    INTEGER,
                    order_id      TEXT,
                    sl_order_id   TEXT,
                    sl_price      REAL,
                    target        REAL,
                    status        TEXT,
                    notes         TEXT
                );

                CREATE TABLE IF NOT EXISTS tsl_state (
                    symbol        TEXT PRIMARY KEY,
                    sl            REAL,
                    sl_order_id   TEXT,
                    updated_at    TEXT
                );

                CREATE TABLE IF NOT EXISTS tsl_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol    TEXT,
                    ltp       REAL,
                    old_sl    REAL,
                    new_sl    REAL,
                    notes     TEXT
                );

                CREATE TABLE IF NOT EXISTS position_exits (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT,
                    symbol      TEXT,
                    side        TEXT,
                    qty         INTEGER,
                    avg_price   REAL,
                    last_ltp    REAL,
                    reason      TEXT
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp  TEXT,
                    symbol     TEXT,
                    decision   TEXT,
                    confidence REAL,
                    reason     TEXT
                );

                CREATE TABLE IF NOT EXISTS cautionary_symbols (
                    symbol     TEXT PRIMARY KEY,
                    reason     TEXT,
                    flagged_at TEXT,
                    attempts   INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS failure_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol    TEXT,
                    failure   TEXT,
                    detail    TEXT,
                    resolved  INTEGER DEFAULT 0
                );
            """)
            # safe migrations for existing DBs
            for sql in [
                "ALTER TABLE cautionary_symbols ADD COLUMN attempts INTEGER DEFAULT 1",
                "ALTER TABLE trade_audit ADD COLUMN filled_price REAL",
                "ALTER TABLE trade_audit ADD COLUMN filled_qty INTEGER",
                "ALTER TABLE trade_audit ADD COLUMN sl_order_id TEXT",
                "ALTER TABLE trade_audit ADD COLUMN sl_price REAL",
                "ALTER TABLE trade_audit ADD COLUMN target REAL",
            ]:
                try:
                    c.execute(sql)
                except Exception:
                    pass
            c.commit()

    # ── Signals ──────────────────────────────────────────────

    def get_latest_signals(self) -> pd.DataFrame:
        """One row per symbol, latest run_date, deduped via MAX(rowid)."""
        with self.conn() as c:
            try:
                latest = c.execute("""
                    SELECT date(run_date) FROM signals
                    WHERE  final_decision IN ('BUY','SELL')
                    ORDER  BY run_date DESC LIMIT 1
                """).fetchone()
                if not latest:
                    return pd.DataFrame()
                signal_date = latest[0]
                log(f"Using signals from {signal_date}")
                df = pd.read_sql("""
                    SELECT s.symbol, s.final_decision, s.avg_confidence,
                           s.supervisor_conf, s.signal_price,
                           s.timeframe, s.reasoning
                    FROM   signals s
                    INNER JOIN (
                        SELECT symbol, MAX(rowid) AS max_rid
                        FROM   signals
                        WHERE  date(run_date) = ?
                          AND  final_decision IN ('BUY','SELL')
                        GROUP  BY symbol
                    ) d ON s.symbol = d.symbol AND s.rowid = d.max_rid
                    ORDER  BY s.supervisor_conf DESC
                """, c, params=[signal_date])
                log(f"Loaded {len(df)} unique signals")
                return df
            except Exception as e:
                log(f"Signal read error: {e}")
                return pd.DataFrame()

    # ── Trade audit ──────────────────────────────────────────

    def log_trade(self, symbol, action, side, price, filled_price,
                  quantity, filled_qty, order_id,
                  sl_order_id=None, sl_price=None,
                  target=None, status="PLACED", notes=""):
        with self.conn() as c:
            c.execute("""
                INSERT INTO trade_audit
                    (timestamp,symbol,action,side,price,filled_price,
                     quantity,filled_qty,order_id,sl_order_id,
                     sl_price,target,status,notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"),
                  symbol, action, side, price, filled_price,
                  quantity, filled_qty, order_id, sl_order_id,
                  sl_price, target, status, notes])
            c.commit()

    def update_sl_order_id(self, symbol, sl_order_id, sl_price):
        with self.conn() as c:
            c.execute("""
                UPDATE trade_audit SET sl_order_id=?, sl_price=?
                WHERE  symbol=? AND action='ENTRY'
                  AND  date(timestamp)=date('now')
            """, [sl_order_id, sl_price, symbol])
            c.commit()

    def get_sl_order_id(self, symbol):
        with self.conn() as c:
            r = c.execute("""
                SELECT sl_order_id FROM trade_audit
                WHERE  symbol=? AND action='ENTRY'
                  AND  date(timestamp)=date('now')
                ORDER  BY timestamp DESC LIMIT 1
            """, [symbol]).fetchone()
            return r[0] if r else None

    # ── Position exit audit  (FIX 8) ─────────────────────────

    def log_position_exit(self, symbol, side, qty, avg_price, last_ltp, reason):
        with self.conn() as c:
            c.execute("""
                INSERT INTO position_exits
                    (timestamp,symbol,side,qty,avg_price,last_ltp,reason)
                VALUES (?,?,?,?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"),
                  symbol, side, qty, avg_price, last_ltp, reason])
            c.commit()

    # ── TSL state ────────────────────────────────────────────

    def save_tsl_state(self, symbol, sl, sl_order_id=None):
        with self.conn() as c:
            c.execute("""
                INSERT INTO tsl_state (symbol,sl,sl_order_id,updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE
                    SET sl=excluded.sl,
                        sl_order_id=excluded.sl_order_id,
                        updated_at=excluded.updated_at
            """, [symbol, sl, sl_order_id,
                  now().strftime("%Y-%m-%d %H:%M:%S")])
            c.commit()

    def load_tsl_state(self) -> dict:
        with self.conn() as c:
            try:
                rows = c.execute("""
                    SELECT symbol,sl,sl_order_id FROM tsl_state
                    WHERE  date(updated_at) >= date('now','-4 days')
                """).fetchall()
                return {r[0]: {"sl": r[1], "sl_order_id": r[2]} for r in rows}
            except Exception:
                return {}

    def delete_tsl_state(self, symbol):
        with self.conn() as c:
            c.execute("DELETE FROM tsl_state WHERE symbol=?", [symbol])
            c.commit()

    def log_tsl(self, symbol, ltp, old_sl, new_sl, notes="TRAIL"):
        with self.conn() as c:
            c.execute("""
                INSERT INTO tsl_log (timestamp,symbol,ltp,old_sl,new_sl,notes)
                VALUES (?,?,?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"),
                  symbol, ltp, old_sl, new_sl, notes])
            c.commit()

    # ── Watchlist ────────────────────────────────────────────

    def add_watchlist(self, symbol, decision, conf, reason):
        with self.conn() as c:
            c.execute("""
                INSERT INTO watchlist (timestamp,symbol,decision,confidence,reason)
                VALUES (?,?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"),
                  symbol, decision, conf, reason])
            c.commit()

    # ── Cautionary ───────────────────────────────────────────

    def mark_cautionary(self, symbol, reason):
        with self.conn() as c:
            c.execute("""
                INSERT INTO cautionary_symbols (symbol,reason,flagged_at,attempts)
                VALUES (?,?,?,1)
                ON CONFLICT(symbol) DO UPDATE
                    SET attempts=attempts+1,
                        reason=excluded.reason,
                        flagged_at=excluded.flagged_at
            """, [symbol, reason, now().strftime("%Y-%m-%d %H:%M:%S")])
            c.commit()

    def is_cautionary(self, symbol) -> bool:
        with self.conn() as c:
            r = c.execute(
                "SELECT attempts FROM cautionary_symbols WHERE symbol=?",
                [symbol]).fetchone()
            return (r[0] if r else 0) >= MAX_CAUTIONARY_RETRIES

    # ── Failure log ──────────────────────────────────────────

    def log_failure(self, symbol, failure_type, detail=""):
        with self.conn() as c:
            c.execute("""
                INSERT INTO failure_log (timestamp,symbol,failure,detail)
                VALUES (?,?,?,?)
            """, [now().strftime("%Y-%m-%d %H:%M:%S"),
                  symbol, failure_type, detail])
            c.commit()

    def resolve_failure(self, symbol, failure_type):
        with self.conn() as c:
            c.execute("""
                UPDATE failure_log SET resolved=1
                WHERE  symbol=? AND failure=? AND resolved=0
            """, [symbol, failure_type])
            c.commit()

    def get_unresolved_failures(self) -> list:
        with self.conn() as c:
            return c.execute("""
                SELECT symbol,failure,detail,timestamp
                FROM   failure_log WHERE resolved=0
                ORDER  BY timestamp DESC
            """).fetchall()


# =========================================================
# RISK MANAGER
# =========================================================

class RiskManager:

    def calc_entry(self, signal_price, side):
        offset = ENTRY_OFFSET_PCT / 100
        raw    = signal_price * (1 + offset) if side == "BUY" \
                 else signal_price * (1 - offset)
        return round_to_tick(raw)

    def calc_sl(self, price, side):
        raw = price * (1 - MAX_RISK_PCT) if side == "BUY" \
              else price * (1 + MAX_RISK_PCT)
        return round_to_tick(raw)

    def calc_target(self, price, side):
        raw = price * (1 + MAX_RISK_PCT * RR_RATIO) if side == "BUY" \
              else price * (1 - MAX_RISK_PCT * RR_RATIO)
        return round_to_tick(raw)

    def calc_qty(self, price, available_capital=None) -> int:
        cap   = available_capital if available_capital else TOTAL_CAPITAL
        alloc = min(TOTAL_CAPITAL * POSITION_SIZE_PCT, cap)
        qty   = max(int(alloc / price), 1)
        # FIX 6: cap to NSE freeze quantity
        freeze = get_freeze_qty(price)   # price used as proxy; pass symbol if known
        return min(qty, freeze)

    def calc_qty_for_symbol(self, symbol, price, available_capital=None) -> int:
        cap   = available_capital if available_capital else TOTAL_CAPITAL
        alloc = min(TOTAL_CAPITAL * POSITION_SIZE_PCT, cap)
        qty   = max(int(alloc / price), 1)
        freeze = get_freeze_qty(symbol)
        if qty > freeze:
            log(f"  ! {symbol}: qty={qty} capped to freeze_qty={freeze}")
            qty = freeze
        return qty


# =========================================================
# CAPITAL TRACKER
# =========================================================

class CapitalTracker:
    """
    Per-order reservation tracking.
    sync_from_broker() updates deployed but does NOT clear reservations.
    Reservations cleared only on fill confirmation or explicit release.
    """

    def __init__(self):
        self.deployed      = 0.0
        self._reservations = {}   # order_id -> amount

    def sync_from_broker(self, positions: dict):
        self.deployed = sum(
            p["avg_price"] * p["qty"] for p in positions.values()
        )

    def reserve(self, order_id: str, amount: float):
        self._reservations[order_id] = amount

    def confirm_fill(self, order_id: str, actual_price: float, actual_qty: int):
        self._reservations.pop(order_id, None)

    def release(self, order_id: str):
        self._reservations.pop(order_id, None)

    def release_all(self):
        self._reservations.clear()

    @property
    def reserved(self) -> float:
        return sum(self._reservations.values())

    @property
    def available(self) -> float:
        return max(0.0, TOTAL_CAPITAL - self.deployed - self.reserved)

    def can_trade(self, alloc: float) -> bool:
        return self.available >= alloc

    def summary(self) -> str:
        return (f"Capital: Rs{TOTAL_CAPITAL:,.0f} | "
                f"Deployed: Rs{self.deployed:,.0f} | "
                f"Reserved: Rs{self.reserved:,.0f} | "
                f"Available: Rs{self.available:,.0f}")


# =========================================================
# EXECUTION ENGINE
# =========================================================

class ExecutionEngine:

    def __init__(self, client, tokens, sl_placer, db,
                 broker_state, risk, capital):
        self.client       = client
        self.tokens       = tokens
        self.sl_placer    = sl_placer
        self.db           = db
        self.broker_state = broker_state
        self.risk         = risk
        self.capital      = capital

        self.placed_this_session = set()
        self.cautionary_this_run = set()
        self.failed_cooldown     = {}

    def _in_cooldown(self, symbol) -> bool:
        ts = self.failed_cooldown.get(symbol)
        return ts is not None and \
               (now() - ts).total_seconds() / 60 < FAILURE_COOLDOWN_MINS

    def _mark_failed(self, symbol, reason=""):
        self.failed_cooldown[symbol] = now()
        self.db.log_failure(symbol, "ORDER_FAILED", reason)

    def place_entries(self):
        check_kill_switch()   # FIX 7: abort immediately if kill switch active

        signals = self.db.get_latest_signals()
        if signals.empty:
            log("No signals found")
            return

        self.capital.release_all()
        placed = skipped = watchlist = 0

        for _, row in signals.iterrows():
            check_kill_switch()   # FIX 7: check between each order

            try:
                symbol       = str(row["symbol"]).strip()
                side         = str(row["final_decision"]).strip()
                conf         = safe_float(
                    row.get("supervisor_conf") or row.get("avg_confidence"))
                signal_price = safe_float(row.get("signal_price"))

                # ── filters
                if conf < MIN_CONFIDENCE:
                    if conf >= 0.70:
                        self.db.add_watchlist(symbol, side, conf, "Medium conf")
                        watchlist += 1
                    else:
                        skipped += 1
                    continue

                if symbol in self.cautionary_this_run or \
                        self.db.is_cautionary(symbol):
                    log(f"SKIP {symbol}: cautionary")
                    skipped += 1
                    continue

                if self._in_cooldown(symbol):
                    log(f"SKIP {symbol}: cooldown")
                    skipped += 1
                    continue

                if symbol in self.placed_this_session or \
                        self.broker_state.has_position(symbol):
                    skipped += 1
                    continue

                if self.broker_state.position_count() + placed >= MAX_TRADES:
                    log(f"Max trades ({MAX_TRADES}) reached")
                    break

                if signal_price <= 0:
                    skipped += 1
                    continue

                try:
                    ltp = self.tokens.ltp(symbol)
                except Exception as e:
                    log(f"SKIP {symbol}: LTP failed — {e}")
                    skipped += 1
                    continue

                gap_pct = ((ltp - signal_price) / signal_price * 100) \
                          if side == "BUY" \
                          else ((signal_price - ltp) / signal_price * 100)

                if abs(gap_pct) > MAX_GAP_PCT:
                    log(f"SKIP {symbol} gap={gap_pct:+.1f}%")
                    skipped += 1
                    continue

                entry = self.risk.calc_entry(signal_price, side)
                # FIX 6: use calc_qty_for_symbol to apply freeze qty
                qty   = self.risk.calc_qty_for_symbol(
                    symbol, entry, self.capital.available)
                alloc = entry * qty

                if not self.capital.can_trade(alloc):
                    log(f"SKIP {symbol}: insufficient capital "
                        f"(need Rs{alloc:,.0f} have Rs{self.capital.available:,.0f})")
                    skipped += 1
                    continue

                log(f"\n{symbol} {side}"
                    f"\n  signal=Rs{signal_price:.2f} ltp=Rs{ltp:.2f} gap={gap_pct:+.1f}%"
                    f"\n  entry=Rs{entry:.2f} qty={qty} alloc=Rs{alloc:,.0f}"
                    f"\n  {self.capital.summary()}")

                # ── place entry order
                if DRY_RUN:
                    order_id = f"DRY_{symbol}_{now().strftime('%H%M%S')}"
                    self.capital.reserve(order_id, alloc)
                    fill = FillResult(True, qty, entry, order_id)
                else:
                    result = self.client.angel_obj.placeOrder({
                        "variety":         "NORMAL",
                        "tradingsymbol":   f"{symbol}-EQ",
                        "symboltoken":     self.tokens.get(symbol),
                        "transactiontype": side,
                        "exchange":        "NSE",
                        "ordertype":       "LIMIT",
                        "producttype":     PRODUCT_TYPE,
                        "duration":        "DAY",
                        "price":           entry,
                        "quantity":        qty,
                    })
                    if isinstance(result, dict):
                        success  = result.get("success", result.get("status", False))
                        order_id = result.get("data", {}).get("orderid") \
                                   if success else None
                        err_code = result.get("errorCode",
                                              result.get("errorcode", ""))
                        if not success:
                            if err_code == "AB4036":
                                self.cautionary_this_run.add(symbol)
                                self.db.mark_cautionary(symbol, "AB4036")
                                log(f"CAUTIONARY {symbol}")
                            else:
                                self._mark_failed(symbol, str(result))
                                log(f"FAILED {symbol}: {result.get('message','?')}")
                            skipped += 1
                            continue
                    elif isinstance(result, str) and result:
                        order_id = result
                    else:
                        self._mark_failed(symbol, "empty response")
                        skipped += 1
                        continue

                    self.capital.reserve(order_id, alloc)
                    fill = self.sl_placer.wait_for_fill(
                        self.broker_state, symbol, order_id)

                if not fill:
                    self.capital.release(order_id)
                    self.db.log_failure(symbol, "FILL_UNCONFIRMED",
                                        f"order_id={order_id}")
                    log(f"  ! {symbol}: not filled after retries")
                    self.placed_this_session.add(symbol)
                    skipped += 1
                    continue

                actual_price = fill.avg_price
                actual_qty   = fill.qty

                if actual_qty < qty:
                    log(f"  ! Partial fill: {actual_qty}/{qty} shares filled")

                # FIX 2: SL and target from actual fill price
                sl     = self.risk.calc_sl(actual_price, side)
                target = self.risk.calc_target(actual_price, side)

                self.capital.confirm_fill(order_id, actual_price, actual_qty)

                # FIX 5: inject with actual data, then verify from broker
                self.broker_state.inject_position(
                    symbol, side, actual_price, actual_qty)
                self.broker_state.sync_position_from_broker(symbol)

                # Place SL for actual filled qty
                sl_order_id = self.sl_placer.place(
                    symbol, side, sl, actual_qty)
                if not sl_order_id:
                    self.db.log_failure(symbol, "SL_PLACEMENT_FAILED",
                                        f"entry={order_id} sl={sl}")

                self.placed_this_session.add(symbol)

                self.db.log_trade(
                    symbol=symbol, action="ENTRY", side=side,
                    price=entry, filled_price=actual_price,
                    quantity=qty, filled_qty=actual_qty,
                    order_id=order_id,
                    sl_order_id=sl_order_id,
                    sl_price=sl, target=target,
                    status="PARTIAL_FILL" if actual_qty < qty else "FILLED",
                    notes=f"signal={signal_price} gap={gap_pct:+.1f}%"
                )
                placed += 1
                log(f"  OK {symbol} entry={order_id} sl={sl_order_id} @ Rs{sl:.2f}")
                time.sleep(0.5)

            except Exception as e:
                log(f"Entry error ({row.get('symbol','?')}): {e}")
                traceback.print_exc()

        log(f"\nEntries: {placed} placed | {skipped} skipped | {watchlist} watchlisted")
        log(self.capital.summary())


# =========================================================
# TSL ENGINE
# =========================================================

class TSLEngine:

    def __init__(self, client, tokens, sl_placer, db, broker_state, risk):
        self.client       = client
        self.tokens       = tokens
        self.sl_placer    = sl_placer
        self.db           = db
        self.broker_state = broker_state
        self.risk         = risk
        self.tsl_state    = self.db.load_tsl_state()
        if self.tsl_state:
            log(f"  [TSL] Restored: {list(self.tsl_state.keys())}")

    def restore_overnight_sl_orders(self):
        """
        Called ONCE at 9:15 AM before market opens.

        Problem: All SL orders placed yesterday were DAY orders.
        They expired at 3:30 PM. By 9:15 AM today, the broker has
        NO active SL orders for carried positions even though:
          - Position exists at broker (delivery, held overnight)
          - TSL state exists in DB with correct trailed SL level

        This method re-places SL orders using the persisted TSL SL level,
        then updates tsl_state with the new order IDs.
        """
        positions = self.broker_state.get_positions()
        if not positions:
            log("  [TSL] No carried positions — nothing to restore")
            return

        carried = {s: p for s, p in positions.items()
                   if s in self.tsl_state}

        if not carried:
            log("  [TSL] No overnight TSL state to restore")
            return

        log(f"  [TSL] Restoring SL orders for {len(carried)} carried position(s)")

        for symbol, pos in carried.items():
            side  = pos["side"]
            qty   = pos["qty"]
            state = self.tsl_state[symbol]
            sl    = safe_float(state.get("sl"))

            # Fallback: recalculate from avg_price if DB value is missing
            if sl <= 0:
                sl = self.risk.calc_sl(pos["avg_price"], side)
                log(f"  {symbol}: SL missing in DB — recalculated Rs{sl:.2f}")

            log(f"  {symbol}: re-placing SL @ Rs{sl:.2f} "
                f"(trailed level from yesterday) qty={qty}")

            if DRY_RUN:
                new_oid = f"DRYSL_{symbol}_{now().strftime('%H%M%S')}"
                log(f"  [DRY] SL restored: {new_oid}")
            else:
                new_oid = self.sl_placer.place(
                    symbol, side, sl, qty,
                    broker_state=self.broker_state)

            if new_oid:
                self.tsl_state[symbol]["sl_order_id"] = new_oid
                self.db.save_tsl_state(symbol, sl, new_oid)
                self.db.update_sl_order_id(symbol, new_oid, sl)
                log(f"  {symbol}: SL restored — order_id={new_oid} @ Rs{sl:.2f}")
            else:
                log(f"  ! {symbol}: SL restore FAILED — "
                    f"reconcile will retry in {RECONCILE_INTERVAL_MINS}m")
                self.db.log_failure(symbol, "SL_RESTORE_FAILED",
                                    f"sl={sl} qty={qty}")

    def process(self):
        check_kill_switch()   # FIX 7

        positions = self.broker_state.get_positions()

        # Cleanup stale TSL state — FIX 8: also log exits to DB
        closed = set(self.tsl_state.keys()) - set(positions.keys())
        for symbol in closed:
            log(f"  [TSL] {symbol}: position gone — cleaning state")
            state = self.tsl_state.pop(symbol, {})
            self.db.delete_tsl_state(symbol)
            self.db.resolve_failure(symbol, "SL_PLACEMENT_FAILED")
            # FIX 8: position exit is already logged by BrokerState._refresh_positions
            # TSL just needs to clean up its own state

        for symbol, pos in positions.items():
            try:
                self._update(symbol, pos)
            except Exception as e:
                log(f"TSL error {symbol}: {e}")
                traceback.print_exc()

    def _update(self, symbol, pos):
        ltp       = self.tokens.ltp(symbol)
        side      = pos["side"]
        avg_price = pos["avg_price"]

        state  = self.tsl_state.get(symbol, {})
        old_sl = state.get("sl", self.risk.calc_sl(avg_price, side))

        new_sl = round_to_tick(ltp * (1 - MAX_RISK_PCT)) if side == "BUY" \
                 else round_to_tick(ltp * (1 + MAX_RISK_PCT))

        # First time: set baseline
        if symbol not in self.tsl_state:
            baseline = self.risk.calc_sl(avg_price, side)
            log(f"  {symbol}: TSL baseline SL=Rs{baseline:.2f} "
                f"avg=Rs{avg_price:.2f} LTP=Rs{ltp:.2f}")
            self.tsl_state[symbol] = {
                "sl":          baseline,
                "sl_order_id": self.db.get_sl_order_id(symbol),
            }
            self.db.save_tsl_state(symbol, baseline,
                                   self.db.get_sl_order_id(symbol))
            return

        should_trail = (side == "BUY"  and new_sl > old_sl) or \
                       (side == "SELL" and new_sl < old_sl)

        if not should_trail:
            log(f"  {symbol}: LTP=Rs{ltp:.2f} SL=Rs{old_sl:.2f} — no trail")
            return

        log(f"  ^ TSL {symbol}: Rs{old_sl:.2f} -> Rs{new_sl:.2f} LTP=Rs{ltp:.2f}")
        self.db.log_tsl(symbol, ltp, old_sl, new_sl)

        old_sl_order_id = state.get("sl_order_id")

        if DRY_RUN:
            self.tsl_state[symbol] = {"sl": new_sl, "sl_order_id": old_sl_order_id}
            self.db.save_tsl_state(symbol, new_sl, old_sl_order_id)
            return

        # FIX 1: mark replace pending to block reconcile during mid-replace
        self.broker_state.mark_sl_replace_pending(symbol)

        try:
            # FIX B: place new SL BEFORE cancelling old (no naked window)
            new_sl_order_id = self.sl_placer.place(
                symbol, side, new_sl, pos["qty"])

            if not new_sl_order_id:
                log(f"  ! {symbol}: new SL failed — old SL unchanged Rs{old_sl:.2f}")
                self.db.log_failure(symbol, "TSL_REPLACE_FAILED",
                                    f"old={old_sl} attempted_new={new_sl}")
                return   # old SL still active, no state change

            # FIX 4: refresh orderbook after placing new SL
            self.broker_state.force_orderbook_refresh()

            # Cancel old SL
            cancelled = self.sl_placer.cancel(old_sl_order_id)

            # FIX 4: refresh again after cancel to confirm
            self.broker_state.force_orderbook_refresh()

            if not cancelled:
                log(f"  ! {symbol}: old SL cancel failed — manual check needed "
                    f"(old={old_sl_order_id} new={new_sl_order_id})")
                self.db.log_failure(symbol, "TSL_CANCEL_FAILED",
                                    f"old={old_sl_order_id} new={new_sl_order_id}")

            # Always update state to new SL regardless of cancel outcome
            self.tsl_state[symbol] = {"sl": new_sl, "sl_order_id": new_sl_order_id}
            self.db.save_tsl_state(symbol, new_sl, new_sl_order_id)
            self.db.update_sl_order_id(symbol, new_sl_order_id, new_sl)
            if cancelled:
                self.db.resolve_failure(symbol, "TSL_CANCEL_FAILED")

        finally:
            # FIX 1: always clear pending flag even if exception occurred
            self.broker_state.clear_sl_replace_pending(symbol)


# =========================================================
# RECONCILIATION ENGINE
# =========================================================

class ReconciliationEngine:

    def __init__(self, client, tokens, sl_placer, db,
                 broker_state, risk, tsl_engine):
        self.client       = client
        self.tokens       = tokens
        self.sl_placer    = sl_placer
        self.db           = db
        self.broker_state = broker_state
        self.risk         = risk
        self.tsl_engine   = tsl_engine

    def reconcile(self):
        check_kill_switch()   # FIX 7

        # FIX 4: always force fresh orderbook before reconciling
        self.broker_state.force_orderbook_refresh()

        positions            = self.broker_state.get_positions()
        sl_placed_this_cycle = set()

        log(f"Reconcile | positions={len(positions)} "
            f"orderbook={len(self.broker_state.orderbook_cache)}")

        for symbol, pos in positions.items():

            # FIX 1: skip if TSL is mid-replace for this symbol
            if self.broker_state.is_sl_replace_pending(symbol):
                log(f"  {symbol}: SL replace in progress — skipping reconcile")
                continue

            side    = pos["side"]
            sl_side = "SELL" if side == "BUY" else "BUY"

            # FIX 3: sum ALL active SL orders for this symbol
            active_sl_qty = self.broker_state.get_active_sl_qty_total(
                symbol, sl_side)
            broker_qty    = pos["qty"]

            if active_sl_qty >= broker_qty:
                continue   # fully covered

            # FIX 2+3: compute exactly how much is uncovered
            uncovered_qty = broker_qty - active_sl_qty

            if symbol in sl_placed_this_cycle:
                log(f"  {symbol}: SL already placed this cycle — skipping")
                continue

            if active_sl_qty == 0:
                log(f"  ! {symbol} ({side}): no SL — placing for qty={uncovered_qty}")
            else:
                log(f"  ! {symbol} ({side}): SL covers {active_sl_qty}/{broker_qty} "
                    f"— placing for uncovered qty={uncovered_qty}")

            # FIX 4: use TSL level if available, else original SL from avg_price
            tsl_state = self.tsl_engine.tsl_state.get(symbol, {})
            if tsl_state.get("sl") and tsl_state["sl"] > 0:
                sl = safe_float(tsl_state["sl"])
                log(f"    Using TSL level: Rs{sl:.2f}")
            else:
                sl = self.risk.calc_sl(pos["avg_price"], side)
                log(f"    Using original SL from avg_price: Rs{sl:.2f}")

            new_oid = self.sl_placer.place(symbol, side, sl, uncovered_qty)

            if new_oid:
                sl_placed_this_cycle.add(symbol)
                self.db.update_sl_order_id(symbol, new_oid, sl)
                self.db.resolve_failure(symbol, "SL_PLACEMENT_FAILED")
                # FIX 4: refresh after placing
                self.broker_state.force_orderbook_refresh()
                log(f"    Auto-SL: {new_oid} @ Rs{sl:.2f} qty={uncovered_qty}")
            else:
                self.db.log_failure(symbol, "SL_PLACEMENT_FAILED",
                                    f"sl={sl} qty={uncovered_qty}")

        # Report unresolved failures
        failures = self.db.get_unresolved_failures()
        if failures:
            log(f"  Unresolved failures: {len(failures)}")
            for f in failures[:5]:
                log(f"    {f[0]}: {f[1]} — {f[2]}")


# =========================================================
# POSITION EXIT HANDLER  (FIX 8)
# =========================================================

class PositionExitHandler:
    """
    FIX 8: Detects positions that closed between heartbeats and logs them.
    Works by comparing previous and current broker positions.
    """

    def __init__(self, db, broker_state):
        self.db           = db
        self.broker_state = broker_state
        self._prev_positions = {}

    def detect_and_log_exits(self):
        """Call after broker_state.refresh() to detect new exits."""
        current   = self.broker_state.get_positions()
        prev_syms = set(self._prev_positions.keys())
        curr_syms = set(current.keys())
        exited    = prev_syms - curr_syms

        for symbol in exited:
            pos    = self._prev_positions[symbol]
            reason = self._infer_exit_reason(symbol, pos)
            log(f"  [EXIT] {symbol} {pos['side']} qty={pos['qty']} "
                f"avg=Rs{pos['avg_price']:.2f} last_ltp=Rs{pos['ltp']:.2f} "
                f"reason={reason}")
            self.db.log_position_exit(
                symbol   = symbol,
                side     = pos["side"],
                qty      = pos["qty"],
                avg_price= pos["avg_price"],
                last_ltp = pos["ltp"],
                reason   = reason,
            )

        self._prev_positions = dict(current)

    def _infer_exit_reason(self, symbol, pos) -> str:
        """Best-effort reason inference from last known state."""
        ltp       = pos["ltp"]
        avg_price = pos["avg_price"]
        side      = pos["side"]
        pnl_pct   = ((ltp - avg_price) / avg_price * 100) if side == "BUY" \
                    else ((avg_price - ltp) / avg_price * 100)

        if pnl_pct <= -(MAX_RISK_PCT * 100 * 1.1):
            return "SL_HIT"
        if pnl_pct >= (MAX_RISK_PCT * RR_RATIO * 100 * 0.9):
            return "TARGET_HIT"
        return "MANUAL_OR_UNKNOWN"


# =========================================================
# REPORTING ENGINE
# =========================================================

class ReportingEngine:

    def __init__(self, db, broker_state, capital):
        self.db           = db
        self.broker_state = broker_state
        self.capital      = capital

    def print_live_snapshot(self):
        positions = self.broker_state.get_positions()
        if not positions:
            return
        log(f"\n  -- P&L Snapshot {now().strftime('%H:%M')} --")
        total = 0.0
        for symbol, p in positions.items():
            pnl    = safe_float(p["pnl"])
            total += pnl
            log(f"  {symbol:<15} {p['side']:<5} qty={p['qty']:<5} "
                f"LTP=Rs{p['ltp']:<10.2f} avg=Rs{p['avg_price']:<10.2f} "
                f"PnL=Rs{pnl:+.2f}")
        log(f"  TOTAL: Rs{total:+.2f} | {self.capital.summary()}\n")

    def eod(self):
        log("\n" + "=" * 60)
        log("EOD REPORT")
        log("=" * 60)
        self.print_live_snapshot()

        with self.db.conn() as c:
            try:
                df = pd.read_sql("""
                    SELECT symbol,action,side,
                           price,filled_price,quantity,filled_qty,
                           order_id,sl_order_id,sl_price,target,
                           status,notes,timestamp
                    FROM   trade_audit
                    WHERE  date(timestamp) = date('now')
                    ORDER  BY timestamp
                """, c)
                if not df.empty:
                    log(f"\nToday's trades ({len(df)}):")
                    for _, row in df.iterrows():
                        fp = safe_float(row.get("filled_price"))
                        fq = safe_int(row.get("filled_qty"))
                        log(f"  {row['timestamp'][11:19]} {row['symbol']:<12} "
                            f"{row['action']:<6} {row['side']:<5} "
                            f"ord=Rs{row['price']:.2f}x{row['quantity']} "
                            f"fill=Rs{fp:.2f}x{fq} "
                            f"sl={row['sl_order_id']} [{row['status']}]")
            except Exception as e:
                log(f"Trade audit error: {e}")

        # FIX 8: show exits
        with self.db.conn() as c:
            try:
                exits = pd.read_sql("""
                    SELECT symbol,side,qty,avg_price,last_ltp,reason,timestamp
                    FROM   position_exits
                    WHERE  date(timestamp) >= date('now','-7 days')
                    ORDER  BY timestamp DESC
                """, c)
                if not exits.empty:
                    log(f"\nRecent exits ({len(exits)}):")
                    for _, row in exits.iterrows():
                        pnl_est = ((row['last_ltp'] - row['avg_price']) * row['qty']) \
                                  if row['side'] == 'BUY' \
                                  else ((row['avg_price'] - row['last_ltp']) * row['qty'])
                        log(f"  {row['timestamp'][11:19]} {row['symbol']:<12} "
                            f"{row['side']:<5} qty={row['qty']} "
                            f"avg=Rs{row['avg_price']:.2f} ltp=Rs{row['last_ltp']:.2f} "
                            f"~PnL=Rs{pnl_est:+.0f} [{row['reason']}]")
            except Exception as e:
                log(f"Exit report error: {e}")

        failures = self.db.get_unresolved_failures()
        if failures:
            log(f"\nUnresolved failures ({len(failures)}):")
            for f in failures:
                log(f"  {f[0]}: {f[1]} — {f[2]}")

        with self.db.conn() as c:
            try:
                rows = c.execute("""
                    SELECT symbol,attempts,reason FROM cautionary_symbols
                    ORDER BY flagged_at DESC LIMIT 20
                """).fetchall()
                if rows:
                    log(f"\nCautionary ({len(rows)}):")
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
        # FIX 7: check kill switch before initialising anything
        check_kill_switch()

        self.db           = DBManager(DB_PATH)
        self.db.setup()

        self.client       = hist_data()
        self.client.log_in()

        self.tokens       = TokenCache(self.client)
        self.sl_placer    = SLPlacer(self.client, self.tokens)
        self.broker_state = BrokerState(self.client)
        self.risk         = RiskManager()
        self.capital      = CapitalTracker()

        self.execution    = ExecutionEngine(
            self.client, self.tokens, self.sl_placer,
            self.db, self.broker_state, self.risk, self.capital)

        self.tsl          = TSLEngine(
            self.client, self.tokens, self.sl_placer,
            self.db, self.broker_state, self.risk)

        self.reconciler   = ReconciliationEngine(
            self.client, self.tokens, self.sl_placer,
            self.db, self.broker_state, self.risk, self.tsl)

        self.reporter     = ReportingEngine(
            self.db, self.broker_state, self.capital)

        # FIX 8: exit handler
        self.exit_handler = PositionExitHandler(self.db, self.broker_state)

        self._last_tsl_run       = None
        self._last_reconcile_run = None

        log("=" * 60)
        log("ORDER MANAGER v8")
        log(f"Capital: Rs{TOTAL_CAPITAL:,.0f} | Max: {MAX_TRADES} | "
            f"Size: {POSITION_SIZE_PCT*100:.0f}%")
        log(f"SL: {MAX_RISK_PCT*100:.1f}% | "
            f"Target: {MAX_RISK_PCT*RR_RATIO*100:.1f}% (1:{RR_RATIO:.0f}) | "
            f"Trigger: {SL_TRIGGER_PCT:.1%}")
        log(f"Min conf: {MIN_CONFIDENCE} | Max gap: {MAX_GAP_PCT:.1f}% | "
            f"Product: {PRODUCT_TYPE} | DRY_RUN: {DRY_RUN}")
        log(f"Kill switch file: '{KILL_SWITCH_FILE}'")
        log(f"Heartbeat={HEARTBEAT_SECS}s TSL={TSL_INTERVAL_MINS}m "
            f"Reconcile={RECONCILE_INTERVAL_MINS}m")
        log("=" * 60)

    def run(self):
        if now().weekday() >= 5:
            log(f"{'Saturday' if now().weekday()==5 else 'Sunday'} — NSE closed")
            return

        log(f"\nWaiting for {MARKET_OPEN_TSL[0]:02d}:{MARKET_OPEN_TSL[1]:02d} — TSL sync")
        wait_until(*MARKET_OPEN_TSL)
        self._sync()
        # Re-place expired DAY SL orders for overnight carried positions
        self.tsl.restore_overnight_sl_orders()
        self._run_tsl()

        log(f"\nWaiting for {MARKET_OPEN_ENTRY[0]:02d}:{MARKET_OPEN_ENTRY[1]:02d} — entries")
        wait_until(*MARKET_OPEN_ENTRY)
        self._sync()
        self.execution.place_entries()

        close_time = now().replace(
            hour=MARKET_CLOSE_EOD[0], minute=MARKET_CLOSE_EOD[1], second=0)

        while now() < close_time:
            try:
                check_kill_switch()   # FIX 7: every heartbeat
                self._sync()
                self._run_tsl_if_due()
                self._run_reconcile_if_due()
                self.reporter.print_live_snapshot()
                time.sleep(HEARTBEAT_SECS)
            except SystemExit:
                raise   # let kill switch propagate
            except KeyboardInterrupt:
                log("KeyboardInterrupt — exiting loop")
                break
            except Exception as e:
                log(f"Main loop error: {e}")
                traceback.print_exc()
                time.sleep(10)

        self.reporter.eod()
        log(f"\nDone. Waiting for {MARKET_EXIT[0]:02d}:{MARKET_EXIT[1]:02d}")
        wait_until(*MARKET_EXIT)

    def _sync(self):
        self.broker_state.refresh()
        # FIX 8: detect exits after every refresh
        self.exit_handler.detect_and_log_exits()
        self.capital.sync_from_broker(self.broker_state.get_positions())

    def _run_tsl(self):
        self.tsl.process()
        self._last_tsl_run = now()

    def _run_tsl_if_due(self):
        if self._last_tsl_run is None or \
           (now() - self._last_tsl_run).total_seconds() / 60 >= TSL_INTERVAL_MINS:
            self._run_tsl()

    def _run_reconcile_if_due(self):
        if self._last_reconcile_run is None or \
           (now() - self._last_reconcile_run).total_seconds() / 60 \
               >= RECONCILE_INTERVAL_MINS:
            self.reconciler.reconcile()
            self._last_reconcile_run = now()


# =========================================================
# MAIN
# =========================================================

def main():
    OrderManagerApp().run()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass   # clean kill switch exit
    except KeyboardInterrupt:
        log("Interrupted")
    except Exception as e:
        log(f"Fatal: {e}")
        traceback.print_exc()