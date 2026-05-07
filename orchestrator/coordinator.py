class TradingCoordinator:

    def __init__(
        self,
        historical_agent,
        strategy_agents
    ):

        self.historical_agent = historical_agent
        self.strategy_agents = strategy_agents

    def run(self):

        symbols = self.historical_agent.get_symbols()

        print(f"Total Symbols: {len(symbols)}")

        for symbol in symbols:

            print(f"Processing: {symbol}")

            df = self.historical_agent.get_market_data(
                symbol=symbol,
                interval='ONE_DAY',
                from_date='2024-01-01',
                to_date='2026-01-01'
            )

            if df is None:
                continue

            for strategy in self.strategy_agents:

                try:

                    signal = strategy.evaluate(df, symbol)

                    if signal:

                        print('\n--------------------------------')
                        print(f"Strategy: {signal.strategy}")
                        print(f"Symbol: {signal.symbol}")
                        print(f"Signal: {signal.signal}")
                        print(f"Confidence: {signal.confidence}")
                        print(f"Reasoning: {signal.reasoning}")
                        print('--------------------------------\n')

                except Exception as e:

                    print(f"ERROR Strategy {symbol}: {e}")