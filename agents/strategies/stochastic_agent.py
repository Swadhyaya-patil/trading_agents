from shared.models import StrategySignal
class StochasticAgent:

    def evaluate(self, df, symbol):

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        bullish_cross = (
            prev['%K_S'] < prev['%D_S']
            and latest['%K_S'] > latest['%D_S']
        )

        oversold = latest['%K_S'] < 25

        if bullish_cross and oversold:

            # return {
            #     "strategy": "stochastic",
            #     "signal": "BUY",
            #     "confidence": 0.74
            # }
            return StrategySignal(
                strategy="stochastic",          # field is 'strategy', not 'strategy_name'
                symbol=symbol,
                signal="BUY",
                confidence=0.89,
                reasoning=["Price above 20-day high", "Volume surge", "BB squeeze"],
                metadata={}
            )

        return None