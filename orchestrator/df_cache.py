# Simple in-memory cache so DataFrames never enter LangGraph state.
# df_cache.clear() is called for EVERY symbol after its pipeline run
# (in executor_node AND in main.py finally block) to prevent unbounded
# memory growth across the full 190-symbol scan.

_cache: dict[str, object] = {}


def store(symbol: str, df) -> None:
    _cache[symbol] = df


def retrieve(symbol: str):
    return _cache.get(symbol)


def clear(symbol: str) -> None:
    _cache.pop(symbol, None)
