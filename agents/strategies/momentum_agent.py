# from shared.models import StrategySignal


# class MomentumAgent:

#     def evaluate(self, df, symbol):

#         latest = df.iloc[-1]

#         conditions = [
#             latest['EMA_21'] > latest['EMA_51'],
#             latest['MACD'] > latest['MACD_signal'],
#             latest['RSI'] > 60,
#             latest['Vol_Ratio'] > 1.5,
#             latest['Momentum_10'] > 0
#         ]

#         score = sum(conditions)

#         if score >= 4:

#             return StrategySignal(
#                 strategy_name="Momentum",
#                 symbol=symbol,
#                 signal="BUY",
#                 confidence=score / len(conditions),
#                 reasoning=[
#                     "EMA bullish",
#                     "MACD bullish",
#                     "Volume breakout"
#                 ],
#                 metadata={}
#             )

#         return None














from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal


class MomentumAgent(BaseStrategy):
    """
    Ported from original squeeze_BO.test_NR_LIVE()
    Checks multi-timeframe Low % change momentum + volume cascade
    Uses the LAST candle only (live mode logic)
    """

    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if df is None or len(df) < 20:
            return None

        df = df.copy()

        # ── Price momentum features (on Low, matching original)
        df["Daily_Pct_Change"]   = df["Low"].pct_change()
        df["Weekly_Pct_Change"]  = df["Low"].pct_change(periods=5)
        df["wk_2_pct_change"]    = df["Low"].pct_change(periods=10)
        df["wk_3_pct_change"]    = df["Low"].pct_change(periods=15)

        # ── Volume momentum features
        df["Daily_vol_Pct_Change"]  = df["Volume"].pct_change()
        df["Weekly_vol_Pct_Change"] = df["Volume"].pct_change(periods=5)
        df["wk_2_vol_pct_change"]   = df["Volume"].pct_change(periods=10)
        df["wk_3_vol_pct_change"]   = df["Volume"].pct_change(periods=15)
        df["vol_avg"]               = df["Volume"].rolling(window=20).mean()

        i = len(df) - 1   # evaluate on last candle (live mode)

        d  = df["Daily_Pct_Change"].iloc[i]
        w1 = df["Weekly_Pct_Change"].iloc[i]
        w2 = df["wk_2_pct_change"].iloc[i]
        w3 = df["wk_3_pct_change"].iloc[i]

        dv  = df["Daily_vol_Pct_Change"].iloc[i]
        wv1 = df["Weekly_vol_Pct_Change"].iloc[i]
        wv2 = df["wk_2_vol_pct_change"].iloc[i]
        wv3 = df["wk_3_vol_pct_change"].iloc[i]
        vol = df["Volume"].iloc[i]
        vol_avg = df["vol_avg"].iloc[i]

        # ── Exact conditions from original test_NR_LIVE()
        price_positive   = d > 0 and w1 > 0 and w2 > 0 and w3 > 0
        price_cascade    = w3 > d and w2 > d and w1 > d   # longer > shorter
        vol_positive     = dv > 0.02 and wv1 > 0 and wv2 > 0 and wv3 > 0
        vol_cascade      = wv2 > dv and wv1 > dv           # longer > shorter
        vol_spike        = vol > 1.2 * vol_avg

        if price_positive and price_cascade and vol_positive and vol_cascade and vol_spike:
            reasons = [
                f"Price momentum: 1d={d:.2%} 1w={w1:.2%} 2w={w2:.2%} 3w={w3:.2%} (cascading)",
                f"Volume spike: {vol:,.0f} vs avg {vol_avg:,.0f} ({vol/vol_avg:.1f}x)",
                f"Volume momentum positive across all timeframes",
            ]
            return StrategySignal(
                strategy="Momentum",
                symbol=symbol,
                signal="BUY",
                confidence=0.82,
                reasoning=reasons,
                metadata={
                    "daily_pct":   round(float(d),  4),
                    "weekly_pct":  round(float(w1), 4),
                    "wk2_pct":     round(float(w2), 4),
                    "wk3_pct":     round(float(w3), 4),
                    "vol_ratio":   round(float(vol / vol_avg), 2),
                    "close":       float(df["Close"].iloc[i]),
                },
            )

        return None