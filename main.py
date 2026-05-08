# from dotenv import load_dotenv
# load_dotenv()

# print("jai shree ram")
# from agents.historical_agent import HistoricalAgent

# from agents.strategies.momentum_agent import MomentumAgent
# from agents.strategies.stochastic_agent import StochasticAgent
# from agents.strategies.breakout_agent import BreakoutAgent

# # from orchestrator.coordinator import TradingCoordinator
# import orchestrator.coordinator








import os
from dotenv import load_dotenv
from orchestrator.graph import get_graph
from agents.historical_agent import HistoricalAgent

load_dotenv()
os.environ["LANGGRAPH_ALLOWED_MSGPACK_MODULES"] = "shared.models.StrategySignal"

def run():
    graph  = get_graph()
    agent  = HistoricalAgent()
    symbols = agent.get_symbols()     # returns [{"Script": ..., "Code": ...}]

    print(f"Scanning {len(symbols)} FNO symbols...\n")

    for row in symbols:
        script = row["Script"]
        code   = row["Code"]
        print(f"→ {script} (code={code})")

        initial_state = {
            "symbol":         script,
            "code":           code,       # ← now correctly passed
            "interval":       "ONE_DAY",
            "from_date":      None,       # None = auto last 201 days
            "to_date":        None,       # None = today
            "df":             None,
            "signals":        [],
            "risk_approved":  False,
            "final_decision": None,
            "reasoning":      [],
            "metadata":       {},
            "messages":       [],
        }

        config = {"configurable": {"thread_id": script}}

        try:
            for event in graph.stream(initial_state, config=config):
                node_name = list(event.keys())[0]
                print(f"  ✓ {node_name}")

            snapshot = graph.get_state(config)
            decision = snapshot.values.get("final_decision")

            if decision == "BUY":
                reasoning = snapshot.values.get("reasoning", [])
                print(f"\n  🟢 BUY signal for {script}")
                for r in reasoning:
                    print(f"     › {r}")
                print(f"\n  Approve trade? (y/n): ", end="")
                ans = input().strip().lower()
                if ans == "y":
                    graph.invoke(None, config=config)
                    print(f"  ✅ Order sent for {script}")
                else:
                    print(f"  ⏭  Skipped by user")

        except Exception as e:
            print(f"  ✗ Pipeline error for {script}: {e}")

        print()

if __name__ == "__main__":
    run()