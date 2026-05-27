"""
order_manager.py

Single-file trade execution and management system.
Run once at 9:00–9:10 AM — it handles everything automatically:

    9:00 AM  → start script
    9:15 AM  → place TSL updates for existing open positions
    9:20 AM  → place fresh entry orders from last night's signals
    Every 30 mins → trail stop losses on all open positions
    3:20 PM  → EOD cleanup, cancel pending orders, P&L report
    3:30 PM  → exit

.env configuration:
    DRY_RUN=true/false
    TOTAL_CAPITAL=1000000
    MAX_TRADES=25
    MAX_RISK_PCT=0.03           3% stop loss
    RR_RATIO=3.0                1:3 reward:risk → 9% target
    TSL_INTERVAL_MINS=30
    ENTRY_OFFSET_PCT=0.0        0.0  = EOD close price
                                -0.5 = 0.5% below close (limit buy cheaper)
                                +0.5 = 0.5% above close (momentum entry)
    MIN_CONFIDENCE=0.85         only HIGH confidence trades executed
"""

import os
import sys
import time
import sqlite3
import traceback
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from brokers.angleone.hist_data import hist_data
from brokers.angleone.executor import AngelOneExecutor

# ─────────────────────────────────────────────
# CONFIG  (all overridable via .env)
# ─────────────────────────────────────────────
TOTAL_CAPITAL      = float(os.getenv("TOTAL_CAPITAL",     "1000000"))
MAX_TRADES         = int(os.getenv("MAX_TRADES",          "25"))
MAX_RISK_PCT       = float(os.getenv("MAX_RISK_PCT",      "0.03"))
RR_RATIO           = float(os.getenv("RR_RATIO",          "3.0"))
TSL_INTERVAL_MINS  = int(os.getenv("TSL_INTERVAL_MINS",   "30"))
ENTRY_OFFSET_PCT   = float(os.getenv("ENTRY_OFFSET_PCT",  "0.0"))
MIN_CONFIDENCE     = float(os.getenv("MIN_CONFIDENCE",    "0.85"))
DB_PATH            = "data/signals.db"

MARKET_OPEN_TSL    = (9, 15)
MARKET_OPEN_ENTRY  = (9, 20)
MARKET_CLOSE_EOD   = (15, 20)
MARKET_EXIT        = (15, 30)
POSITION_SIZE_PCT  = 0.05


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def now() -> datetime:
    return datetime.now()

def log(msg: str):
    print(f"[{now().strftime('%H:%M:%S')}] {msg}")

def wait_until(hour: int, minute: int):
    target = now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now() >= target:
        return  # already past — don't block
    while now() < target:
        remaining = int((target - now()).total_seconds())
        print(f"\r  Waiting for {hour:02d}:{minute:02d} — {remaining}s remaining   ",
              end="", flush=True)
        time.sleep(10)
    print()

def safe_float(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        f = float(val)
        return default if pd.isna(f) else f
    except Exception:
        return default

def safe_col(pos, col: str, default=None):
    try:
        if isinstance(pos, dict):
            return pos.get(col, default)
        if col in pos.index:
            val = pos[col]
            return default if (isinstance(val, float) and pd.isna(val)) else val
        return default
    except Exception:
        return default

def entry_price(close: float, signal: str) -> float:
    offset = ENTRY_OFFSET_PCT / 100.0
    if signal == "BUY":
        raw = close * (1 + offset)
    else:
        raw = close * (1 - offset)
    return round_to_tick(raw)          # ← FIX 2 applied

def calc_sl(price: float, signal: str) -> float:
    if signal == "BUY":
        raw = price * (1 - MAX_RISK_PCT)
    else:
        raw = price * (1 + MAX_RISK_PCT)
    return round_to_tick(raw)          # ← FIX 2 applied

def calc_target(price: float, signal: str) -> float:
    if signal == "BUY":
        raw = price * (1 + MAX_RISK_PCT * RR_RATIO)
    else:
        raw = price * (1 - MAX_RISK_PCT * RR_RATIO)
    return round_to_tick(raw)          # ← FIX 2 applied

def calc_quantity(price: float, available_capital: float = None) -> int:
    """
    Calculate quantity based on position size % of total capital.
    Caps to available_capital if provided.
    Uses LTP as the price basis — not the offset entry price.
    """
    cap = available_capital if available_capital else TOTAL_CAPITAL
    alloc = min(TOTAL_CAPITAL * POSITION_SIZE_PCT, cap)
    qty   = int(alloc / price)
    return max(qty, 1)

def _get_deployed_capital() -> float:
    """Sum of capital in currently open positions."""
    with get_db_conn() as conn:
        try:
            r = conn.execute("""
                SELECT SUM(entry_price * quantity) FROM orders
                WHERE status IN ('PLACED', 'DRY_RUN', 'PARTIAL')
            """).fetchone()
            return safe_float(r[0]) if r and r[0] else 0.0
        except Exception:
            return 0.0
        

def get_db_conn():
    return sqlite3.connect(DB_PATH)


# ─────────────────────────────────────────────
# DB SETUP & MIGRATION
# ─────────────────────────────────────────────

def ensure_tables():
    with get_db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT,
                date            TEXT,
                symbol          TEXT,
                decision        TEXT,
                entry_price     REAL,
                current_price   REAL,
                stop_loss       REAL,
                initial_sl      REAL,
                target          REAL,
                quantity        INTEGER,
                order_id        TEXT,
                sl_order_id     TEXT,
                status          TEXT,
                confidence      REAL,
                pnl             REAL,
                dry_run         INTEGER,
                notes           TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                symbol      TEXT,
                decision    TEXT,
                confidence  REAL,
                reason      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tsl_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                symbol      TEXT,
                ltp         REAL,
                old_sl      REAL,
                new_sl      REAL,
                action      TEXT
            )
        """)

        # Migrate: add any missing columns (safe to run repeatedly)
        migrations = [
            "ALTER TABLE orders ADD COLUMN target        REAL",
            "ALTER TABLE orders ADD COLUMN initial_sl    REAL",
            "ALTER TABLE orders ADD COLUMN current_price REAL",
            "ALTER TABLE orders ADD COLUMN confidence    REAL",
            "ALTER TABLE orders ADD COLUMN sl_order_id   TEXT",
            "ALTER TABLE orders ADD COLUMN dry_run       INTEGER",
            "ALTER TABLE orders ADD COLUMN notes         TEXT",
            "ALTER TABLE orders ADD COLUMN date          TEXT",
            "ALTER TABLE orders ADD COLUMN pnl           REAL",
            "ALTER TABLE orders ADD COLUMN entry_price   REAL",
            "ALTER TABLE orders ADD COLUMN decision      TEXT",
            "ALTER TABLE orders ADD COLUMN entry_filled INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass

        # Rename legacy columns if they exist
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
            if "price" in cols and "entry_price" not in cols:
                conn.execute("ALTER TABLE orders RENAME COLUMN price TO entry_price")
                log("  [DB] Renamed 'price' -> 'entry_price'")
            if "direction" in cols and "decision" not in cols:
                conn.execute("ALTER TABLE orders RENAME COLUMN direction TO decision")
                log("  [DB] Renamed 'direction' -> 'decision'")
        except Exception as e:
            log(f"  [DB] Column rename skipped: {e}")

        conn.commit()

        # Show current schema
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        log(f"  [DB] orders columns: {cols}")


# ─────────────────────────────────────────────
# DATA ACCESS
# ─────────────────────────────────────────────

def last_trading_day() -> str:
    with get_db_conn() as conn:
        try:
            result = conn.execute("""
                SELECT date(run_date) as sig_date
                FROM signals
                WHERE final_decision IN ('BUY', 'SELL')
                ORDER BY run_date DESC LIMIT 1
            """).fetchone()
            if result:
                return result[0]
        except Exception:
            pass
    return now().strftime("%Y-%m-%d")

def is_signal_still_valid(signal_date: str) -> bool:
    sig_dt   = datetime.strptime(signal_date, "%Y-%m-%d")
    age_days = (now() - sig_dt).days
    return age_days <= 4

def get_open_positions() -> pd.DataFrame:
    with get_db_conn() as conn:
        try:
            return pd.read_sql("""
                SELECT * FROM orders
                WHERE status IN ('PLACED', 'DRY_RUN', 'PARTIAL')
                ORDER BY timestamp DESC
            """, conn)
        except Exception:
            return pd.DataFrame()

def get_open_position_symbols() -> list:
    df = get_open_positions()
    return df["symbol"].tolist() if not df.empty else []

def get_todays_signals() -> pd.DataFrame:
    sig_date = last_trading_day()
    if not is_signal_still_valid(sig_date):
        log(f"  Signals from {sig_date} are too old (>4 days). Run main.py first.")
        return pd.DataFrame()
    age = (now() - datetime.strptime(sig_date, "%Y-%m-%d")).days
    if age > 0:
        log(f"  Using signals from {sig_date} ({age}d ago — {'weekend' if age<=3 else 'holiday'} gap)")
    else:
        log(f"  Using today's signals ({sig_date})")
    with get_db_conn() as conn:
        try:
            return pd.read_sql("""
                SELECT symbol, final_decision, avg_confidence,
                       supervisor_conf, suggested_entry, timeframe, reasoning, signal_price
                FROM signals
                WHERE date(run_date) = ?
                AND final_decision IN ('BUY', 'SELL')
                ORDER BY supervisor_conf DESC, avg_confidence DESC
            """, conn, params=[sig_date])
        except Exception as e:
            log(f"ERROR reading signals: {e}")
            return pd.DataFrame()

def count_todays_orders() -> int:
    with get_db_conn() as conn:
        try:
            r = conn.execute("""
                SELECT COUNT(*) FROM orders
                WHERE date(timestamp) = date('now')
                AND status IN ('PLACED', 'DRY_RUN', 'PARTIAL')
            """).fetchone()
            return r[0] if r else 0
        except Exception:
            return 0

def update_order_sl(symbol: str, new_sl: float, new_price: float):
    with get_db_conn() as conn:
        conn.execute("""
            UPDATE orders SET stop_loss = ?, current_price = ?
            WHERE symbol = ? AND status IN ('PLACED', 'DRY_RUN', 'PARTIAL')
        """, [new_sl, new_price, symbol])

def close_position(symbol: str, ltp: float, reason: str):
    with get_db_conn() as conn:
        row = conn.execute("""
            SELECT entry_price, quantity, decision FROM orders
            WHERE symbol = ? AND status IN ('PLACED', 'DRY_RUN', 'PARTIAL')
            ORDER BY timestamp DESC LIMIT 1
        """, [symbol]).fetchone()
        if row:
            entry, qty, decision = row
            entry = safe_float(entry)
            qty   = int(qty) if qty else 1
            pnl   = (ltp - entry) * qty if decision == "BUY" else (entry - ltp) * qty
            conn.execute("""
                UPDATE orders SET status='CLOSED', current_price=?, pnl=?, notes=?
                WHERE symbol=? AND status IN ('PLACED','DRY_RUN','PARTIAL')
            """, [ltp, round(pnl, 2), reason, symbol])
            log(f"  CLOSED {symbol} @ Rs{ltp:.2f} | P&L: Rs{pnl:+.2f} | {reason}")

# def _save_order(symbol, decision, e_price, ltp, sl, target, qty, conf, result):
#     with get_db_conn() as conn:
#         conn.execute("""
#             INSERT INTO orders (
#                 timestamp, date, symbol, decision,
#                 entry_price, current_price, stop_loss, initial_sl,
#                 target, quantity, order_id, sl_order_id,
#                 status, confidence, dry_run, notes
#             ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
#         """, [
#             now().strftime("%Y-%m-%d %H:%M:%S"),
#             now().strftime("%Y-%m-%d"),
#             symbol, decision, e_price, ltp, sl, sl, target, qty,
#             result.get("order_id"), result.get("sl_order_id"),
#             result["status"], conf,
#             int(result.get("dry_run", True)),
#             f"Entry offset {ENTRY_OFFSET_PCT:+.1f}%",
#         ])


# ─────────────────────────────────────────────
# FIX 3 — _save_order: track whether entry was
#          actually filled before allowing SL
# ─────────────────────────────────────────────
# Add 'entry_filled' column to orders table
# SL is only placed when entry_filled = 1
 
# Add to migrations list in ensure_tables():
#   "ALTER TABLE orders ADD COLUMN entry_filled  INTEGER DEFAULT 0",
 
# Updated _save_order — sets entry_filled based on status
def _save_order(symbol, decision, e_price, ltp, sl, target, qty, conf, result):
    entry_filled = 1 if result["status"] == "PLACED" else 0
    with get_db_conn() as conn:
        conn.execute("""
            INSERT INTO orders (
                timestamp, date, symbol, decision,
                entry_price, current_price, stop_loss, initial_sl,
                target, quantity, order_id, sl_order_id,
                status, confidence, dry_run, notes, entry_filled
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            now().strftime("%Y-%m-%d %H:%M:%S"),
            now().strftime("%Y-%m-%d"),
            symbol, decision, e_price, ltp, sl, sl, target, qty,
            result.get("order_id"), result.get("sl_order_id"),
            result["status"], conf,
            int(result.get("dry_run", True)),
            f"Entry offset {ENTRY_OFFSET_PCT:+.1f}%",
            entry_filled,
        ])

def _add_to_watchlist(symbol, decision, conf, reason):
    with get_db_conn() as conn:
        conn.execute("""
            INSERT INTO watchlist (timestamp, symbol, decision, confidence, reason)
            VALUES (?,?,?,?,?)
        """, [now().strftime("%Y-%m-%d %H:%M:%S"), symbol, decision, conf, reason])


# ─────────────────────────────────────────────
# PHASE 1 — UPDATE TSL FOR EXISTING POSITIONS
# ─────────────────────────────────────────────

def update_existing_tsl(client, executor):
    positions = get_open_positions()
    if positions.empty:
        log("No existing positions to update TSL")
        return
    log(f"Updating TSL for {len(positions)} existing positions...")
    for _, pos in positions.iterrows():
        symbol = safe_col(pos, "symbol", "UNKNOWN")
        try:
            token = client.token_lookup(symbol)
            ltp   = client.get_ltp_data(token, exchange="NSE")
        except Exception as e:
            log(f"  x {symbol} LTP failed: {e}")
            continue
        _trail_stop(client, executor, pos, ltp)
        time.sleep(0.4)


# ─────────────────────────────────────────────
# PHASE 2 — PLACE FRESH ENTRY ORDERS
# ─────────────────────────────────────────────

# def place_orders(client, executor):
#     signals         = get_todays_signals()
#     open_symbols    = get_open_position_symbols()
#     current_count   = count_todays_orders()
#     available_slots = MAX_TRADES - current_count
#     placed = watchlisted = skipped = 0

#     log(f"\n{'='*55}")
#     log(f"PLACING ORDERS — {now().strftime('%Y-%m-%d %H:%M')}")
#     log(f"Signals: {len(signals)} | Open: {current_count} | Slots: {available_slots}")
#     log(f"Entry offset: {ENTRY_OFFSET_PCT:+.1f}% | Min conf: {MIN_CONFIDENCE}")
#     log(f"SL: {MAX_RISK_PCT*100:.1f}% | Target: {MAX_RISK_PCT*RR_RATIO*100:.1f}% (1:{RR_RATIO:.0f})")
#     log(f"{'='*55}\n")

#     if signals.empty:
#         log("No signals found — check that main.py ran last night")
#         return

#     for _, row in signals.iterrows():
#         symbol   = row["symbol"]
#         decision = row["final_decision"]
#         conf     = safe_float(row.get("supervisor_conf") or row.get("avg_confidence"), 0.0)


#         if conf < MIN_CONFIDENCE:
#             if conf >= 0.70:
#                 _add_to_watchlist(symbol, decision, conf,
#                                   f"Medium confidence {conf:.2f}")
#                 watchlisted += 1
#                 log(f"  WATCH  {symbol}: {decision} conf={conf:.2f}")

#         # order_manager.py — in place_orders(), after the confidence check
#             if is_cautionary(client, symbol):
#                 log(f"  SKIP   {symbol}: cautionary listing — exchange blocked")
#                 skipped += 1
#                 continue
#             else:
#                 skipped += 1
#                 log(f"  SKIP   {symbol}: conf={conf:.2f} (low)")
#             continue

#         if symbol in open_symbols:
#             log(f"  SKIP   {symbol}: already open")
#             continue

#         if placed >= available_slots:
#             log(f"  Max trades ({MAX_TRADES}) reached")
#             break

#         try:
#             token = client.token_lookup(symbol)
#             ltp   = client.get_ltp_data(token, exchange="NSE")
#         except Exception as e:
#             log(f"  x {symbol}: LTP failed — {e}")
#             continue

#         e_price = entry_price(ltp, decision)
#         sl      = calc_sl(e_price, decision)
#         target  = calc_target(e_price, decision)
#         qty     = calc_quantity(e_price)
#         capital = round(e_price * qty, 2)

#         log(f"\n  -> {symbol}: {decision}")
#         log(f"     Confidence : {conf:.2f}")
#         log(f"     LTP        : Rs{ltp:.2f}")
#         log(f"     Entry      : Rs{e_price:.2f} ({ENTRY_OFFSET_PCT:+.1f}% offset)")
#         log(f"     SL         : Rs{sl:.2f} ({MAX_RISK_PCT*100:.1f}%)")
#         log(f"     Target     : Rs{target:.2f} ({MAX_RISK_PCT*RR_RATIO*100:.1f}%)")
#         log(f"     Qty        : {qty} shares @ Rs{capital:,.0f}")
#         log(f"     Timeframe  : {row.get('timeframe', 'N/A')}")
#         log(f"     Entry type : {row.get('suggested_entry', 'N/A')}")

#         result = executor.execute(
#             symbol=symbol, signal=decision,
#             close_price=e_price, lot_size=qty, sl_pct=MAX_RISK_PCT,
#         )

#         if result["status"] in ("PLACED", "DRY_RUN"):
#             _save_order(symbol, decision, e_price, ltp, sl, target, qty, conf, result)
#             placed += 1
#             log(f"     OK Order ID: {result.get('order_id', 'N/A')}")

#         time.sleep(0.5)

#     log(f"\n{'='*55}")
#     log(f"Done: {placed} placed | {watchlisted} watchlisted | {skipped} skipped")
#     log(f"{'='*55}\n")

# ─────────────────────────────────────────────
# Updated place_orders — all 3 fixes applied
# Replace the existing place_orders function
# ─────────────────────────────────────────────
 
def place_orders(client, executor):
    signals         = get_todays_signals()
    open_symbols    = get_open_position_symbols()
    current_count   = count_todays_orders()
    available_slots = MAX_TRADES - current_count
 
    # FIX 1: track remaining capital
    already_deployed = _get_deployed_capital()
    remaining_capital = TOTAL_CAPITAL - already_deployed
 
    placed = watchlisted = skipped = 0
 
    log(f"\n{'='*55}")
    log(f"PLACING ORDERS — {now().strftime('%Y-%m-%d %H:%M')}")
    log(f"Signals: {len(signals)} | Open: {current_count} | Slots: {available_slots}")
    log(f"Capital: Rs{TOTAL_CAPITAL:,.0f} | Deployed: Rs{already_deployed:,.0f} | "
        f"Available: Rs{remaining_capital:,.0f}")
    log(f"Entry offset: {ENTRY_OFFSET_PCT:+.1f}% | Min conf: {MIN_CONFIDENCE}")
    log(f"SL: {MAX_RISK_PCT*100:.1f}% | Target: {MAX_RISK_PCT*RR_RATIO*100:.1f}% "
        f"(1:{RR_RATIO:.0f})")
    log(f"{'='*55}\n")
 
    if signals.empty:
        log("No signals found — check that main.py ran last night")
        return
 
    for _, row in signals.iterrows():
        symbol   = row["symbol"]
        decision = row["final_decision"]
        conf     = safe_float(
            row.get("supervisor_conf") or row.get("avg_confidence"), 0.0)
        signal_price  = safe_float(row.get("signal_price"), 0.0)
 
        # Confidence filter
        if conf < MIN_CONFIDENCE:
            if conf >= 0.70:
                _add_to_watchlist(symbol, decision, conf,
                                  f"Medium confidence {conf:.2f}")
                watchlisted += 1
                log(f"  WATCH  {symbol}: {decision} conf={conf:.2f}")
            else:
                skipped += 1
                log(f"  SKIP   {symbol}: conf={conf:.2f} (low)")
            continue
 
        # Cautionary check
        if is_cautionary(client, symbol):
            log(f"  SKIP   {symbol}: cautionary listing")
            skipped += 1
            continue
 
        if symbol in open_symbols:
            log(f"  SKIP   {symbol}: already open")
            continue
 
        if placed >= available_slots:
            log(f"  Max trades ({MAX_TRADES}) reached")
            break
 
        # FIX 1: check remaining capital before fetching LTP
        if remaining_capital < TOTAL_CAPITAL * POSITION_SIZE_PCT * 0.5:
            log(f"  Insufficient capital remaining (Rs{remaining_capital:,.0f}) — stopping")
            break
 
        try:
            token = client.token_lookup(symbol)
            ltp   = client.get_ltp_data(token, exchange="NSE")
        except Exception as e:
            log(f"  x {symbol}: LTP failed — {e}")
            continue
 
        
        # FIX 1: calc qty using remaining capital, not just total
        # ── Use signal_price as entry basis, fall back to LTP if missing
        price_basis = signal_price if signal_price > 0 else ltp
        qty     = calc_quantity(ltp, remaining_capital)      # use LTP not offset price
        e_price = entry_price(price_basis, decision)
        sl      = calc_sl(e_price, decision)
        target  = calc_target(e_price, decision)
        capital = round(e_price * qty, 2)
 
        # FIX 1: final capital check
        if capital > remaining_capital:
            qty     = int(remaining_capital / e_price)
            capital = round(e_price * qty, 2)
            log(f"  ! {symbol}: qty reduced to {qty} to fit remaining capital")
            if qty < 1:
                log(f"  SKIP   {symbol}: not enough capital for even 1 share")
                skipped += 1
                continue
 
    # ── Gap analysis
            if decision == "BUY":
                gap_pct = (ltp - price_basis) / price_basis * 100
                if ltp > e_price:
                    gap_action = f"LIMIT order at Rs{e_price:.2f} — waiting for pullback"
                else:
                    gap_action = f"LTP below entry — execute immediately"
            else:
                gap_pct = (price_basis - ltp) / price_basis * 100
                if ltp < e_price:
                    gap_action = f"LIMIT order at Rs{e_price:.2f} — waiting for bounce"
                else:
                    gap_action = f"LTP above entry — execute immediately"

            qty     = calc_quantity(price_basis, remaining_capital)
            capital = round(e_price * qty, 2)

            log(f"\n  -> {symbol}: {decision}")
            log(f"     Signal price : Rs{price_basis:.2f} (EOD close)")
            log(f"     Current LTP  : Rs{ltp:.2f} "
                f"({'gap up' if decision=='BUY' and ltp>price_basis else 'gap down' if decision=='BUY' else ''})"
                f" ({gap_pct:+.1f}%)")
            log(f"     Entry        : Rs{e_price:.2f} ({ENTRY_OFFSET_PCT:+.2f}% of signal price)")
            log(f"     Action       : {gap_action}")
            log(f"     SL           : Rs{sl:.2f} ({MAX_RISK_PCT*100:.1f}%)")
            log(f"     Target       : Rs{target:.2f} ({MAX_RISK_PCT*RR_RATIO*100:.1f}%)")
            log(f"     Qty          : {qty} shares @ Rs{capital:,.0f}")

            # ── Skip if gap is too large — price moved too far from signal
            MAX_GAP_PCT = float(os.getenv("MAX_GAP_PCT", "2.0"))
            if abs(gap_pct) > MAX_GAP_PCT:
                log(f"     SKIP {symbol}: gap {gap_pct:+.1f}% exceeds "
                    f"MAX_GAP_PCT {MAX_GAP_PCT:.1f}% — signal stale")
                skipped += 1
                continue

            result = executor.execute(
                symbol      = symbol,
                signal      = decision,
                close_price = e_price,      # ← limit order AT signal-based price
                lot_size    = qty,
                sl_pct      = MAX_RISK_PCT,
            )
            
        # FIX 3: only save and count if order actually went through
        if result["status"] == "CAUTIONARY":
            skipped += 1
            continue
 
        if result["status"] == "FAILED":
            log(f"     x Order failed: {result.get('error','unknown')}")
            continue
 
        if result["status"] in ("PLACED", "DRY_RUN"):
            _save_order(symbol, decision, e_price, ltp,
                        sl, target, qty, conf, result)
            placed += 1
            remaining_capital -= capital       # FIX 1: deduct from remaining
            log(f"     OK Order ID: {result.get('order_id', 'N/A')}")
            log(f"     Remaining capital: Rs{remaining_capital:,.0f}")
 
        time.sleep(0.5)
 
    log(f"\n{'='*55}")
    log(f"Done: {placed} placed | {watchlisted} watchlisted | {skipped} skipped")
    log(f"{'='*55}\n")
 
 

# ─────────────────────────────────────────────
# PHASE 3 — TSL MANAGEMENT LOOP
# ─────────────────────────────────────────────

def tsl_loop(client, executor):
    market_close = now().replace(
        hour=MARKET_CLOSE_EOD[0], minute=MARKET_CLOSE_EOD[1], second=0)
    log(f"TSL loop — every {TSL_INTERVAL_MINS} mins until "
        f"{MARKET_CLOSE_EOD[0]:02d}:{MARKET_CLOSE_EOD[1]:02d}")

    while now() < market_close:
        positions = get_open_positions()
        if positions.empty:
            log("TSL: No open positions")
        else:
            log(f"TSL check — {len(positions)} positions")
            for _, pos in positions.iterrows():
                symbol = safe_col(pos, "symbol", "?")
                try:
                    token = client.token_lookup(symbol)
                    ltp   = client.get_ltp_data(token, exchange="NSE")
                    _trail_stop(client, executor, pos, ltp)
                except Exception as e:
                    log(f"  TSL error {symbol}: {e}")
                time.sleep(0.4)

        _print_pnl_snapshot(client)

        next_check = now() + timedelta(minutes=TSL_INTERVAL_MINS)
        if next_check >= market_close:
            break
        log(f"Next TSL check at {next_check.strftime('%H:%M')}")
        time.sleep(TSL_INTERVAL_MINS * 60)

def round_to_tick(price: float, tick: float = 0.05) -> float:
    """
    Round price to nearest tick size.
    NSE equities: tick = 0.05 (5 paise)
    """
    return round(round(price / tick) * tick, 2)


def _trail_stop(client, executor, pos, ltp: float):
    symbol     = safe_col(pos, "symbol", "?")
    decision   = safe_col(pos, "decision", "BUY")
    current_sl = safe_float(safe_col(pos, "stop_loss"))
    entry      = safe_float(safe_col(pos, "entry_price"))

    if entry == 0.0:
        log(f"  ! {symbol}: entry_price missing — skipping TSL")
        return

    # Recalculate target if missing
    raw_target = safe_col(pos, "target")
    if raw_target is None or safe_float(raw_target) == 0.0:
        target = calc_target(entry, decision)
        log(f"  {symbol}: target recalculated Rs{target:.2f}")
        with get_db_conn() as conn:
            conn.execute("""
                UPDATE orders SET target=?
                WHERE symbol=? AND status IN ('PLACED','DRY_RUN','PARTIAL')
            """, [target, symbol])
    else:
        target = safe_float(raw_target)

    # Target hit
    if decision == "BUY" and ltp >= target:
        close_position(symbol, ltp, "TARGET HIT")
        return
    if decision == "SELL" and ltp <= target:
        close_position(symbol, ltp, "TARGET HIT")
        return

    # SL hit
    if decision == "BUY" and ltp <= current_sl:
        close_position(symbol, ltp, "SL HIT")
        return
    if decision == "SELL" and ltp >= current_sl:
        close_position(symbol, ltp, "SL HIT")
        return

    # Trail
    if decision == "BUY":
        new_sl = round(ltp * (1 - MAX_RISK_PCT), 2)
        if new_sl > current_sl:
            _apply_tsl(client, executor, pos, ltp, current_sl, new_sl)
        else:
            log(f"  {symbol}: LTP=Rs{ltp:.2f} SL=Rs{current_sl:.2f} — no trail")
    else:
        new_sl = round(ltp * (1 + MAX_RISK_PCT), 2)
        if new_sl < current_sl:
            _apply_tsl(client, executor, pos, ltp, current_sl, new_sl)
        else:
            log(f"  {symbol}: LTP=Rs{ltp:.2f} SL=Rs{current_sl:.2f} — no trail")


# def _apply_tsl(client, executor, pos, ltp, old_sl, new_sl):
#     symbol  = safe_col(pos, "symbol", "?")
#     decision= safe_col(pos, "decision", "BUY")
#     qty     = int(safe_float(safe_col(pos, "quantity"), 1))
#     dry_run = bool(safe_col(pos, "dry_run", True))

#     log(f"  ^ TSL {symbol}: Rs{old_sl:.2f} -> Rs{new_sl:.2f} (LTP Rs{ltp:.2f})")
#     update_order_sl(symbol, new_sl, ltp)

#     with get_db_conn() as conn:
#         conn.execute("""
#             INSERT INTO tsl_log (timestamp, symbol, ltp, old_sl, new_sl, action)
#             VALUES (?,?,?,?,?,?)
#         """, [now().strftime("%Y-%m-%d %H:%M:%S"), symbol, ltp, old_sl, new_sl, "TRAIL"])

#     if dry_run:
#         return

#     try:
#         sl_oid = safe_col(pos, "sl_order_id")
#         if sl_oid:
#             client.angel_obj.cancelOrder(sl_oid, variety="NORMAL")
#             time.sleep(0.3)
#         sl_side = "SELL" if decision == "BUY" else "BUY"
#         new_oid = client.place_limit_order(
#             ticker=symbol, buy_sell=sl_side, price=new_sl, quantity=qty)
#         with get_db_conn() as conn:
#             conn.execute("""
#                 UPDATE orders SET sl_order_id=?
#                 WHERE symbol=? AND status IN ('PLACED','PARTIAL')
#             """, [new_oid, symbol])
#         log(f"    SL updated: {new_oid}")
#     except Exception as e:
#         log(f"    x SL update failed {symbol}: {e}")
#         traceback.print_exc()





 
# ─────────────────────────────────────────────
# FIX 3 — _apply_tsl: only place broker SL when
#          entry is confirmed filled
#          Also tick-round the new SL price
# ─────────────────────────────────────────────
 
def _apply_tsl(client, executor, pos, ltp, old_sl, new_sl):
    symbol   = safe_col(pos, "symbol", "?")
    decision = safe_col(pos, "decision", "BUY")
    qty      = int(safe_float(safe_col(pos, "quantity"), 1))
    dry_run  = bool(safe_col(pos, "dry_run", True))
 
    # FIX 2: tick-round the new SL
    new_sl = round_to_tick(new_sl)
    if new_sl == round_to_tick(old_sl):
        log(f"  {symbol}: after tick rounding SL unchanged — no trail")
        return
 
    log(f"  ^ TSL {symbol}: Rs{old_sl:.2f} -> Rs{new_sl:.2f} (LTP Rs{ltp:.2f})")
    update_order_sl(symbol, new_sl, ltp)
 
    with get_db_conn() as conn:
        conn.execute("""
            INSERT INTO tsl_log (timestamp, symbol, ltp, old_sl, new_sl, action)
            VALUES (?,?,?,?,?,?)
        """, [now().strftime("%Y-%m-%d %H:%M:%S"),
              symbol, ltp, old_sl, new_sl, "TRAIL"])
 
    if dry_run:
        return
 
    # FIX 3: only place SL at broker if entry was actually filled
    entry_filled = safe_col(pos, "entry_filled", 0)
    if not entry_filled:
        log(f"  ! {symbol}: entry not confirmed filled — SL tracked in DB only")
        return
 
    try:
        sl_oid = safe_col(pos, "sl_order_id")
        if sl_oid:
            client.angel_obj.cancelOrder(sl_oid, variety="NORMAL")
            time.sleep(0.3)
 
        sl_side = "SELL" if decision == "BUY" else "BUY"
        new_oid = client.place_limit_order(
            ticker       = symbol,
            buy_sell     = sl_side,
            price        = new_sl,
            quantity     = qty,
            product_type = os.getenv("PRODUCT_TYPE", "DELIVERY"),
        )
 
        if new_oid:
            with get_db_conn() as conn:
                conn.execute("""
                    UPDATE orders SET sl_order_id=?
                    WHERE symbol=? AND status IN ('PLACED','PARTIAL')
                """, [new_oid, symbol])
            log(f"    SL order updated: {new_oid}")
        else:
            log(f"    ! SL order rejected — DB updated, monitor manually")
 
    except Exception as e:
        err = str(e)
        if "AB4036" in err or "cautionary" in err.lower():
            mark_cautionary(symbol, err)
        log(f"    x SL update failed {symbol}: {e}")
 

# ─────────────────────────────────────────────
# PHASE 4 — EOD CLEANUP
# ─────────────────────────────────────────────

def eod_cleanup(client):
    log(f"\n{'='*55}")
    log(f"EOD CLEANUP — {now().strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*55}")

    positions = get_open_positions()
    if not positions.empty:
        log(f"\nClosing {len(positions)} open positions...")
        for _, pos in positions.iterrows():
            symbol = safe_col(pos, "symbol", "?")
            try:
                token = client.token_lookup(symbol)
                ltp   = client.get_ltp_data(token, exchange="NSE")
                close_position(symbol, ltp, "EOD CLOSE")
            except Exception as e:
                log(f"  x EOD close failed {symbol}: {e}")

    with get_db_conn() as conn:
        try:
            df = pd.read_sql("""
                SELECT symbol, decision, entry_price, current_price,
                       stop_loss, target, quantity, status,
                       confidence, pnl, dry_run,
                       date(timestamp) as trade_date
                FROM orders
                WHERE status='CLOSED'
                AND date(timestamp) >= date('now','-7 days')
                ORDER BY timestamp DESC
            """, conn)
        except Exception:
            df = pd.DataFrame()

    log(f"\n{'─'*55}")
    log(f"CLOSED TRADES (last 7 days)")
    log(f"{'─'*55}")

    if df.empty:
        log("No closed trades")
    else:
        total_pnl = df["pnl"].sum() if "pnl" in df.columns else 0
        winners   = len(df[df["pnl"] > 0])
        losers    = len(df[df["pnl"] < 0])
        for _, row in df.iterrows():
            pnl_str = f"Rs{row['pnl']:+.0f}" if pd.notna(row.get("pnl")) else "--"
            tag     = "[DRY]" if row.get("dry_run") else "[LIVE]"
            ep      = safe_float(row.get("entry_price"))
            log(f"  {tag} {row['symbol']:<12} {row['decision']:<5} "
                f"{row['trade_date']} entry=Rs{ep:.2f} P&L={pnl_str} [{row['status']}]")
        log(f"\n  Total P&L  : Rs{total_pnl:+,.2f}")
        log(f"  Winners    : {winners}  Losers: {losers}")
        win_rate = winners / len(df) * 100 if len(df) > 0 else 0
        log(f"  Win rate   : {win_rate:.1f}%")

    # Watchlist
    with get_db_conn() as conn:
        try:
            wl = pd.read_sql("""
                SELECT symbol, decision, confidence, reason
                FROM watchlist WHERE date(timestamp)=date('now')
                ORDER BY confidence DESC
            """, conn)
            if not wl.empty:
                log(f"\n{'─'*55}")
                log(f"WATCHLIST (medium confidence — not traded)")
                log(f"{'─'*55}")
                for _, row in wl.iterrows():
                    log(f"  WATCH {row['symbol']:<12} {row['decision']:<5} "
                        f"conf={row['confidence']:.2f}  {row['reason']}")
        except Exception:
            pass

    # Carry-forward positions
    still_open = get_open_positions()
    if not still_open.empty:
        log(f"\n{'─'*55}")
        log(f"CARRYING TO NEXT TRADING DAY ({len(still_open)} positions)")
        log(f"{'─'*55}")
        for _, pos in still_open.iterrows():
            ep  = safe_float(safe_col(pos, "entry_price"))
            sl  = safe_float(safe_col(pos, "stop_loss"))
            tgt = safe_float(safe_col(pos, "target"))
            log(f"  >> {safe_col(pos,'symbol','?'):<12} "
                f"{safe_col(pos,'decision','?'):<5} "
                f"entry=Rs{ep:.2f} SL=Rs{sl:.2f} target=Rs{tgt:.2f} "
                f"qty={safe_col(pos,'quantity',0)}")
        log(f"\n  TSL will be updated at 9:15 AM next trading day")

    log(f"\n{'='*55}\n")


def _print_pnl_snapshot(client):
    positions = get_open_positions()
    if positions.empty:
        return
    total = 0.0
    log(f"\n  -- P&L Snapshot {now().strftime('%H:%M')} --")
    for _, pos in positions.iterrows():
        symbol = safe_col(pos, "symbol", "?")
        try:
            token = client.token_lookup(symbol)
            ltp   = client.get_ltp_data(token, exchange="NSE")
            qty   = int(safe_float(safe_col(pos, "quantity"), 1))
            entry = safe_float(safe_col(pos, "entry_price"))
            sl    = safe_float(safe_col(pos, "stop_loss"))
            dec   = safe_col(pos, "decision", "BUY")
            pnl   = (ltp - entry) * qty if dec == "BUY" else (entry - ltp) * qty
            total += pnl
            log(f"  {symbol:<15} LTP=Rs{ltp:.2f} SL=Rs{sl:.2f} P&L=Rs{pnl:+.0f}")
        except Exception:
            pass
    log(f"  Total unrealised: Rs{total:+,.0f}\n")


# order_manager.py — add this function
def is_cautionary(client, symbol: str) -> bool:
    """
    Check if AngelOne will reject orders for this symbol.
    Try a tiny dummy order in validation mode — if AB4036, skip it.
    Unfortunately AngelOne has no pre-check API so we catch on first real order.
    Flag the symbol in DB so it's never retried.
    """
    with get_db_conn() as conn:
        try:
            result = conn.execute("""
                SELECT 1 FROM cautionary_symbols WHERE symbol = ?
            """, [symbol]).fetchone()
            return result is not None
        except Exception:
            return False

def mark_cautionary(symbol: str, reason: str):
    """Permanently flag a symbol as cautionary — skip in future runs."""
    with get_db_conn() as conn:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cautionary_symbols (
                    symbol TEXT PRIMARY KEY,
                    reason TEXT,
                    flagged_at TEXT
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO cautionary_symbols (symbol, reason, flagged_at)
                VALUES (?, ?, ?)
            """, [symbol, reason, now().strftime("%Y-%m-%d %H:%M:%S")])
            log(f"  ! {symbol} flagged as cautionary — will be skipped in future")
        except Exception as e:
            log(f"  ! Could not flag {symbol}: {e}")


def cleanup():
    # "import sqlite3
    conn = sqlite3.connect("data/signals.db")
    conn.execute("DROP TABLE IF EXISTS orders")
    conn.commit()
    conn.close()
    print("Done — orders table dropped, will be recreated with correct schema")
# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log("="*55)
    log("ORDER MANAGER STARTING")
    log(f"Capital: Rs{TOTAL_CAPITAL:,.0f} | Max trades: {MAX_TRADES}")
    log(f"Min confidence: {MIN_CONFIDENCE} | Position size: {POSITION_SIZE_PCT*100:.0f}%")
    log(f"SL: {MAX_RISK_PCT*100:.1f}% | Target: {MAX_RISK_PCT*RR_RATIO*100:.1f}% (1:{RR_RATIO:.0f})")
    log(f"Entry offset: {ENTRY_OFFSET_PCT:+.1f}% from close")
    log(f"DRY RUN: {os.getenv('DRY_RUN','true').upper()}")
    log("="*55)

    ensure_tables()
    client   = hist_data()
    client.log_in()
    executor = AngelOneExecutor(client)

    today = now()
    if today.weekday() >= 5:
        log(f"Today is {'Saturday' if today.weekday()==5 else 'Sunday'} — NSE closed.")
        log("Run main.py tonight/Sunday to generate Monday signals.")
        sys.exit(0)

    sig_date = last_trading_day()
    if not is_signal_still_valid(sig_date):
        log(f"No valid signals (last: {sig_date}). Run main.py first.")
        sys.exit(1)

    age = (today - datetime.strptime(sig_date, "%Y-%m-%d")).days
    if age > 0:
        log(f"Will use {sig_date} signals "
            f"(gap: {age}d — {'weekend' if age<=2 else 'holiday'})")

    log(f"\nWaiting for {MARKET_OPEN_TSL[0]:02d}:{MARKET_OPEN_TSL[1]:02d} — TSL update")
    wait_until(*MARKET_OPEN_TSL)
    update_existing_tsl(client, executor)

    log(f"\nWaiting for {MARKET_OPEN_ENTRY[0]:02d}:{MARKET_OPEN_ENTRY[1]:02d} — placing orders")
    wait_until(*MARKET_OPEN_ENTRY)
    place_orders(client, executor)

    log("\nEntering TSL loop...")
    tsl_loop(client, executor)

    log(f"\nWaiting for {MARKET_CLOSE_EOD[0]:02d}:{MARKET_CLOSE_EOD[1]:02d} — EOD cleanup")
    wait_until(*MARKET_CLOSE_EOD)
    eod_cleanup(client)

    log(f"\nDone. Exiting at {MARKET_EXIT[0]:02d}:{MARKET_EXIT[1]:02d}.")
    wait_until(*MARKET_EXIT)


if __name__ == "__main__":
    try:
        main()
        # cleanup()
    except KeyboardInterrupt:
        log("\nInterrupted — running EOD cleanup...")
        try:
            c = hist_data()
            c.log_in()
            eod_cleanup(c)
        except Exception:
            pass