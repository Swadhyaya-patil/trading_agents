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

            return {
                "strategy": "breakout",
                "signal": "BUY",
                "confidence": 0.89
            }

        return None