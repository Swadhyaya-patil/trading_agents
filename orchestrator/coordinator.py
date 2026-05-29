from orchestrator.graph import get_graph
from orchestrator.nodes import _historical_agent   # singleton — already logged in
from datetime import date


def run():
    graph   = get_graph()
    symbols = _historical_agent.get_symbols()

    print(f"Scanning {len(symbols)} FNO symbols...\n")

    for row in symbols:
        script = row["Script"]
        code   = row["Code"]
        print(f"→ {script} (code={code})")

        initial_state = {
            "symbol":         script,
            "code":           code,
            "interval":       "ONE_DAY",
            "from_date":      None,
            "to_date":        str(date.today()),
            "signals":        [],
            "risk_approved":  False,
            "final_decision": None,
            "reasoning":      [],
            "messages":       [],
            "metadata":       {},
        }

        config = {"configurable": {"thread_id": script}}

        for event in graph.stream(initial_state, config=config):
            node_name = list(event.keys())[0]
            print(f"  ✓ {node_name}")

        snapshot = graph.get_state(config)
        decision = snapshot.values.get("final_decision")

        if decision == "BUY":
            print(f"  → Decision: BUY — approve? (y/n): ", end="")
            ans = input().strip().lower()
            if ans == "y":
                graph.invoke(None, config=config)
            else:
                print("  → Skipped by user")
        else:
            print(f"  → HOLD")

        print()


if __name__ == "__main__":
    run()
