from shared.llm import get_chain, safe_invoke
from shared.state import TradingState
from shared.models import StrategySignal
from orchestrator import df_cache


SUPERVISOR_SYSTEM_PROMPT = """You are a JSON-only trading decision API.
You ONLY output a single JSON object. No explanations, no markdown, no text before or after.

Make final BUY/SELL/HOLD decision for Indian NSE equity swing trade.

OUTPUT FORMAT (output ONLY this JSON, nothing else):
{{"decision": "BUY", "confidence": 0.82, "trend": "brief", "momentum": "brief", "final_reason": "1 sentence", "suggested_entry": "at market", "timeframe": "swing"}}"""


supervisor_chain = get_chain(SUPERVISOR_SYSTEM_PROMPT)


def _signals(state: TradingState) -> list[StrategySignal]:
    """Rebuild StrategySignal objects from the dict list stored in state."""
    return [StrategySignal(**d) for d in state.get("signals", [])]


def build_supervisor_summary(state: TradingState) -> str:
    signals = _signals(state)
    df      = df_cache.retrieve(state["symbol"])
    latest  = df.iloc[-1].to_dict() if df is not None and len(df) > 0 else {}

    signal_summary = "\n".join([
        f"  - {s.strategy}: {s.signal} (conf={s.confidence:.2f})"
        f" | {s.reasoning[0] if s.reasoning else ''}"
        for s in signals
    ])

    avg_conf   = sum(s.confidence for s in signals) / len(signals) if signals else 0
    risk_notes = "\n".join(state.get("reasoning", []))

    trend = (
        f"EMA21={latest.get('EMA_21',0):.2f} EMA51={latest.get('EMA_51',0):.2f} "
        f"SMA50_dist={latest.get('SMA_50_dist',0):.3f} "
        f"SMA200_dist={latest.get('SMA_200_dist',0):.3f} "
        f"Day_Trend={latest.get('Day_Trend','?')}"
    )
    momentum = (
        f"RSI={latest.get('RSI',0):.1f} Williams%R={latest.get('Williams_%R',0):.1f} "
        f"KL={latest.get('%K_L',0):.1f} DL={latest.get('%D_L',0):.1f} "
        f"Momentum10={latest.get('Momentum_10',0):.3f}"
    )
    macd = (
        f"MACD={latest.get('MACD',0):.3f} Signal={latest.get('MACD_signal',0):.3f} "
        f"Hist={latest.get('MACD_Histogram',0):.3f} Cross={latest.get('MACD_Cross_Flag','?')}"
    )
    volatility = (
        f"ATR%={latest.get('ATR_pct',0):.4f} BB_WIDTH={latest.get('BB_WIDTH',0):.4f} "
        f"BB_POS={latest.get('BB_POSITION',0):.2f} "
        f"Volatility21={latest.get('Volatility_21',0):.4f}"
    )
    volume = (
        f"Vol_Ratio={latest.get('Vol_Ratio',0):.2f} CMF={latest.get('CMF',0):.3f} "
        f"MFI={latest.get('MFI',0):.1f} OBV_pct3={latest.get('OBV_pct_change_3',0):.3f} "
        f"ADX={latest.get('ADX',0):.1f}"
    )
    returns = (
        f"Ret1={latest.get('Return_1',0):.3f} "
        f"Ret3={latest.get('Return_3',0):.3f} "
        f"Ret7={latest.get('Return_7',0):.3f}"
    )

    return f"""
Symbol: {state['symbol']}
Risk approved: {state['risk_approved']}
Max position: {state.get('metadata', {}).get('max_position_pct', 'N/A')}

Strategy signals:
{signal_summary}
Average confidence: {avg_conf:.2f}

Market context (latest bar):
  Trend      : {trend}
  Momentum   : {momentum}
  MACD       : {macd}
  Volatility : {volatility}
  Volume     : {volume}
  Returns    : {returns}
  Close      : {latest.get('Close', 'N/A')}

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
        result     = safe_invoke(supervisor_chain, summary)
        decision   = result.get("decision", "HOLD").upper()
        confidence = result.get("confidence", 0.0)
        reasoning  = result.get("final_reason", result.get("reasoning", ""))
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
