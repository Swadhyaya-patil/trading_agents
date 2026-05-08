from shared.state import TradingState
from agents.historical_agent import HistoricalAgent
from agents.strategies.momentum_agent import MomentumAgent
from agents.strategies.breakout_agent import BreakoutAgent
from agents.strategies.stochastic_agent import StochasticAgent

historical_agent = HistoricalAgent()
strategies = [MomentumAgent(), BreakoutAgent(), StochasticAgent()]

# Node 1 — fetch and enrich data
def data_fetcher_node(state: TradingState) -> TradingState:
    df = historical_agent.get_market_data(
        symbol=state["symbol"],
        interval=state["interval"],
        from_date=state["from_date"],
        to_date=state["to_date"],
    )
    return {**state, "df": df, "reasoning": ["Data fetched and enriched"]}

# Node 2 — run all strategy agents
def strategy_node(state: TradingState) -> TradingState:
    df = state["df"]
    symbol = state["symbol"]
    signals = []

    for strategy in strategies:
        try:
            signal = strategy.evaluate(df, symbol)
            if signal:
                signals.append(signal)
        except Exception as e:
            print(f"Strategy error: {e}")

    return {**state, "signals": signals, "reasoning": state["reasoning"] + [f"{len(signals)} signals fired"]}

# Node 3 — Ollama risk manager (next step — see below)
def risk_manager_node(state: TradingState) -> TradingState:
    # placeholder until Ollama is wired in
    approved = len(state["signals"]) >= 2
    return {**state, "risk_approved": approved}

# Node 4 — Ollama supervisor (next step)
def supervisor_node(state: TradingState) -> TradingState:
    if not state["risk_approved"]:
        return {**state, "final_decision": "HOLD", "reasoning": state["reasoning"] + ["Risk rejected"]}
    signals = state["signals"]
    buys = [s for s in signals if s.signal == "BUY"]
    decision = "BUY" if len(buys) >= 2 else "HOLD"
    return {**state, "final_decision": decision}

# Node 5 — executor (paper trading for now)
def executor_node(state: TradingState) -> TradingState:
    print(f"\n{'='*40}")
    print(f"DECISION: {state['final_decision']} — {state['symbol']}")
    print(f"Signals: {[s.strategy for s in state['signals']]}")
    print(f"{'='*40}\n")
    return state