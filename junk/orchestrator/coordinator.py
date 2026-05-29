# class TradingCoordinator:

#     def __init__(
#         self,
#         historical_agent,
#         strategy_agents
#     ):

#         self.historical_agent = historical_agent
#         self.strategy_agents = strategy_agents

#     def run(self):

#         symbols = self.historical_agent.get_symbols()

#         print(f"Total Symbols: {len(symbols)}")

#         for symbol in symbols:

#             print(f"Processing: {symbol}")

#             df = self.historical_agent.get_market_data(
#                 symbol=symbol,
#                 interval='ONE_DAY',
#                 from_date='2024-01-01',
#                 to_date='2026-01-01'
#             )

#             if df is None:
#                 continue

#             for strategy in self.strategy_agents:

#                 try:

#                     signal = strategy.evaluate(df, symbol)

#                     if signal:

#                         print('\n--------------------------------')
#                         print(f"Strategy: {signal.strategy}")
#                         print(f"Symbol: {signal.symbol}")
#                         print(f"Signal: {signal.signal}")
#                         print(f"Confidence: {signal.confidence}")
#                         print(f"Reasoning: {signal.reasoning}")
#                         print('--------------------------------\n')

#                 except Exception as e:

#                     print(f"ERROR Strategy {symbol}: {e}")



















from orchestrator.graph import get_graph
from agents.historical_agent import HistoricalAgent
from datetime import date

def run():
    graph = get_graph()
    agent = HistoricalAgent()
    symbols = agent.get_symbols()[:10]   # start with 10 to test

    for symbol in symbols:
        print(f"\nRunning: {symbol}")

        initial_state = {
            "symbol": symbol,
            "interval": "ONE_DAY",
            "from_date": "2024-01-01",
            "to_date": str(date.today()),
            "df": None,
            "signals": [],
            "risk_approved": False,
            "final_decision": None,
            "reasoning": [],
            "messages": [],
        }

        config = {"configurable": {"thread_id": symbol}}

        # Run up to the interrupt (before executor)
        for event in graph.stream(initial_state, config=config):
            node_name = list(event.keys())[0]
            print(f"  ✓ {node_name}")

        # Peek at the paused state
        snapshot = graph.get_state(config)
        decision = snapshot.values.get("final_decision")

        if decision == "BUY":
            print(f"  → Decision: BUY — approve? (y/n): ", end="")
            ans = input().strip().lower()
            if ans == "y":
                # Resume past the interrupt
                graph.invoke(None, config=config)
            else:
                print("  → Skipped by user")

if __name__ == "__main__":
    run()