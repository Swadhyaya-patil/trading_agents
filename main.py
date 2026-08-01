import os
from dotenv import load_dotenv

load_dotenv()

os.environ["LANGGRAPH_ALLOWED_MSGPACK_MODULES"] = "shared.models.StrategySignal"
os.environ["TF_ENABLE_ONEDNN_OPTS"]             = "0"

from orchestrator.graph import get_graph
from orchestrator.logger import log_signal
from orchestrator import df_cache
from orchestrator.nodes import _historical_agent


def run():
    graph   = get_graph()
    symbols = _historical_agent.get_symbols()

    print(f"Scanning {len(symbols)} FNO symbols...\n")

    for row in symbols:
        script = row["Script"]
        code   = row.get("code") or row.get("Code")
        print(f"→ {script} (code={code})")

        initial_state = {
            "symbol":         script,
            "code":           code,
            "interval":       "ONE_DAY",
            "from_date":      None,
            "to_date":        None,
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
            state    = snapshot.values

            log_signal(state)

            decision = state.get("final_decision")

            if decision in ("BUY", "SELL"):
                meta = state.get("metadata", {})
                tag  = "🟢 BUY" if decision == "BUY" else "🔴 SELL"
                print(f"\n  {tag} — {script}")
                print(f"     Confidence : {meta.get('supervisor_confidence', 'N/A')}")
                print(f"     Entry      : {meta.get('suggested_entry', 'N/A')}")
                print(f"     Timeframe  : {meta.get('timeframe', 'N/A')}")
                pos = meta.get("max_position_pct", 0)
                if isinstance(pos, float):
                    print(f"     Position   : {pos * 100:.1f}% of portfolio")

                # signals are dicts — use ["strategy"] not .strategy
                fired = [s["strategy"] for s in state.get("signals", [])]
                print(f"     Strategies : {fired}")
                for r in state.get("reasoning", []):
                    print(f"     › {r}")

                print(f"\n  Approve trade? (y/n): ", end="")
                # ans = input().strip().lower()
                ans = "y" # Auto-approve for testing
                if ans == "y":
                    graph.invoke(None, config=config)
                    print(f"  ✅ Order placed for {script}")
                else:
                    print(f"  ⏭  Skipped")
            else:
                print(f"  → HOLD")

        except Exception as e:
            import traceback
            print(f"  ✗ Pipeline error for {script}: {e}")
            traceback.print_exc()

        finally:
            df_cache.clear(script)

        print()

    _historical_agent.print_summary()


if __name__ == "__main__":
    run()