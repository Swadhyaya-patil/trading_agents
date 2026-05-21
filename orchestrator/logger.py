import sqlite3
import csv
import os
from datetime import datetime
from shared.models import StrategySignal


DB_PATH  = "data/signals.db"
CSV_PATH = "data/signals.csv"


def _init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date        TEXT,
            symbol          TEXT,
            final_decision  TEXT,
            strategies_fired TEXT,
            avg_confidence  REAL,
            risk_approved   INTEGER,
            supervisor_conf REAL,
            suggested_entry TEXT,
            timeframe       TEXT,
            max_position_pct REAL,
            reasoning       TEXT,
            momentum_fired  INTEGER,
            breakout_fired  INTEGER,
            stochastic_fired INTEGER
        )
    """)
    conn.commit()


def log_signal(state: dict):
    """Call this at the end of each symbol's graph run."""
    signals: list[StrategySignal] = state.get("signals", [])
    meta    = state.get("metadata", {})

    strategy_names  = [s.strategy for s in signals]
    avg_conf        = round(sum(s.confidence for s in signals) / len(signals), 3) if signals else 0.0
    reasoning_text  = " | ".join(state.get("reasoning", []))
    run_date        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = {
        "run_date":         run_date,
        "symbol":           state.get("symbol", ""),
        "final_decision":   state.get("final_decision", "HOLD"),
        "strategies_fired": ", ".join(strategy_names),
        "avg_confidence":   avg_conf,
        "risk_approved":    int(state.get("risk_approved", False)),
        "supervisor_conf":  meta.get("supervisor_confidence", 0.0),
        "suggested_entry":  meta.get("suggested_entry", ""),
        "timeframe":        meta.get("timeframe", ""),
        "max_position_pct": meta.get("max_position_pct", 0.0),
        "reasoning":        reasoning_text,
        "momentum_fired":   int("Momentum"   in strategy_names),
        "breakout_fired":   int("Breakout"   in strategy_names),
        "stochastic_fired": int("Stochastic" in strategy_names),
    }

    # ── SQLite
    os.makedirs("data", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        _init_db(conn)
        conn.execute("""
            INSERT INTO signals (
                run_date, symbol, final_decision, strategies_fired,
                avg_confidence, risk_approved, supervisor_conf,
                suggested_entry, timeframe, max_position_pct, reasoning,
                momentum_fired, breakout_fired, stochastic_fired
            ) VALUES (
                :run_date, :symbol, :final_decision, :strategies_fired,
                :avg_confidence, :risk_approved, :supervisor_conf,
                :suggested_entry, :timeframe, :max_position_pct, :reasoning,
                :momentum_fired, :breakout_fired, :stochastic_fired
            )
        """, row)

    # ── CSV  (append mode — one file per day)
    csv_path = CSV_PATH.replace(".csv", f"_{datetime.now().strftime('%Y%m%d')}.csv")
    write_header = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)



def log_order(symbol: str, decision: str, order: dict):
    """Log executed order details to SQLite."""
    os.makedirs("data", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                symbol      TEXT,
                decision    TEXT,
                price       REAL,
                stop_loss   REAL,
                quantity    INTEGER,
                order_id    TEXT,
                status      TEXT,
                error       TEXT,
                dry_run     INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO orders (
                timestamp, symbol, decision, price, stop_loss,
                quantity, order_id, status, error, dry_run
            ) VALUES (
                :timestamp, :symbol, :decision, :price, :stop_loss,
                :quantity, :order_id, :status, :error, :dry_run
            )
        """, {
            "timestamp": order.get("timestamp"),
            "symbol":    symbol,
            "decision":  decision,
            "price":     order.get("price"),
            "stop_loss": order.get("stop_loss"),
            "quantity":  order.get("quantity"),
            "order_id":  order.get("order_id"),
            "status":    order.get("status"),
            "error":     order.get("error"),
            "dry_run":   int(order.get("dry_run", True)),
        })