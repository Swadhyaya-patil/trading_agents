from shared.models import StrategySignal

class BreakoutAgent:

    def evaluate(self, df, symbol):

        latest = df.iloc[-1]

        breakout = (
            latest['Close']
            >= latest['Rolling_Max_20']
        )

        volume_confirmation = (
            latest['Vol_Ratio'] > 2
        )

        squeeze = (
            latest['BB_WIDTH']
            < df['BB_WIDTH'].rolling(20).mean().iloc[-1]
        )

        if breakout and volume_confirmation and squeeze:

            # return {
            #     "strategy": "breakout",
            #     "signal": "BUY",
            #     "confidence": 0.89
            # }

            return StrategySignal(
                strategy="Breakout",          # field is 'strategy', not 'strategy_name'
                symbol=symbol,
                signal="BUY",
                confidence=0.89,
                reasoning=["Price above 20-day high", "Volume surge", "BB squeeze"],
                metadata={}
            )
        return None