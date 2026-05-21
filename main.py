# from dotenv import load_dotenv
# load_dotenv()

# print("jai shree ram")
# from agents.historical_agent import HistoricalAgent

# from agents.strategies.momentum_agent import MomentumAgent
# from agents.strategies.stochastic_agent import StochasticAgent
# from agents.strategies.breakout_agent import BreakoutAgent

# # from orchestrator.coordinator import TradingCoordinator
# import orchestrator.coordinator








# import os
# from dotenv import load_dotenv
# from orchestrator.graph import get_graph
# from agents.historical_agent import HistoricalAgent

# # load_dotenv()
# # os.environ["LANGGRAPH_ALLOWED_MSGPACK_MODULES"] = "shared.models.StrategySignal"

# def run():
#     graph  = get_graph()
#     agent  = HistoricalAgent()
#     symbols = agent.get_symbols()     # returns [{"Script": ..., "Code": ...}]

#     print(f"Scanning {len(symbols)} FNO symbols...\n")

#     for row in symbols:
#         script = row["Script"]
#         code   = row["Code"]
#         print(f"→ {script} (code={code})")

#         initial_state = {
#             "symbol":         script,
#             "code":           code,       # ← now correctly passed
#             "interval":       "ONE_DAY",
#             "from_date":      None,       # None = auto last 201 days
#             "to_date":        None,       # None = today
#             "df":             None,
#             "signals":        [],
#             "risk_approved":  False,
#             "final_decision": None,
#             "reasoning":      [],
#             "metadata":       {},
#             "messages":       [],
#         }

#         config = {"configurable": {"thread_id": script}}

#         try:
#             for event in graph.stream(initial_state, config=config):
#                 node_name = list(event.keys())[0]
#                 print(f"  ✓ {node_name}")
#                 from orchestrator.logger import log_signal

#                 # inside the for loop, after graph.stream() finishes:
#                 snapshot = graph.get_state(config)
#                 state    = snapshot.values

#                 # log every symbol — BUY, HOLD, or no signals
#                 log_signal(state)

#                 decision = state.get("final_decision")
#                 if decision == "BUY":
#                     print(f"\n  🟢 BUY signal for {script}")
#                     for r in state.get("reasoning", []):
#                         print(f"     › {r}")
#                     print(f"  Approve trade? (y/n): ", end="")
#                     ans = input().strip().lower()
#                     if ans == "y":
#                         graph.invoke(None, config=config)
                        
#             snapshot = graph.get_state(config)
#             decision = snapshot.values.get("final_decision")

#             if decision == "BUY":
#                 reasoning = snapshot.values.get("reasoning", [])
#                 print(f"\n  🟢 BUY signal for {script}")
#                 for r in reasoning:
#                     print(f"     › {r}")
#                 print(f"\n  Approve trade? (y/n): ", end="")
#                 ans = input().strip().lower()
#                 if ans == "y":
#                     graph.invoke(None, config=config)
#                     print(f"  ✅ Order sent for {script}")
#                 else:
#                     print(f"  ⏭  Skipped by user")

#         except Exception as e:
#             print(f"  ✗ Pipeline error for {script}: {e}")

#         print()

# if __name__ == "__main__":
#     run()







import os
from dotenv import load_dotenv

load_dotenv()

from orchestrator.graph import get_graph
from orchestrator.logger import log_signal
from agents.historical_agent import HistoricalAgent

import os
os.environ["LANGGRAPH_ALLOWED_MSGPACK_MODULES"] = "shared.models.StrategySignal"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"   # also silence the TF noise

def run():
    graph   = get_graph()
    agent   = HistoricalAgent()
    symbols = agent.get_symbols()

    print(f"Scanning {len(symbols)} FNO symbols...\n")

    for row in symbols:
        script = row["Script"]
        code   = row["Code"]
        print(f"→ {script} (code={code})")

        initial_state = {
            "symbol":        script,
            "code":          code,
            "interval":      "ONE_DAY",
            "from_date":     None,
            "to_date":       None,
            "signals":       [],
            "risk_approved": False,
            "final_decision": None,
            "reasoning":     [],
            "metadata":      {},
            "messages":      [],
        }

        config = {"configurable": {"thread_id": script}}

        try:
            # ── Stream through all nodes up to interrupt
            for event in graph.stream(initial_state, config=config):
                node_name = list(event.keys())[0]
                print(f"  ✓ {node_name}")

            # ── Graph is now paused at interrupt_before=["executor"]
            snapshot = graph.get_state(config)
            state    = snapshot.values

            # ── Log every symbol regardless of decision
            log_signal(state)

            decision = state.get("final_decision")

            if decision == "BUY":
                meta = state.get("metadata", {})
                print(f"\n  🟢 BUY — {script}")
                print(f"     Confidence : {meta.get('supervisor_confidence', 'N/A')}")
                print(f"     Entry      : {meta.get('suggested_entry', 'N/A')}")
                print(f"     Timeframe  : {meta.get('timeframe', 'N/A')}")
                print(f"     Position   : {meta.get('max_position_pct', 0)*100:.1f}% of portfolio")
                print(f"     Strategies : {[s.strategy for s in state.get('signals', [])]}")
                for r in state.get("reasoning", []):
                    print(f"     › {r}")

                print(f"\n  Approve trade? (y/n): ", end="")
                ans = input().strip().lower()
                if ans == "y":
                    graph.invoke(None, config=config)   # resume past interrupt → executor
                    print(f"  ✅ Order queued for {script}")
                else:
                    print(f"  ⏭  Skipped")
            else:
                print(f"  → {decision or 'HOLD'}")

        except Exception as e:
            print(f"  ✗ Pipeline error for {script}: {e}")

        print()


if __name__ == "__main__":
    run()