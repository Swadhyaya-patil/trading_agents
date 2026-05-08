from shared.llm import get_chain
from shared.state import TradingState

RISK_SYSTEM_PROMPT = """
You are a strict risk manager for an Indian equity trading system (NSE/FNO).
You will receive a summary of strategy signals for a stock.

Your job is to decide whether it is SAFE to proceed with a trade.

Rules you must enforce:
- Reject if fewer than 2 strategies agree
- Reject if confidence average is below 0.70
- Reject if the stock is highly volatile (ATR_pct > 0.04)
- Reject if volume ratio is below 1.2 (low conviction)

Respond ONLY with valid JSON in this exact format, no explanation outside JSON:
{
  "approved": true or false,
  "reason": "one sentence explaining the decision",
  "max_position_pct": 0.02
}

max_position_pct is the maximum % of portfolio to risk on this trade (between 0.01 and 0.05).
"""

def build_risk_summary(state: TradingState) -> str:
    signals = state["signals"]
    df = state["df"]
    latest = df.iloc[-1] if df is not None else {}

    signal_lines = "\n".join([
        f"  - {s.strategy}: {s.signal}, confidence={s.confidence:.2f}, reasoning={s.reasoning}"
        for s in signals
    ])

    return f"""
Symbol: {state['symbol']}
Number of signals fired: {len(signals)}
Signals:
{signal_lines}

Latest market data:
  - ATR_pct (volatility): {latest.get('ATR_pct', 'N/A')}
  - Vol_Ratio (volume vs 21d avg): {latest.get('Vol_Ratio', 'N/A')}
  - RSI: {latest.get('RSI', 'N/A')}
  - BB_WIDTH: {latest.get('BB_WIDTH', 'N/A')}
"""

risk_chain = get_chain(RISK_SYSTEM_PROMPT)

def risk_manager_node(state: TradingState) -> TradingState:
    if not state["signals"]:
        return {
            **state,
            "risk_approved": False,
            "reasoning": state["reasoning"] + ["Risk: no signals to evaluate"],
        }

    summary = build_risk_summary(state)

    try:
        result = risk_chain.invoke({"input": summary})
        approved = result.get("approved", False)
        reason = result.get("reason", "no reason given")
        max_pos = result.get("max_position_pct", 0.02)
    except Exception as e:
        print(f"Risk manager LLM error: {e}")
        approved = False
        reason = f"LLM error: {e}"
        max_pos = 0.0

    return {
        **state,
        "risk_approved": approved,
        "reasoning": state["reasoning"] + [f"Risk: {reason}"],
        "messages": state["messages"] + [{"role": "risk_manager", "content": reason}],
        "metadata": {
            **state.get("metadata", {}),
            "max_position_pct": max_pos
        },
    }