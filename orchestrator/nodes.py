import numpy as np
from agents.historical_agent import HistoricalAgent
from shared.state import TradingState
from shared.models import StrategySignal
from agents.strategies.momentum_agent import MomentumAgent
from agents.strategies.breakout_agent import BreakoutAgent
from agents.strategies.stochastic_agent import StochasticAgent
from agents.strategies.avg_momentum_agent import AvgMomentumAgent
from agents.strategies.oi_agent import OIAgent
from agents.strategies.ml_model_agent import MLModelAgent
from agents.trade_llm_agent import TradeLLMAgent
from brokers.angleone.executor import AngelOneExecutor
from orchestrator import df_cache

# ── Singletons ─────────────────────────────────────────────────────────────
_historical_agent = HistoricalAgent()

rule_strategies = [
    MomentumAgent(),
    BreakoutAgent(),
    StochasticAgent(),
    AvgMomentumAgent(),
    OIAgent(),
    MLModelAgent(),
]

_trade_llm = TradeLLMAgent()
_executor: AngelOneExecutor = None


def _get_executor() -> AngelOneExecutor:
    global _executor
    if _executor is None:
        _executor = AngelOneExecutor(_historical_agent.client)
    return _executor


def _sanitize(obj):
    """
    Recursively convert numpy/pandas scalars to native Python types so
    msgpack (used by LangGraph MemorySaver) can serialize the state dict.
    Fixes: "Type is not msgpack serializable: numpy.float64"
    """
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def _to_dict(signal: StrategySignal) -> dict:
    """Convert StrategySignal to msgpack-safe plain dict."""
    return _sanitize(signal.model_dump())


# ── Node 1: data fetcher ───────────────────────────────────────────────────
def data_fetcher_node(state: TradingState) -> TradingState:
    df = _historical_agent.get_market_data(
        symbol    = state["symbol"],
        code      = state["code"],
        interval  = state.get("interval", "ONE_DAY"),
        from_date = state.get("from_date"),
        to_date   = state.get("to_date"),
    )

    if df is not None:
        df_cache.store(state["symbol"], df)

    rows = len(df) if df is not None else 0
    return {
        **state,
        "reasoning": state.get("reasoning", []) + [
            f"Data fetched for {state['symbol']}: {rows} rows"
        ],
    }


# ── Node 2: rule-based + ML strategies ────────────────────────────────────
def strategy_node(state: TradingState) -> TradingState:
    df     = df_cache.retrieve(state["symbol"])
    symbol = state["symbol"]

    if df is None:
        return {
            **state,
            "signals":   [],
            "reasoning": state.get("reasoning", []) + ["No data — skipping strategies"],
        }

    signals = []
    for strategy in rule_strategies:
        try:
            signal = strategy.evaluate(df, symbol)
            if signal:
                signals.append(signal)
                print(f"  ✓ {signal.strategy}: {signal.signal} (conf={signal.confidence:.2f})")
        except Exception as e:
            print(f"  ✗ {strategy.__class__.__name__} error on {symbol}: {e}")

    fired = [s.strategy for s in signals]
    return {
        **state,
        "signals":   [_to_dict(s) for s in signals],
        "reasoning": state.get("reasoning", []) + [
            f"{len(signals)}/{len(rule_strategies)} strategies fired: {fired}"
        ],
    }


# ── Node 3: TradeLLM — only runs when >=1 signal fired ────────────────────
def trade_llm_node(state: TradingState) -> TradingState:
    if not state.get("signals"):
        return state

    df     = df_cache.retrieve(state["symbol"])
    symbol = state["symbol"]

    try:
        signal = _trade_llm.evaluate(df, symbol)
        if signal:
            print(f"  ✓ TradeLLM: {signal.signal} (conf={signal.confidence:.2f})")
            return {
                **state,
                "signals":   state["signals"] + [_to_dict(signal)],
                "reasoning": state.get("reasoning", []) + [
                    f"TradeLLM confirmed: {signal.signal}"
                ],
            }
    except Exception as e:
        print(f"  ✗ TradeLLM error on {symbol}: {e}")

    return state


# ── Node 4: executor ───────────────────────────────────────────────────────
def executor_node(state: TradingState) -> TradingState:
    decision = state.get("final_decision")
    meta     = state.get("metadata", {})
    symbol   = state["symbol"]

    df    = df_cache.retrieve(symbol)
    close = float(df["Close"].iloc[-1]) if df is not None and len(df) > 0 else 0.0

    try:
        import pandas as pd
        fno = pd.read_csv("data/FNO_LST_190.csv", usecols=["Script", "LOTSIZ"])
        fno = fno.dropna(subset=["Script", "LOTSIZ"]).set_index("Script")
        lot = int(fno.loc[symbol, "LOTSIZ"]) if symbol in fno.index else 1
    except Exception:
        lot = 1

    result = _get_executor().execute(
        symbol      = symbol,
        signal      = decision,
        close_price = close,
        lot_size    = lot,
        sl_pct      = 0.03,
    )

    from orchestrator.logger import log_order
    log_order(symbol, decision, result)
    df_cache.clear(symbol)

    return {
        **state,
        "reasoning": state.get("reasoning", []) + [
            f"Executor: {result['status']} order {result.get('order_id', '')} @ ₹{close:.2f}"
        ],
        "metadata": {**meta, "order": result},
    }
