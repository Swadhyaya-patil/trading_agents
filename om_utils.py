"""
om_utils.py — Shared utility functions.
"""
import time
from datetime import datetime


def now():
    return datetime.now()


def log(msg):
    print(f"[{now().strftime('%H:%M:%S')}] {msg}")


def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        f = float(v)
        return default if (f != f) else f
    except Exception:
        return default


def safe_int(v, default=0):
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def round_to_tick(price, tick=0.05):
    return round(round(price / tick) * tick, 2)


def strip_eq(symbol: str) -> str:
    return symbol.replace("-EQ", "").replace("-BE", "").strip()


def wait_until(hour, minute):
    target = now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now() >= target:
        return
    while now() < target:
        rem = int((target - now()).total_seconds())
        print(f"\rWaiting for {hour:02d}:{minute:02d} | {rem}s remaining  ",
              end="", flush=True)
        time.sleep(5)
    print()