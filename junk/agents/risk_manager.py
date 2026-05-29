# from shared.llm import get_chain
# from shared.state import TradingState
# from orchestrator import df_cache

# # RISK_SYSTEM_PROMPT = """
# # You are a strict risk manager for an Indian equity trading system (NSE/FNO).
# # You will receive a summary of strategy signals for a stock.

# # Your job is to decide whether it is SAFE to proceed with a trade.

# # Rules you must enforce:
# # - Reject if fewer than 2 strategies agree
# # - Reject if confidence average is below 0.70
# # - Reject if the stock is highly volatile (ATR_pct > 0.04)
# # - Reject if volume ratio is below 1.2 (low conviction)

# # Respond ONLY with valid JSON in this exact format, no explanation outside JSON:
# # {
# #   "approved": true or false,
# #   "reason": "one sentence explaining the decision",
# #   "max_position_pct": 0.02
# # }

# # max_position_pct is the maximum % of portfolio to risk on this trade (between 0.01 and 0.05).
# # """

# RISK_SYSTEM_PROMPT = """
# You are a strict risk manager for an Indian equity trading system (NSE/FNO).
# You will receive a summary of strategy signals for a stock.

# Your job is to decide whether it is SAFE to proceed with a trade.

# Rules you must enforce:
# - Reject if fewer than 2 strategies agree
# - Reject if confidence average is below 0.70
# - Reject if the stock is highly volatile (ATR_pct > 0.04)
# - Reject if volume ratio is below 1.2 (low conviction)

# Respond ONLY with valid JSON in this exact format, no explanation outside JSON:
# {{
#   "approved": true or false,
#   "reason": "one sentence explaining the decision",
#   "max_position_pct": 0.02
# }}

# max_position_pct is the maximum % of portfolio to risk on this trade (between 0.01 and 0.05).
# """



# def build_risk_summary(state: TradingState) -> str:
#     signals = state["signals"]
#     # df = state["df"]
#     df      = df_cache.retrieve(state["symbol"])  
#     latest = df.iloc[-1] if df is not None else {}

#     signal_lines = "\n".join([
#         f"  - {s.strategy}: {s.signal}, confidence={s.confidence:.2f}, reasoning={s.reasoning}"
#         for s in signals
#     ])

#     return f"""
# Symbol: {state['symbol']}
# Number of signals fired: {len(signals)}
# Signals:
# {signal_lines}

# Latest market data:
#   - ATR_pct (volatility): {latest.get('ATR_pct', 'N/A')}
#   - Vol_Ratio (volume vs 21d avg): {latest.get('Vol_Ratio', 'N/A')}
#   - RSI: {latest.get('RSI', 'N/A')}
#   - BB_WIDTH: {latest.get('BB_WIDTH', 'N/A')}
# """

# risk_chain = get_chain(RISK_SYSTEM_PROMPT)

# def risk_manager_node(state: TradingState) -> TradingState:
#     if not state["signals"]:
#         return {
#             **state,
#             "risk_approved": False,
#             "reasoning": state["reasoning"] + ["Risk: no signals to evaluate"],
#         }

#     summary = build_risk_summary(state)

#     try:
#         result = risk_chain.invoke({"input": summary})
#         approved = result.get("approved", False)
#         reason = result.get("reason", "no reason given")
#         max_pos = result.get("max_position_pct", 0.02)
#     except Exception as e:
#         print(f"Risk manager LLM error: {e}")
#         approved = False
#         reason = f"LLM error: {e}"
#         max_pos = 0.0

#     return {
#         **state,
#         "risk_approved": approved,
#         "reasoning": state["reasoning"] + [f"Risk: {reason}"],
#         "messages": state["messages"] + [{"role": "risk_manager", "content": reason}],
#         "metadata": {
#             **state.get("metadata", {}),
#             "max_position_pct": max_pos
#         },
#     }












from shared.llm import get_chain, safe_invoke
from shared.state import TradingState
from orchestrator import df_cache

# NOTE: all { } in the JSON example are doubled {{ }} to avoid
# LangChain treating them as prompt template variables
# RISK_SYSTEM_PROMPT = """
# You are a strict risk manager for an Indian equity trading system (NSE/FNO).
# You will receive a summary of strategy signals for a stock.

# Your job is to decide whether it is SAFE to proceed with a trade.

# Rules you must enforce:
# - Reject if fewer than 2 strategies agree
# - Reject if confidence average is below 0.70
# - Reject if the stock is highly volatile (ATR_pct > 0.04)
# - Reject if volume ratio is below 1.2 (low conviction)

# Respond ONLY with valid JSON in this exact format, no explanation outside JSON:
# {{
#   "approved": true or false,
#   "reason": "one sentence explaining the decision",
#   "max_position_pct": 0.02
# }}

# max_position_pct is the maximum percentage of portfolio to risk (between 0.01 and 0.05).
# """

# agents/risk_manager.py — update RISK_SYSTEM_PROMPT

# RISK_SYSTEM_PROMPT = """
# You are a strict risk manager for an Indian equity trading system (NSE/FNO).

# Rules:
# - If MLModel or TradeLLM fires, that counts as 2 strategy agreements (they are model-based, higher quality)
# - Reject if average confidence is below 0.65
# - Reject if ATR_pct > 0.04 (too volatile)
# - Reject if Vol_Ratio < 1.0 (low conviction)
# - Approve if 1+ model-based signal (MLModel/TradeLLM) OR 2+ rule-based signals agree

# Respond ONLY with valid JSON:
# {{
#   "approved": true or false,
#   "reason": "one sentence",
#   "max_position_pct": 0.02
# }}
# """


RISK_SYSTEM_PROMPT = """You are a JSON-only risk assessment API. 
You ONLY output a single JSON object. No explanations, no markdown, no text before or after.
If you write anything other than a JSON object, you have failed.

Evaluate trading risk for Indian NSE equity. Apply these rules:
- Reject if fewer than 2 strategies agree (unless MLModel or TradeLLM fired)
- Reject if avg confidence < 0.65
- Reject if ATR_pct > 0.04
- Approve if 1+ model-based signal (MLModel/TradeLLM) OR 2+ rule-based signals

OUTPUT FORMAT — exactly this, nothing else:
{{"approved": true, "reason": "one sentence", "max_position_pct": 0.02}}"""


risk_chain = get_chain(RISK_SYSTEM_PROMPT)


# def build_risk_summary(state: TradingState) -> str:
#     signals = state["signals"]
#     df      = df_cache.retrieve(state["symbol"])
#     latest  = df.iloc[-1].to_dict() if df is not None and len(df) > 0 else {}

#     signal_lines = "\n".join([
#         f"  - {s.strategy}: {s.signal}, confidence={s.confidence:.2f}, reasoning={s.reasoning}"
#         for s in signals
#     ])

#     return f"""
# Symbol: {state['symbol']}
# Number of signals fired: {len(signals)}
# Signals:
# {signal_lines}

# Latest market data:
#   - ATR_pct (volatility): {latest.get('ATR_pct', 'N/A')}
#   - Vol_Ratio (volume vs 21d avg): {latest.get('Vol_Ratio', 'N/A')}
#   - RSI: {latest.get('RSI', 'N/A')}
#   - BB_WIDTH: {latest.get('BB_WIDTH', 'N/A')}
# """

# # agents/risk_manager.py — in build_risk_summary()
# def build_risk_summary(state: TradingState) -> str:
#     signals  = state["signals"]
#     df       = df_cache.retrieve(state["symbol"])
#     latest   = df.iloc[-1].to_dict() if df is not None and len(df) > 0 else {}

#     signal_lines = "\n".join([
#         f"  - {s.strategy} ({'MODEL-BASED' if s.strategy in ('MLModel','TradeLLM') else 'rule-based'})"
#         f": {s.signal}, confidence={s.confidence:.2f}"
#         for s in signals
#     ])

#     buy_count  = sum(1 for s in signals if s.signal == "BUY")
#     sell_count = sum(1 for s in signals if s.signal == "SELL")
#     model_signals = [s for s in signals if s.strategy in ("MLModel", "TradeLLM")]

#     return f"""
# Symbol: {state['symbol']}
# Total signals: {len(signals)} ({buy_count} BUY, {sell_count} SELL)
# Model-based signals: {len(model_signals)}
# Signals:
# {signal_lines}

# Latest market data:
#   - ATR_pct: {latest.get('ATR_pct', 'N/A')}
#   - Vol_Ratio: {latest.get('Vol_Ratio', 'N/A')}
#   - RSI: {latest.get('RSI', 'N/A')}
#   - BB_WIDTH: {latest.get('BB_WIDTH', 'N/A')}
# """

def build_risk_summary(state: TradingState) -> str:
    signals = state["signals"]
    df      = df_cache.retrieve(state["symbol"])

    if df is None or len(df) == 0:
        market_context = "No market data available"
    else:
        # Use last 3 rows to show direction, not just snapshot
        tail = df.tail(3).copy()

        def row_summary(r) -> str:
            return (
                f"  Date={str(r.get('Date','?'))[:10]}  "
                f"Close={r.get('Close','?'):.2f}  "

                # Trend
                f"EMA21={r.get('EMA_21','?'):.2f}  "
                f"EMA51={r.get('EMA_51','?'):.2f}  "
                f"SMA50_dist={r.get('SMA_50_dist','?'):.3f}  "
                f"SMA200_dist={r.get('SMA_200_dist','?'):.3f}  "
                f"Day_Trend={r.get('Day_Trend','?')}  "

                # Momentum
                f"RSI={r.get('RSI','?'):.1f}  "
                f"Williams%R={r.get('Williams_%R','?'):.1f}  "
                f"Momentum10={r.get('Momentum_10','?'):.3f}  "
                f"KL={r.get('%K_L','?'):.1f}/%DL={r.get('%D_L','?'):.1f}  "

                # MACD
                f"MACD={r.get('MACD','?'):.3f}  "
                f"MACDsig={r.get('MACD_signal','?'):.3f}  "
                f"MACDhist={r.get('MACD_Histogram','?'):.3f}  "
                f"MACDcross={r.get('MACD_Cross_Flag','?')}  "

                # Volatility
                f"ATR%={r.get('ATR_pct','?'):.4f}  "
                f"BB_WIDTH={r.get('BB_WIDTH','?'):.4f}  "
                f"BB_POS={r.get('BB_POSITION','?'):.2f}  "
                f"Volatility21={r.get('Volatility_21','?'):.4f}  "
                f"Donchian={r.get('Donchian_Width','?'):.4f}  "

                # Volume / money flow
                f"Vol_Ratio={r.get('Vol_Ratio','?'):.2f}  "
                f"CMF={r.get('CMF','?'):.3f}  "
                f"MFI={r.get('MFI','?'):.1f}  "
                f"OBV_pct3={r.get('OBV_pct_change_3','?'):.3f}  "
                f"CCI={r.get('CCI','?'):.1f}  "
                f"ADX={r.get('ADX','?'):.1f}  "

                # Returns
                f"Ret1={r.get('Return_1','?'):.3f}  "
                f"Ret3={r.get('Return_3','?'):.3f}  "
                f"Ret7={r.get('Return_7','?'):.3f}  "

                # Candle strength
                f"Body={r.get('Body','?'):.2f}  "
                f"Wick={r.get('Wick','?'):.2f}  "
                f"BW_Ratio={r.get('Body_Wick_Ratio','?'):.2f}  "
                f"CloseRange={r.get('Close_Range_Position','?'):.2f}"
            )

        rows = [row_summary(tail.iloc[i].to_dict()) for i in range(len(tail))]
        market_context = "\n".join(rows)

    # Signal summary
    buy_count     = sum(1 for s in signals if s.signal == "BUY")
    sell_count    = sum(1 for s in signals if s.signal == "SELL")
    model_signals = [s for s in signals if s.strategy in ("MLModel", "TradeLLM")]

    signal_lines = "\n".join([
        f"  - {s.strategy} "
        f"({'MODEL-BASED' if s.strategy in ('MLModel','TradeLLM') else 'rule-based'})"
        f": {s.signal} conf={s.confidence:.2f} | {' | '.join(s.reasoning[:2])}"
        for s in signals
    ])

    return f"""
Symbol: {state['symbol']}
Signals: {len(signals)} total ({buy_count} BUY, {sell_count} SELL)
Model-based signals: {len(model_signals)}

Signal details:
{signal_lines}

Market data (last 3 bars — oldest to newest):
{market_context}
"""


def risk_manager_node(state: TradingState) -> TradingState:
    if not state["signals"]:
        return {
            **state,
            "risk_approved": False,
            "reasoning": state.get("reasoning", []) + ["Risk: no signals to evaluate"],
        }

    summary = build_risk_summary(state)

    try:
        result      = safe_invoke(risk_chain, summary)
        approved    = result.get("approved", False)
        reason      = result.get("reason", "no reason given")
        max_pos     = result.get("max_position_pct", 0.02)
    except Exception as e:
        print(f"Risk manager LLM error: {e}")
        approved    = False
        reason      = f"LLM error: {e}"
        max_pos     = 0.0

    return {
        **state,
        "risk_approved": approved,
        "reasoning": state.get("reasoning", []) + [f"Risk: {reason}"],
        # ← role must be 'assistant', NOT 'risk_manager'
        "messages": state.get("messages", []) + [
            {"role": "assistant", "content": f"[risk_manager] {reason}"}
        ],
        "metadata": {
            **state.get("metadata", {}),
            "max_position_pct": max_pos,
        },
    }
