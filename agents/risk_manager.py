from shared.llm import get_chain, safe_invoke
from shared.state import TradingState
from shared.models import StrategySignal
from orchestrator import df_cache


RISK_SYSTEM_PROMPT = """You are a JSON-only risk assessment API.
You ONLY output a single JSON object. No explanations, no markdown, no text before or after.

Evaluate trading risk for Indian NSE equity. Apply these rules:
- Reject if fewer than 2 strategies agree (unless MLModel or TradeLLM fired)
- Reject if avg confidence < 0.65
- Reject if ATR_pct > 0.04
- Approve if 1+ model-based signal (MLModel/TradeLLM) OR 2+ rule-based signals

OUTPUT FORMAT (output ONLY this JSON, nothing else):
{{"approved": true, "reason": "one sentence", "max_position_pct": 0.02}}"""


risk_chain = get_chain(RISK_SYSTEM_PROMPT)


def _signals(state: TradingState) -> list[StrategySignal]:
    """Rebuild StrategySignal objects from the dict list stored in state."""
    return [StrategySignal(**d) for d in state.get("signals", [])]


def build_risk_summary(state: TradingState) -> str:
    signals = _signals(state)
    df      = df_cache.retrieve(state["symbol"])

    if df is None or len(df) == 0:
        market_context = "No market data available"
    else:
        tail = df.tail(3).copy()

        def row_summary(r) -> str:
            return (
                f"  Date={str(r.get('Date','?'))[:10]}  "
                f"Close={r.get('Close',0):.2f}  "
                f"EMA21={r.get('EMA_21',0):.2f}  EMA51={r.get('EMA_51',0):.2f}  "
                f"SMA50_dist={r.get('SMA_50_dist',0):.3f}  SMA200_dist={r.get('SMA_200_dist',0):.3f}  "
                f"Day_Trend={r.get('Day_Trend','?')}  "
                f"RSI={r.get('RSI',0):.1f}  Williams%R={r.get('Williams_%R',0):.1f}  "
                f"Momentum10={r.get('Momentum_10',0):.3f}  "
                f"KL={r.get('%K_L',0):.1f}/%DL={r.get('%D_L',0):.1f}  "
                f"MACD={r.get('MACD',0):.3f}  MACDsig={r.get('MACD_signal',0):.3f}  "
                f"MACDhist={r.get('MACD_Histogram',0):.3f}  MACDcross={r.get('MACD_Cross_Flag','?')}  "
                f"ATR%={r.get('ATR_pct',0):.4f}  BB_WIDTH={r.get('BB_WIDTH',0):.4f}  "
                f"BB_POS={r.get('BB_POSITION',0):.2f}  "
                f"Vol_Ratio={r.get('Vol_Ratio',0):.2f}  CMF={r.get('CMF',0):.3f}  "
                f"MFI={r.get('MFI',0):.1f}  OBV_pct3={r.get('OBV_pct_change_3',0):.3f}  "
                f"ADX={r.get('ADX',0):.1f}  "
                f"Ret1={r.get('Return_1',0):.3f}  Ret3={r.get('Return_3',0):.3f}  "
                f"Ret7={r.get('Return_7',0):.3f}  "
                f"Body={r.get('Body',0):.2f}  Wick={r.get('Wick',0):.2f}  "
                f"CloseRange={r.get('Close_Range_Position',0):.2f}"
            )

        rows           = [row_summary(tail.iloc[i].to_dict()) for i in range(len(tail))]
        market_context = "\n".join(rows)

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
    if not state.get("signals"):
        return {
            **state,
            "risk_approved": False,
            "reasoning": state.get("reasoning", []) + ["Risk: no signals to evaluate"],
        }

    summary = build_risk_summary(state)

    try:
        result   = safe_invoke(risk_chain, summary)
        approved = result.get("approved", False)
        reason   = result.get("reason", "no reason given")
        max_pos  = result.get("max_position_pct", 0.02)
    except Exception as e:
        print(f"Risk manager LLM error: {e}")
        approved = False
        reason   = f"LLM error: {e}"
        max_pos  = 0.0

    return {
        **state,
        "risk_approved": approved,
        "reasoning": state.get("reasoning", []) + [f"Risk: {reason}"],
        "messages": state.get("messages", []) + [
            {"role": "assistant", "content": f"[risk_manager] {reason}"}
        ],
        "metadata": {
            **state.get("metadata", {}),
            "max_position_pct": max_pos,
        },
    }
