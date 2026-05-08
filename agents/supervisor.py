# from shared.llm import get_chain
# from shared.state import TradingState
# from orchestrator import df_cache


# SUPERVISOR_SYSTEM_PROMPT = """
# You are a senior portfolio manager for an Indian equity trading desk.
# You will receive approved risk-cleared signals for a stock.

# Your job is to make the FINAL trading decision.

# Consider:
# - Signal agreement across strategies (more = better)
# - Average confidence score
# - Market context (RSI, MACD, trend)
# - Whether the risk manager approved and what position size was allowed

# Respond ONLY with valid JSON in this exact format, no text outside the JSON:
# {
#   "decision": "BUY" or "HOLD" or "SELL",
#   "confidence": 0.0 to 1.0,
#   "reasoning": "2-3 sentence explanation",
#   "suggested_entry": "at market" or "wait for pullback",
#   "timeframe": "intraday" or "swing" or "positional"
# }

# Be conservative. Prefer HOLD when signals are mixed.
# """

# def build_supervisor_summary(state: TradingState) -> str:
#     signals = state["signals"]
#     # df = state["df"]    latest = df.iloc[-1] if df is not None else {}
#     df     = df_cache.retrieve(state["symbol"])     # ← from cache

#     signal_summary = "\n".join([
#         f"  - {s.strategy}: {s.signal} (conf={s.confidence:.2f})"
#         for s in signals
#     ])

#     avg_conf = sum(s.confidence for s in signals) / len(signals) if signals else 0
#     risk_notes = "\n".join(state.get("reasoning", []))

#     return f"""
# Symbol: {state['symbol']}
# Risk approved: {state['risk_approved']}
# Max position size: {state.get('metadata', {}).get('max_position_pct', 'N/A')}

# Strategy signals:
# {signal_summary}
# Average confidence: {avg_conf:.2f}

# Key indicators:
#   - RSI: {latest.get('RSI', 'N/A')}
#   - MACD: {latest.get('MACD', 'N/A')} vs signal {latest.get('MACD_signal', 'N/A')}
#   - EMA_21 vs EMA_51: {latest.get('EMA_21', 'N/A')} vs {latest.get('EMA_51', 'N/A')}
#   - ADX (trend strength): {latest.get('ADX', 'N/A')}
#   - Close: {latest.get('Close', 'N/A')}

# Risk manager notes:
# {risk_notes}
# """

# supervisor_chain = get_chain(SUPERVISOR_SYSTEM_PROMPT)

# def supervisor_node(state: TradingState) -> TradingState:
#     if not state["risk_approved"]:
#         return {
#             **state,
#             "final_decision": "HOLD",
#             "reasoning": state["reasoning"] + ["Supervisor: risk not approved, holding"],
#         }

#     summary = build_supervisor_summary(state)

#     try:
#         result = supervisor_chain.invoke({"input": summary})
#         decision = result.get("decision", "HOLD").upper()
#         confidence = result.get("confidence", 0.0)
#         reasoning = result.get("reasoning", "")
#         entry = result.get("suggested_entry", "at market")
#         timeframe = result.get("timeframe", "swing")
#     except Exception as e:
#         print(f"Supervisor LLM error: {e}")
#         decision = "HOLD"
#         confidence = 0.0
#         reasoning = f"LLM error: {e}"
#         entry = "N/A"
#         timeframe = "N/A"

#     return {
#         **state,
#         "final_decision": decision,
#         "reasoning": state["reasoning"] + [f"Supervisor: {reasoning}"],
#         "messages": state["messages"] + [{"role": "supervisor", "content": reasoning}],
#         "metadata": {
#             **state.get("metadata", {}),
#             "supervisor_confidence": confidence,
#             "suggested_entry": entry,
#             "timeframe": timeframe,
#         },
#     }








from shared.llm import get_chain, safe_invoke
from shared.state import TradingState
from orchestrator import df_cache

# NOTE: all { } in the JSON example are doubled {{ }} to avoid
# LangChain treating them as prompt template variables
SUPERVISOR_SYSTEM_PROMPT = """
You are a senior portfolio manager for an Indian equity trading desk.
You will receive risk-cleared signals for a stock.

Your job is to make the FINAL trading decision.

Consider:
- Signal agreement across strategies (more = better)
- Average confidence score
- Market context (RSI, MACD, trend)
- Whether the risk manager approved and what position size was allowed

Respond ONLY with valid JSON in this exact format, no text outside the JSON:
{{
  "decision": "BUY" or "HOLD" or "SELL",
  "confidence": 0.0 to 1.0,
  "reasoning": "2-3 sentence explanation",
  "suggested_entry": "at market" or "wait for pullback",
  "timeframe": "intraday" or "swing" or "positional"
}}

Be conservative. Prefer HOLD when signals are mixed.
"""

supervisor_chain = get_chain(SUPERVISOR_SYSTEM_PROMPT)


def build_supervisor_summary(state: TradingState) -> str:
    signals = state["signals"]
    df      = df_cache.retrieve(state["symbol"])
    latest  = df.iloc[-1].to_dict() if df is not None and len(df) > 0 else {}

    signal_summary = "\n".join([
        f"  - {s.strategy}: {s.signal} (conf={s.confidence:.2f})"
        for s in signals
    ])

    avg_conf   = sum(s.confidence for s in signals) / len(signals) if signals else 0
    risk_notes = "\n".join(state.get("reasoning", []))

    return f"""
Symbol: {state['symbol']}
Risk approved: {state['risk_approved']}
Max position size: {state.get('metadata', {}).get('max_position_pct', 'N/A')}

Strategy signals:
{signal_summary}
Average confidence: {avg_conf:.2f}

Key indicators:
  - RSI: {latest.get('RSI', 'N/A')}
  - MACD: {latest.get('MACD', 'N/A')} vs signal {latest.get('MACD_signal', 'N/A')}
  - EMA_21 vs EMA_51: {latest.get('EMA_21', 'N/A')} vs {latest.get('EMA_51', 'N/A')}
  - ADX (trend strength): {latest.get('ADX', 'N/A')}
  - Close: {latest.get('Close', 'N/A')}

Risk manager notes:
{risk_notes}
"""


def supervisor_node(state: TradingState) -> TradingState:
    if not state["risk_approved"]:
        return {
            **state,
            "final_decision": "HOLD",
            "reasoning": state.get("reasoning", []) + [
                "Supervisor: risk not approved, holding"
            ],
        }

    summary = build_supervisor_summary(state)

    try:
        result    = safe_invoke(supervisor_chain, summary)
        decision  = result.get("decision", "HOLD").upper()
        confidence = result.get("confidence", 0.0)
        reasoning  = result.get("reasoning", "")
        entry      = result.get("suggested_entry", "at market")
        timeframe  = result.get("timeframe", "swing")
    except Exception as e:
        print(f"Supervisor LLM error: {e}")
        decision   = "HOLD"
        confidence = 0.0
        reasoning  = f"LLM error: {e}"
        entry      = "N/A"
        timeframe  = "N/A"

    return {
        **state,
        "final_decision": decision,
        "reasoning": state.get("reasoning", []) + [f"Supervisor: {reasoning}"],
        # ← role must be 'assistant', NOT 'supervisor'
        "messages": state.get("messages", []) + [
            {"role": "assistant", "content": f"[supervisor] {reasoning}"}
        ],
        "metadata": {
            **state.get("metadata", {}),
            "supervisor_confidence": confidence,
            "suggested_entry":       entry,
            "timeframe":             timeframe,
        },
    }