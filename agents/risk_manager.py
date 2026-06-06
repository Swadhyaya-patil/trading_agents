from shared.llm import get_chain, safe_invoke
from shared.state import TradingState
from shared.models import StrategySignal
from orchestrator import df_cache


# RISK_SYSTEM_PROMPT = """You are a JSON-only risk assessment API.
# You ONLY output a single JSON object. No explanations, no markdown, no text before or after.

# Evaluate trading risk for Indian NSE equity. Apply these rules:
# - Reject if fewer than 2 strategies agree (unless MLModel or TradeLLM fired)
# - Reject if avg confidence < 0.65
# - Reject if ATR_pct > 0.04
# - Approve if 1+ model-based signal (MLModel/TradeLLM) OR 2+ rule-based signals

# OUTPUT FORMAT (output ONLY this JSON, nothing else):
# {{"approved": true, "reason": "one sentence", "max_position_pct": 0.02}}"""

RISK_SYSTEM_PROMPT = """
You are a JSON-only risk assessment API for Indian NSE equities.

IMPORTANT:

* Output ONLY a single valid JSON object.
* No markdown.
* No explanations.
* No additional text.

Your job is NOT to generate trading signals.
Your job is to evaluate whether an existing BUY signal is strong enough to risk capital.

Available Inputs:

SIGNAL DATA:

* strategy_votes
* strategy_names
* avg_confidence
* MLModel_signal
* TradeLLM_signal

TREND:

* Price_EMA_21_Ratio
* Price_EMA_51_Ratio
* EMA_21_minus_EMA_51
* SMA_50_dist
* SMA_200_dist
* Day_Trend
* ADX

MOMENTUM:

* MACD
* MACD_signal
* MACD_Histogram
* MACD_Cross_Flag
* RSI
* Williams_%R
* CCI
* Momentum_10
* Aroon_Oscillator

VOLUME:

* Vol_Ratio
* CMF
* OBV_pct_change_3
* MFI
* VWAP

VOLATILITY:

* ATR_pct
* ATR
* Volatility_21
* BB_WIDTH
* Donchian_Width

MARKET STRUCTURE:

* Close_Range_Position
* BB_POSITION
* Parabolic_SAR

RISK EVALUATION RULES

====================
HARD REJECTION RULES
====================

Reject immediately if ANY of the following are true:

1. strategy_votes < 2
   AND MLModel_signal is false
   AND TradeLLM_signal is false

2. avg_confidence < 0.65

3. ATR_pct > 0.04

4. ADX < 15

5. EMA_21_minus_EMA_51 < 0

6. MACD_Histogram < 0

7. Vol_Ratio < 0.70

8. RSI > 85

9. Close_Range_Position > 0.98
   AND RSI > 80

====================
SCORING RULES
=============

Start score = 0

TREND:

+2 if EMA_21_minus_EMA_51 > 0
+1 if Price_EMA_21_Ratio > 1
+1 if Price_EMA_51_Ratio > 1
+2 if ADX > 25
+1 if Day_Trend is bullish

MOMENTUM:

+2 if MACD_Histogram > 0
+1 if MACD_Cross_Flag is bullish
+1 if RSI between 50 and 75
+1 if Momentum_10 > 0
+1 if Aroon_Oscillator > 0

VOLUME:

+2 if Vol_Ratio > 1.2
+1 if CMF > 0
+1 if OBV_pct_change_3 > 0
+1 if MFI between 50 and 80

MODEL CONFIRMATION:

+3 if MLModel_signal is true
+3 if TradeLLM_signal is true

====================
APPROVAL RULES
==============

APPROVE if:

(score >= 8)
AND
(
MLModel_signal is true
OR
TradeLLM_signal is true
OR
strategy_votes >= 2
)

Otherwise reject.

====================
POSITION SIZING
===============

If approved:

score >= 14:
max_position_pct = 0.02

score between 11 and 13:
max_position_pct = 0.015

score between 8 and 10:
max_position_pct = 0.01

If rejected:
max_position_pct = 0

====================
OUTPUT FORMAT
=============

{
"approved": true,
"reason": "short concise reason",
"max_position_pct": 0.02
}

The reason must be a single sentence summarizing the strongest approval or rejection factor.
"""


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
