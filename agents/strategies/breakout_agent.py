# from shared.models import StrategySignal

# class BreakoutAgent:

#     def evaluate(self, df, symbol):

#         latest = df.iloc[-1]

#         breakout = (
#             latest['Close']
#             >= latest['Rolling_Max_20']
#         )

#         volume_confirmation = (
#             latest['Vol_Ratio'] > 2
#         )

#         squeeze = (
#             latest['BB_WIDTH']
#             < df['BB_WIDTH'].rolling(20).mean().iloc[-1]
#         )

#         if breakout and volume_confirmation and squeeze:

#             # return {
#             #     "strategy": "breakout",
#             #     "signal": "BUY",
#             #     "confidence": 0.89
#             # }

#             return StrategySignal(
#                 strategy="Breakout",          # field is 'strategy', not 'strategy_name'
#                 symbol=symbol,
#                 signal="BUY",
#                 confidence=0.89,
#                 reasoning=["Price above 20-day high", "Volume surge", "BB squeeze"],
#                 metadata={}
#             )
#         return None










from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal


class BreakoutAgent(BaseStrategy):
    """
    Ported from original squeeze_BO.test_momentum_live() — "Momentum 2024"
    Checks: 2 green candles + 9 historical touch points at support + stoch confluence
    """

    TOUCH_PCT   = 0.5    # ±0.5% band around the reference close
    MIN_TOUCHES = 9      # minimum historical touches required

    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if df is None or len(df) < 40:
            return None

        df = df.copy().reset_index(drop=True)

        # ── Compute stochastics (same as original)
        low_min  = df["Low"].rolling(34).min()
        high_max = df["High"].rolling(34).max()
        df["%KL"] = (df["Close"] - low_min) / (high_max - low_min) * 100
        df["%DL"] = df["%KL"].rolling(8).mean()
        df["%K_L"] = df["%KL"].rolling(8).mean()
        df["%D_L"] = df["%DL"].rolling(3).mean()

        low_min  = df["Low"].rolling(5).min()
        high_max = df["High"].rolling(5).max()
        df["%KS"] = (df["Close"] - low_min) / (high_max - low_min) * 100
        df["%DS"] = df["%KS"].rolling(3).mean()
        df["%K_S"] = df["%KS"].rolling(3).mean()
        df["%D_S"] = df["%DS"].rolling(3).mean()

        n = len(df) - 1

        # ── Condition 1: Last 2 candles are green
        two_green = (
            df["Close"].iloc[n]   > df["Open"].iloc[n]
            and df["Close"].iloc[n-1] > df["Open"].iloc[n-1]
        )

        # ── Condition 2: Stochastic confluence (from original test_momentum_live)
        ref = n - 3   # reference candle is 3 bars back
        stoch_ok = (
            df["%K_L"].iloc[ref] > df["%D_L"].iloc[ref]
            and 20 <= df["%K_L"].iloc[ref] <= 45
            and df["%K_L"].iloc[ref] > df["%K_L"].iloc[ref-1] > df["%K_L"].iloc[ref-2]
            and df["%K_S"].iloc[ref] > df["%D_S"].iloc[ref]
            and 40 < df["%K_S"].iloc[ref] < 80
            and df["%K_S"].iloc[ref] > df["%K_S"].iloc[ref-1]
            and df["%K_S"].iloc[ref-2] > df["%K_S"].iloc[ref-3]
        )

        # ── Condition 3: 9 touch points of support (Low within ±0.5% of ref close)
        ref_close = df["Close"].iloc[ref]
        band_hi = ref_close * (1 + self.TOUCH_PCT / 100)
        band_lo = ref_close * (1 - self.TOUCH_PCT / 100)

        touches = sum(
            1 for j in range(0, ref)
            if band_lo <= df["Low"].iloc[j] <= band_hi
        )

        if two_green and stoch_ok and touches >= self.MIN_TOUCHES:
            reasons = [
                f"2 consecutive green candles confirmed",
                f"Stoch confluence: %K_L={df['%K_L'].iloc[ref]:.1f} (20-45 zone), %K_S={df['%K_S'].iloc[ref]:.1f} (40-80 zone)",
                f"Support zone: {touches} historical Low touches near {ref_close:.2f} (±{self.TOUCH_PCT}%)",
            ]
            return StrategySignal(
                strategy="Breakout",
                symbol=symbol,
                signal="BUY",
                confidence=0.86,
                reasoning=reasons,
                metadata={
                    "ref_close":   round(ref_close, 2),
                    "touch_count": touches,
                    "k_l_ref":     round(float(df["%K_L"].iloc[ref]), 2),
                    "k_s_ref":     round(float(df["%K_S"].iloc[ref]), 2),
                    "close":       float(df["Close"].iloc[n]),
                },
            )

        return None
