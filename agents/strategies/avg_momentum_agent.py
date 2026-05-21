from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal


class AvgMomentumAgent(BaseStrategy):
    """
    Ported from squeeze_BO.test_AVG_BO()
    Uses rolling-average smoothed pct change — catches steadier,
    sustained trends vs the raw momentum spike strategy.
    Conditions: 3 consecutive rising weekly avg pct changes
    + total gain < 5.1% (not overextended) + volume spike
    """

    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if df is None or len(df) < 30:
            return None

        df = df.copy()

        # ── Smoothed pct changes (rolling avg first, then pct change)
        df["Daily_Pct_Change"]   = df["Low"].rolling(1).mean().pct_change()
        df["Weekly_Pct_Change"]  = df["Low"].rolling(5).mean().pct_change(periods=5)
        df["vol_avg"]            = df["Volume"].rolling(window=20).mean()

        n = len(df) - 1

        w1  = df["Weekly_Pct_Change"].iloc[n-1]
        w2  = df["Weekly_Pct_Change"].iloc[n-2]
        w3  = df["Weekly_Pct_Change"].iloc[n-3]
        d   = df["Daily_Pct_Change"].iloc[n]
        vol = df["Volume"].iloc[n]
        vol_avg = df["vol_avg"].iloc[n]

        # ── Exact conditions from test_AVG_BO
        three_rising_weeks  = w3 > 0 and w2 > 0 and w1 > 0
        accelerating        = w3 < w2 < w1
        not_overextended    = (w1 - w3) < 0.051      # total gain < 5.1%
        vol_spike           = vol > 1.2 * vol_avg
        daily_positive      = d > 0

        if three_rising_weeks and accelerating and not_overextended and vol_spike and daily_positive:
            reasons = [
                f"3 rising weekly avg pct: w3={w3:.2%} → w2={w2:.2%} → w1={w1:.2%}",
                f"Not overextended: total gain {(w1-w3):.2%} < 5.1%",
                f"Volume spike: {vol:,.0f} vs avg {vol_avg:,.0f} ({vol/vol_avg:.1f}x)",
            ]
            return StrategySignal(
                strategy="AvgMomentum",
                symbol=symbol,
                signal="BUY",
                confidence=0.80,
                reasoning=reasons,
                metadata={
                    "w1": round(float(w1), 4),
                    "w2": round(float(w2), 4),
                    "w3": round(float(w3), 4),
                    "vol_ratio": round(float(vol / vol_avg), 2),
                    "close": float(df["Close"].iloc[n]),
                },
            )

        return None