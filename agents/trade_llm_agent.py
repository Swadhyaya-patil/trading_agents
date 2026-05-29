import json
import pandas as pd
from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal
from shared.llm import get_chain, safe_invoke


TRADE_LLM_PROMPT = """You are a JSON-only trading analysis API.
You ONLY output a single JSON object. No explanations, no markdown, no text.
If you write anything other than a JSON object, you have failed.

Analyze the market data and output ONLY this JSON:
{"decision": "BUY", "confidence": 75, "trend": "brief", "momentum": "brief", "final_reason": "1 sentence"}"""

_trade_llm_chain = None


def _get_chain():
    global _trade_llm_chain
    if _trade_llm_chain is None:
        _trade_llm_chain = get_chain(TRADE_LLM_PROMPT)
    return _trade_llm_chain


WINDOW = 21

COLS = [
    "Date", "Open", "High", "Low", "Close", "Volume",
    "EMA_21", "EMA_51", "SMA_50", "SMA_200",
    "MACD", "MACD_signal", "MACD_Histogram", "MACD_Cross_Flag",
    "%K_L", "%D_L", "%K_S", "%D_S",
    "RSI", "ADX", "Williams_%R", "Momentum_10",
    "ATR_pct", "BB_WIDTH", "BB_POSITION",
    "Vol_Ratio", "OBV_pct_change_3", "CMF",
    "Body", "Wick", "Body_Wick_Ratio",
    "Close_Range_Position", "Day_Trend",
]


class TradeLLMAgent(BaseStrategy):

    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if df is None or len(df) < WINDOW:
            return None

        try:
            result = self._call_llm(df, symbol)
        except Exception as e:
            print(f"  [TradeLLM] error on {symbol}: {e}")
            return None

        if result is None:
            return None

        decision   = result.get("decision", "NO_TRADE").upper()
        confidence = float(result.get("confidence", 0)) / 100.0

        if decision == "NO_TRADE" or confidence < 0.55:
            return None

        return StrategySignal(
            strategy="TradeLLM",
            symbol=symbol,
            signal="BUY" if decision == "BUY" else "SELL",
            confidence=round(confidence, 2),
            reasoning=[
                f"Trend: {result.get('trend', 'N/A')}",
                f"Momentum: {result.get('momentum', 'N/A')}",
                f"Conclusion: {result.get('final_reason', 'N/A')}",
            ],
            metadata={
                "llm_decision":   decision,
                "llm_confidence": result.get("confidence"),
            },
        )

    def _call_llm(self, df, symbol: str) -> dict | None:
        available = [c for c in COLS if c in df.columns]
        df_slice  = df[available].tail(WINDOW).copy()

        if "Date" in df_slice.columns:
            df_slice["Date"] = pd.to_datetime(
                df_slice["Date"]).dt.strftime("%Y-%m-%d")

        num_cols = df_slice.select_dtypes(include="number").columns
        df_slice[num_cols] = df_slice[num_cols].round(3)

        data_str   = json.dumps(df_slice.to_dict(orient="records"))
        input_text = f"Symbol: {symbol}\n\n{data_str}"

        try:
            return safe_invoke(_get_chain(), input_text)
        except Exception as e:
            print(f"  [TradeLLM] safe_invoke failed for {symbol}: {e}")
            return None
