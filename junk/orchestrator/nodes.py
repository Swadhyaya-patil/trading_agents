# from shared.state import TradingState
# from agents.historical_agent import HistoricalAgent
# from agents.strategies.momentum_agent import MomentumAgent
# from agents.strategies.breakout_agent import BreakoutAgent
# from agents.strategies.stochastic_agent import StochasticAgent

# historical_agent = HistoricalAgent()
# strategies = [MomentumAgent(), BreakoutAgent(), StochasticAgent()]

# # Node 1 — fetch and enrich data
# def data_fetcher_node(state: TradingState) -> TradingState:
#     df = historical_agent.get_market_data(
#         symbol=state["symbol"],
#         interval=state["interval"],
#         from_date=state["from_date"],
#         to_date=state["to_date"],
#     )
#     return {**state, "df": df, "reasoning": ["Data fetched and enriched"]}

# # Node 2 — run all strategy agents
# def strategy_node(state: TradingState) -> TradingState:
#     df = state["df"]
#     symbol = state["symbol"]
#     signals = []

#     for strategy in strategies:
#         try:
#             signal = strategy.evaluate(df, symbol)
#             if signal:
#                 signals.append(signal)
#         except Exception as e:
#             print(f"Strategy error: {e}")

#     return {**state, "signals": signals, "reasoning": state["reasoning"] + [f"{len(signals)} signals fired"]}

# # Node 3 — Ollama risk manager (next step — see below)
# def risk_manager_node(state: TradingState) -> TradingState:
#     # placeholder until Ollama is wired in
#     approved = len(state["signals"]) >= 2
#     return {**state, "risk_approved": approved}

# # Node 4 — Ollama supervisor (next step)
# def supervisor_node(state: TradingState) -> TradingState:
#     if not state["risk_approved"]:
#         return {**state, "final_decision": "HOLD", "reasoning": state["reasoning"] + ["Risk rejected"]}
#     signals = state["signals"]
#     buys = [s for s in signals if s.signal == "BUY"]
#     decision = "BUY" if len(buys) >= 2 else "HOLD"
#     return {**state, "final_decision": decision}

# # Node 5 — executor (paper trading for now)
# def executor_node(state: TradingState) -> TradingState:
#     print(f"\n{'='*40}")
#     print(f"DECISION: {state['final_decision']} — {state['symbol']}")
#     print(f"Signals: {[s.strategy for s in state['signals']]}")
#     print(f"{'='*40}\n")
#     return state












# from shared.state import TradingState
# from agents.historical_agent import HistoricalAgent
# from agents.strategies.momentum_agent import MomentumAgent
# from agents.strategies.breakout_agent import BreakoutAgent
# from agents.strategies.stochastic_agent import StochasticAgent

# historical_agent = HistoricalAgent()
# strategies = [MomentumAgent(), BreakoutAgent(), StochasticAgent()]


# # Node 1 — fetch and enrich data
# def data_fetcher_node(state: TradingState) -> TradingState:
#     df = historical_agent.get_market_data(
#         symbol=state["symbol"],
#         code=state["code"],          # ← was missing entirely before
#         interval=state.get("interval", "ONE_DAY"),
#         from_date=state.get("from_date"),
#         to_date=state.get("to_date"),
#     )
#     return {
#         **state,
#         "df": df,
#         "reasoning": state.get("reasoning", []) + [
#             f"Data fetched for {state['symbol']}: {len(df) if df is not None else 0} rows"
#         ],
#     }


# # Node 2 — run all 3 strategy agents (each self-contained, no pre-enrichment needed)
# def strategy_node(state: TradingState) -> TradingState:
#     df = state["df"]
#     if df is None:
#         return {**state, "signals": [], "reasoning": state["reasoning"] + ["No data, skipping strategies"]}

#     symbol  = state["symbol"]
#     signals = []

#     for strategy in strategies:
#         try:
#             signal = strategy.evaluate(df, symbol)
#             if signal:
#                 signals.append(signal)
#                 print(f"  ✓ {signal.strategy}: {signal.signal} (conf={signal.confidence})")
#         except Exception as e:
#             print(f"  ✗ {strategy.__class__.__name__} error on {symbol}: {e}")

#     return {
#         **state,
#         "signals": signals,
#         "reasoning": state["reasoning"] + [
#             f"{len(signals)}/3 strategies fired: {[s.strategy for s in signals]}"
#         ],
#     }


# # Node 3 — risk manager (Ollama — already built)
# # Node 4 — supervisor  (Ollama — already built)


# # Node 5 — executor (dry run)
# def executor_node(state: TradingState) -> TradingState:
#     meta = state.get("metadata", {})
#     print(f"\n{'='*50}")
#     print(f"  SYMBOL   : {state['symbol']}")
#     print(f"  DECISION : {state['final_decision']}")
#     print(f"  CONFIDENCE: {meta.get('supervisor_confidence', 'N/A')}")
#     print(f"  ENTRY    : {meta.get('suggested_entry', 'N/A')}")
#     print(f"  TIMEFRAME: {meta.get('timeframe', 'N/A')}")
#     print(f"  POSITION : {meta.get('max_position_pct', 'N/A')*100:.1f}% of portfolio"
#           if isinstance(meta.get('max_position_pct'), float) else "")
#     print(f"  SIGNALS  : {[s.strategy for s in state['signals']]}")
#     for r in state.get("reasoning", []):
#         print(f"  › {r}")
#     print(f"{'='*50}\n")
#     return state





# from shared.state import TradingState
# from agents.historical_agent import HistoricalAgent
# # from agents.strategies.momentum_agent import MomentumAgent
# # from agents.strategies.breakout_agent import BreakoutAgent
# # from agents.strategies.stochastic_agent import StochasticAgent
# from agents.strategies.momentum_agent import MomentumAgent
# from agents.strategies.breakout_agent import BreakoutAgent
# from agents.strategies.stochastic_agent import StochasticAgent
# from agents.strategies.avg_momentum_agent import AvgMomentumAgent
# from agents.strategies.oi_agent import OIAgent
# from orchestrator import df_cache

# historical_agent = HistoricalAgent()
# # strategies = [MomentumAgent(), BreakoutAgent(), StochasticAgent()]
# # strategies = [
# #     MomentumAgent(),
# #     BreakoutAgent(),
# #     StochasticAgent(),
# #     AvgMomentumAgent(),
# #     OIAgent(),            # initialises bhav download once at startup
# # ]
# from agents.strategies.ml_model_agent import MLModelAgent

# # strategies = [
# #     MomentumAgent(),
# #     BreakoutAgent(),
# #     StochasticAgent(),
# #     AvgMomentumAgent(),
# #     OIAgent(),
# #     MLModelAgent(),    # ← loads model once, gracefully disabled if model missing
# # ]

# from agents.trade_llm_agent import TradeLLMAgent

# strategies = [
#     MomentumAgent(),
#     BreakoutAgent(),
#     StochasticAgent(),
#     AvgMomentumAgent(),
#     OIAgent(),
#     MLModelAgent(),       # needs model files
#     TradeLLMAgent(),      # local Ollama — always available
# ]

from agents.historical_agent import HistoricalAgent
from shared.state import TradingState
from agents.strategies.momentum_agent import MomentumAgent
from agents.strategies.breakout_agent import BreakoutAgent
from agents.strategies.stochastic_agent import StochasticAgent
from agents.strategies.avg_momentum_agent import AvgMomentumAgent
from agents.strategies.oi_agent import OIAgent
from agents.strategies.ml_model_agent import MLModelAgent
from agents.trade_llm_agent import TradeLLMAgent
from orchestrator import df_cache
from shared.state import TradingState

# Rule-based + ML strategies — run on every symbol
rule_strategies = [
    MomentumAgent(),
    BreakoutAgent(),
    StochasticAgent(),
    AvgMomentumAgent(),
    OIAgent(),
    MLModelAgent(),
]

# TradeLLM — only called when at least 1 signal fires
trade_llm = TradeLLMAgent()


def data_fetcher_node(state: TradingState) -> TradingState:
    df = HistoricalAgent().get_market_data(
        symbol=state["symbol"],
        code=state["code"],
        interval=state.get("interval", "ONE_DAY"),
        from_date=state.get("from_date"),
        to_date=state.get("to_date"),
    )
    # Store df in cache — never put it in state
    df_cache.store(state["symbol"], df)

    rows = len(df) if df is not None else 0
    return {
        **state,
        "reasoning": state.get("reasoning", []) + [
            f"Data fetched for {state['symbol']}: {rows} rows"
        ],
    }


# def strategy_node(state: TradingState) -> TradingState:
#     df = df_cache.retrieve(state["symbol"])    # ← pull from cache
#     if df is None:
#         return {
#             **state,
#             "signals": [],
#             "reasoning": state["reasoning"] + ["No data, skipping strategies"],
#         }

#     symbol  = state["symbol"]
#     signals = []

#     for strategy in strategies:
#         try:
#             signal = strategy.evaluate(df, symbol)
#             if signal:
#                 signals.append(signal)
#                 print(f"  ✓ {signal.strategy}: {signal.signal} (conf={signal.confidence})")
#         except Exception as e:
#             print(f"  ✗ {strategy.__class__.__name__} error on {symbol}: {e}")

#     return {
#         **state,
#         "signals": signals,
#         "reasoning": state["reasoning"] + [
#             f"{len(signals)}/3 strategies fired: {[s.strategy for s in signals]}"
#         ],
#     }

def strategy_node(state: TradingState) -> TradingState:
    """Run rule-based + ML strategies on every symbol."""
    df     = df_cache.retrieve(state["symbol"])
    if df is None:
        return {**state, "signals": [],
                "reasoning": state["reasoning"] + ["No data"]}

    symbol  = state["symbol"]
    signals = []

    for strategy in rule_strategies:
        try:
            signal = strategy.evaluate(df, symbol)
            if signal:
                signals.append(signal)
                print(f"  ✓ {signal.strategy}: {signal.signal} "
                      f"(conf={signal.confidence})")
        except Exception as e:
            print(f"  ✗ {strategy.__class__.__name__} error on {symbol}: {e}")

    return {
        **state,
        "signals": signals,
        "reasoning": state["reasoning"] + [
            f"{len(signals)}/6 strategies fired: "
            f"{[s.strategy for s in signals]}"
        ],
    }


# def executor_node(state: TradingState) -> TradingState:
#     meta = state.get("metadata", {})
#     pos  = meta.get("max_position_pct")
#     print(f"\n{'='*50}")
#     print(f"  SYMBOL    : {state['symbol']}")
#     print(f"  DECISION  : {state['final_decision']}")
#     print(f"  CONFIDENCE: {meta.get('supervisor_confidence', 'N/A')}")
#     print(f"  ENTRY     : {meta.get('suggested_entry', 'N/A')}")
#     print(f"  TIMEFRAME : {meta.get('timeframe', 'N/A')}")
#     if isinstance(pos, float):
#         print(f"  POSITION  : {pos*100:.1f}% of portfolio")
#     print(f"  SIGNALS   : {[s.strategy for s in state['signals']]}")
#     for r in state.get("reasoning", []):
#         print(f"  › {r}")
#     print(f"{'='*50}\n")
#     # clear cache for this symbol — free memory
#     df_cache.clear(state["symbol"])
#     return state

from brokers.angleone.executor import AngelOneExecutor
from orchestrator import df_cache
from agents.historical_agent import HistoricalAgent

# Shared client — already logged in via HistoricalAgent
_executor: AngelOneExecutor = None

def _get_executor() -> AngelOneExecutor:
    global _executor
    if _executor is None:
        client   = HistoricalAgent().client   # reuse logged-in client
        _executor = AngelOneExecutor(client)
    return _executor


def executor_node(state: TradingState) -> TradingState:
    decision = state.get("final_decision")
    meta     = state.get("metadata", {})
    signals  = state.get("signals", [])
    symbol   = state["symbol"]

    # ── Get close price from cache
    df    = df_cache.retrieve(symbol)
    close = float(df["Close"].iloc[-1]) if df is not None else 0.0

    # ── Get lot size from FNO list if available
    try:
        fno   = __import__("pandas").read_csv("data/FNO_LST_190.csv",
                            usecols=["Script", "LOTSIZ"])
        fno   = fno.dropna(subset=["Script", "LOTSIZ"])
        fno   = fno.set_index("Script")
        lot   = int(fno.loc[symbol, "LOTSIZ"]) if symbol in fno.index else 1
    except Exception:
        lot   = 1

    # ── Position sizing from risk manager
    max_pos_pct = meta.get("max_position_pct", 0.02)

    executor = _get_executor()
    result   = executor.execute(
        symbol      = symbol,
        signal      = decision,
        close_price = close,
        lot_size    = lot,
        sl_pct      = 0.03,
    )

    # ── Log order to signals DB
    from orchestrator.logger import log_order
    log_order(symbol, decision, result)

    # ── Clear df cache — free memory
    df_cache.clear(symbol)

    return {
        **state,
        "reasoning": state.get("reasoning", []) + [
            f"Executor: {result['status']} order {result.get('order_id', '')} @ ₹{close:.2f}"
        ],
        "metadata": {**meta, "order": result},
    }

def trade_llm_node(state: TradingState) -> TradingState:
    """
    Run TradeLLM only when at least 1 rule-based signal fired.
    Skips entirely if no signals — saves ~270 wasted LLM calls per scan.
    """
    if not state["signals"]:
        return state    # nothing fired — skip LLM entirely

    df     = df_cache.retrieve(state["symbol"])
    symbol = state["symbol"]

    try:
        signal = trade_llm.evaluate(df, symbol)
        if signal:
            print(f"  ✓ TradeLLM: {signal.signal} (conf={signal.confidence})")
            return {
                **state,
                "signals": state["signals"] + [signal],
                "reasoning": state["reasoning"] + [
                    f"TradeLLM confirmed: {signal.signal}"
                ],
            }
    except Exception as e:
        print(f"  ✗ TradeLLM error on {symbol}: {e}")

    return state    # TradeLLM failed or gave NO_TRADE — keep existing signals