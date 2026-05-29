# from shared.models import StrategySignal
# class StochasticAgent:

#     def evaluate(self, df, symbol):

#         latest = df.iloc[-1]
#         prev = df.iloc[-2]

#         bullish_cross = (
#             prev['%K_S'] < prev['%D_S']
#             and latest['%K_S'] > latest['%D_S']
#         )

#         oversold = latest['%K_S'] < 25

#         if bullish_cross and oversold:

#             # return {
#             #     "strategy": "stochastic",
#             #     "signal": "BUY",
#             #     "confidence": 0.74
#             # }
#             return StrategySignal(
#                 strategy="stochastic",          # field is 'strategy', not 'strategy_name'
#                 symbol=symbol,
#                 signal="BUY",
#                 confidence=0.89,
#                 reasoning=["Price above 20-day high", "Volume surge", "BB squeeze"],
#                 metadata={}
#             )

#         return None

















from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal


class StochasticAgent(BaseStrategy):
    """
    Ported from original stoch_live.identify_opportunity()
    Uses LONG stochastic (k=34, d=8) crossover + SHORT stoch rising
    + Rising highs + MACD bull + EMA_21 filter
    """

    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if df is None or len(df) < 40:
            return None

        df = df.copy()

        # ── Long stochastic (k=34, d=8, smooth=3) — same params as original
        low_min  = df["Low"].rolling(34).min()
        high_max = df["High"].rolling(34).max()
        df["%KL"] = (df["Close"] - low_min) / (high_max - low_min) * 100
        df["%DL"] = df["%KL"].rolling(8).mean()
        df["%K_L"] = df["%KL"].rolling(8).mean()
        df["%D_L"] = df["%DL"].rolling(3).mean()

        # ── Short stochastic (k=5, d=3, smooth=3)
        low_min  = df["Low"].rolling(5).min()
        high_max = df["High"].rolling(5).max()
        df["%KS"] = (df["Close"] - low_min) / (high_max - low_min) * 100
        df["%DS"] = df["%KS"].rolling(3).mean()
        df["%K_S"] = df["%KS"].rolling(3).mean()
        df["%D_S"] = df["%DS"].rolling(3).mean()

        # ── EMA21 and MACD
        df["EMA_21"] = df["Close"].ewm(span=21).mean()
        exp1 = df["Close"].ewm(span=12, adjust=False).mean()
        exp2 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"]        = exp1 - exp2
        df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

        n = len(df) - 1   # last index

        # ── Step 1: find last Long-stoch bullish crossover (within zone 10–60)
        last_cross = 0
        for i in range(1, n + 1):
            if (df["%K_L"].iloc[i] > df["%D_L"].iloc[i]
                    and df["%K_L"].iloc[i - 1] < df["%D_L"].iloc[i - 1]
                    and 10 < df["%K_L"].iloc[i] < 60):
                last_cross = i

        # ── Step 2: crossover must have happened within last 10 bars
        if last_cross == 0 or last_cross + 10 < n:
            return None

        # ── Step 3: all conditions from original identify_opportunity()
        rising_highs = (
            df["High"].iloc[n]   > df["High"].iloc[n-1]
            and df["High"].iloc[n-1] > df["High"].iloc[n-2]
            and df["High"].iloc[n-2] > df["High"].iloc[n-3]
        )
        short_stoch_rising = (
            df["%K_S"].iloc[n]   > df["%K_S"].iloc[n-1]
            and df["%K_S"].iloc[n-1] > df["%K_S"].iloc[n-2]
            and df["%K_S"].iloc[n-2] > df["%K_S"].iloc[n-3]
        )
        long_stoch_rising = (
            df["%K_L"].iloc[n]   > df["%K_L"].iloc[n-1]
            and df["%K_L"].iloc[n-1] > df["%K_L"].iloc[n-2]
            and df["%K_L"].iloc[n-2] > df["%K_L"].iloc[n-3]
        )
        long_stoch_above_30    = df["%K_L"].iloc[n] > 30
        short_stoch_bull       = df["%K_S"].iloc[n] > df["%D_S"].iloc[n] and df["%K_S"].iloc[n] > 40
        macd_bull              = (df["MACD_signal"].iloc[n] < df["MACD"].iloc[n]
                                  and df["MACD_signal"].iloc[n-1] < df["MACD_signal"].iloc[n])
        above_ema21            = df["EMA_21"].iloc[n-1] < df["Close"].iloc[n-1]

        if (rising_highs and short_stoch_rising and long_stoch_rising
                and long_stoch_above_30 and short_stoch_bull
                and macd_bull and above_ema21):

            reasons = [
                f"Long stoch crossover {n - last_cross} bars ago, %K_L={df['%K_L'].iloc[n]:.1f}",
                f"Rising highs 3 consecutive bars (H={df['High'].iloc[n]:.2f})",
                f"Short stoch rising + above %D at {df['%K_S'].iloc[n]:.1f}",
                f"MACD bull: {df['MACD'].iloc[n]:.3f} > signal {df['MACD_signal'].iloc[n]:.3f}",
                f"Close {df['Close'].iloc[n]:.2f} above EMA21 {df['EMA_21'].iloc[n]:.2f}",
            ]
            return StrategySignal(
                strategy="Stochastic",
                symbol=symbol,
                signal="BUY",
                confidence=0.78,
                reasoning=reasons,
                metadata={
                    "k_l":          round(float(df["%K_L"].iloc[n]), 2),
                    "k_s":          round(float(df["%K_S"].iloc[n]), 2),
                    "macd":         round(float(df["MACD"].iloc[n]), 4),
                    "macd_signal":  round(float(df["MACD_signal"].iloc[n]), 4),
                    "ema_21":       round(float(df["EMA_21"].iloc[n]), 2),
                    "close":        float(df["Close"].iloc[n]),
                    "bars_since_cross": n - last_cross,
                },
            )

        return None