# import json
# import pandas as pd
# from agents.strategies.base_strategy import BaseStrategy
# from shared.models import StrategySignal
# from shared.llm import OLLAMA_HOST, OLLAMA_MODEL
# import ollama


# # TRADE_LLM_PROMPT = """
# # You are a quantitative trading model analyzing Indian NSE equity daily candles.

# # Below are the last {n} days of market features for {symbol}.
# # Use them to determine if a trade should be taken tomorrow.

# # DATA:
# # {data}

# # Analyze across these dimensions:
# # 1. Trend      : EMA_21, EMA_51, SMA_50, SMA_200, EMA crosses, Day_Trend
# # 2. Momentum   : RSI, %K_L/%D_L, %K_S/%D_S, Williams_%R, Momentum_10
# # 3. MACD       : MACD, MACD_signal, MACD_Histogram, MACD_Cross_Flag
# # 4. Volatility : ATR_pct, Volatility_21, BB_WIDTH, Donchian_Width
# # 5. Mean Rev   : BB_POSITION, Keltner bands
# # 6. Volume     : Vol_Ratio, OBV_pct_change_3, CMF, VWAP
# # 7. Candles    : Body, Wick, Body_Wick_Ratio, Close_Range_Position
# # 8. Risk       : conflicting signals, volatility regime, false breakout risk

# # Respond ONLY with valid JSON — no markdown, no explanation outside JSON:
# # {{
# #   "decision":     "BUY" | "SELL" | "NO_TRADE",
# #   "confidence":   0-100,
# #   "trend":        "brief text",
# #   "momentum":     "brief text",
# #   "volatility":   "brief text",
# #   "volume":       "brief text",
# #   "final_reason": "1-2 sentence combined explanation"
# # }}
# # """

# TRADE_LLM_PROMPT = """
# You are a trading analyst. Analyze these {n} days of {symbol} market data and respond in JSON only.

# DATA: {data}

# JSON response format:
# {{"decision":"BUY|SELL|NO_TRADE","confidence":0-100,"trend":"brief","momentum":"brief","volatility":"brief","volume":"brief","final_reason":"1 sentence"}}
# """

# class TradeLLMAgent(BaseStrategy):
#     """
#     LLM-based trade analysis using local Ollama.
#     Runs AFTER the ML model fires — acts as a second opinion / confirmation layer.
#     Can run standalone or as part of the LangGraph pipeline.
#     """

#     # How many recent candles to send to the LLM (keep small — context window)
#     WINDOW = 20

#     # Columns to include — enough for analysis, not so many it bloats the prompt
#     COLS = [
#         "Date", "Open", "High", "Low", "Close", "Volume",
#         "EMA_21", "EMA_51", "SMA_50", "SMA_200",
#         "MACD", "MACD_signal", "MACD_Histogram", "MACD_Cross_Flag",
#         "%K_L", "%D_L", "%K_S", "%D_S",
#         "RSI", "ADX", "Williams_%R", "Momentum_10",
#         "ATR_pct", "BB_WIDTH", "BB_POSITION",
#         "Vol_Ratio", "OBV_pct_change_3", "CMF",
#         "Body", "Wick", "Body_Wick_Ratio",
#         "Close_Range_Position", "Day_Trend",
#     ]

#     def __init__(self):
#         self.client = ollama.Client(host=OLLAMA_HOST)
#         self.model  = OLLAMA_MODEL

#     def evaluate(self, df, symbol: str) -> StrategySignal | None:
#         if df is None or len(df) < self.WINDOW:
#             return None

#         result = self._call_llm(df, symbol)
#         if result is None:
#             return None

#         decision   = result.get("decision", "NO_TRADE").upper()
#         confidence = float(result.get("confidence", 0)) / 100.0  # normalise to 0-1

#         if decision == "NO_TRADE" or confidence < 0.55:
#             return None

#         signal = "BUY" if decision == "BUY" else "SELL"

#         return StrategySignal(
#             strategy="TradeLLM",
#             symbol=symbol,
#             signal=signal,
#             confidence=round(confidence, 2),
#             reasoning=[
#                 f"Trend: {result.get('trend', 'N/A')}",
#                 f"Momentum: {result.get('momentum', 'N/A')}",
#                 f"Volatility: {result.get('volatility', 'N/A')}",
#                 f"Volume: {result.get('volume', 'N/A')}",
#                 f"Conclusion: {result.get('final_reason', 'N/A')}",
#             ],
#             metadata={
#                 "llm_decision":   decision,
#                 "llm_confidence": result.get("confidence"),
#                 "llm_model":      self.model,
#             },
#         )

#     def _call_llm(self, df, symbol: str) -> dict | None:
#         # ── Prepare last WINDOW rows, only relevant columns
#         available = [c for c in self.COLS if c in df.columns]
#         # df_slice  = df[available].tail(self.WINDOW).copy()

#         # if "Date" in df_slice.columns:
#         #     df_slice["Date"] = pd.to_datetime(df_slice["Date"]).dt.strftime("%Y-%m-%d")

#         # # Round floats to 4dp to keep prompt compact
#         # df_slice = df_slice.round(4)
#         # data_str = json.dumps(df_slice.to_dict(orient="records"), indent=2)

#         df_slice = df[available].tail(self.WINDOW).copy()

#         if "Date" in df_slice.columns:
#             df_slice["Date"] = pd.to_datetime(
#                 df_slice["Date"]).dt.strftime("%Y-%m-%d")

#         # ── Round only numeric columns — leave strings (Day_Trend etc) alone
#         numeric_cols = df_slice.select_dtypes(include="number").columns
#         df_slice[numeric_cols] = df_slice[numeric_cols].round(3)

#         data_str = json.dumps(df_slice.to_dict(orient="records"))

#         prompt = TRADE_LLM_PROMPT.format(
#             n=len(df_slice),
#             symbol=symbol,
#             data=data_str,
#         )

#         # try:
#         #     response = self.client.chat(
#         #         model=self.model,
#         #         messages=[{"role": "user", "content": prompt}],
#         #         format="json",        # Ollama JSON mode
#         #     )
#         #     content = response["message"]["content"].strip()
#         #     # Strip markdown fences if model ignores format=json
#         #     content = content.lstrip("```json").lstrip("```").rstrip("```").strip()
#         #     return json.loads(content)

#         # except json.JSONDecodeError as e:
#         #     print(f"  [TradeLLM] JSON parse error for {symbol}: {e}")
#         #     return None
#         # except Exception as e:
#         #     print(f"  [TradeLLM] LLM call failed for {symbol}: {e}")
#         #     return None

#         # agents/trade_llm_agent.py — in _call_llm()
#         import time

#         for attempt in range(2):    # try twice before giving up
#             try:
#                 response = self.client.chat(
#                     model=self.model,
#                     messages=[{"role": "user", "content": prompt}],
#                     format="json",
#                     options={
#                         # "num_gpu":     0,
#                         "num_ctx":     4096,
#                         "temperature": 0,
#                         "num_predict": 200,
#                     },
#                 )
#                 content = response["message"]["content"].strip()
#                 content = (content
#                         .lstrip("```json").lstrip("```")
#                         .rstrip("```").strip())

#                 if not content:
#                     print(f"  [TradeLLM] Empty response attempt {attempt+1} for {symbol}")
#                     time.sleep(1)
#                     continue        # retry

#                 return json.loads(content)

#             except json.JSONDecodeError:
#                 print(f"  [TradeLLM] JSON parse error for {symbol}, attempt {attempt+1}")
#                 time.sleep(1)
#                 continue
#             except Exception as e:
#                 print(f"  [TradeLLM] LLM call failed for {symbol}: {e}")
#                 return None

#         return None     # both attempts failed




# agents/trade_llm_agent.py — complete corrected file

import json
import pandas as pd
from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal
from shared.llm import get_chain, safe_invoke

# NO curly-brace placeholders except {{ }} for the JSON example
# TRADE_LLM_PROMPT = """
# You are a quantitative trading analyst for Indian NSE equity markets.
# You will receive recent daily OHLCV and indicator data for a stock.
# Analyze trend (EMAs), momentum (RSI, Stoch), MACD, volatility (ATR, BB), and volume (Vol_Ratio).
# Decide if a trade should be taken tomorrow.

# Respond ONLY with valid JSON — no markdown, no text outside JSON:
# {{
#   "decision": "BUY" or "SELL" or "NO_TRADE",
#   "confidence": 0-100,
#   "trend": "brief text",
#   "momentum": "brief text",
#   "final_reason": "1 sentence"
# }}
# """






# agents/trade_llm_agent.py — replace TRADE_LLM_PROMPT

TRADE_LLM_PROMPT = """You are a JSON-only trading analysis API.
You ONLY output a single JSON object. No explanations, no markdown, no text.
If you write anything other than a JSON object, you have failed.

Analyze the market data and output ONLY this JSON:
{{"decision": "BUY", "confidence": 75, "trend": "brief", "momentum": "brief", "final_reason": "1 sentence"}}"""

_trade_llm_chain = None

def _get_chain():
    global _trade_llm_chain
    if _trade_llm_chain is None:
        _trade_llm_chain = get_chain(TRADE_LLM_PROMPT)
    return _trade_llm_chain


# WINDOW = 5
# COLS = [
#     "Date", "Close", "Volume",
#     "EMA_21", "EMA_51",
#     "MACD", "MACD_signal", "MACD_Histogram",
#     "%K_L", "%D_L", "RSI", "ADX",
#     "ATR_pct", "BB_WIDTH", "Vol_Ratio", "CMF",
# ]

WINDOW = 21

# Columns to include — enough for analysis, not so many it bloats the prompt
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

        data_str = json.dumps(df_slice.to_dict(orient="records"))

        # Everything goes into {input} — no other placeholders
        input_text = f"Symbol: {symbol}\n\n{data_str}"

        try:
            return safe_invoke(_get_chain(), input_text)
        except Exception as e:
            print(f"  [TradeLLM] safe_invoke failed for {symbol}: {e}")
            return None