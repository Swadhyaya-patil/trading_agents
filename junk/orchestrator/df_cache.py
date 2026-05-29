# Simple in-memory cache so DataFrame never enters LangGraph state
_cache: dict[str, object] = {}

def store(symbol: str, df) -> None:
    _cache[symbol] = df

def retrieve(symbol: str):
    return _cache.get(symbol)

def clear(symbol: str) -> None:
    _cache.pop(symbol, None)