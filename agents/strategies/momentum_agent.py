from shared.models import StrategySignal


class MomentumAgent:

    def evaluate(self, df, symbol):

        latest = df.iloc[-1]

        conditions = [
            latest['EMA_21'] > latest['EMA_51'],
            latest['MACD'] > latest['MACD_signal'],
            latest['RSI'] > 60,
            latest['Vol_Ratio'] > 1.5,
            latest['Momentum_10'] > 0
        ]

        score = sum(conditions)

        if score >= 4:

            return StrategySignal(
                strategy_name="Momentum",
                symbol=symbol,
                signal="BUY",
                confidence=score / len(conditions),
                reasoning=[
                    "EMA bullish",
                    "MACD bullish",
                    "Volume breakout"
                ],
                metadata={}
            )

        return None