# from langgraph.graph import StateGraph, END
# from langgraph.checkpoint.sqlite import SqliteSaver
# from shared.state import TradingState
# from orchestrator.nodes import (
#     data_fetcher_node,
#     strategy_node,
#     risk_manager_node,
#     supervisor_node,
#     executor_node,
# )

# def build_graph(checkpointer=None):
#     builder = StateGraph(TradingState)

#     # Register nodes
#     builder.add_node("data_fetcher",  data_fetcher_node)
#     builder.add_node("strategies",    strategy_node)
#     builder.add_node("risk_manager",  risk_manager_node)
#     builder.add_node("supervisor",    supervisor_node)
#     builder.add_node("executor",      executor_node)

#     # Wire edges (sequential for now)
#     builder.set_entry_point("data_fetcher")
#     builder.add_edge("data_fetcher", "strategies")
#     builder.add_edge("strategies",   "risk_manager")
#     builder.add_edge("risk_manager", "supervisor")

#     # Conditional edge: supervisor decides whether to execute or skip
#     builder.add_conditional_edges(
#         "supervisor",
#         lambda state: "executor" if state["final_decision"] == "BUY" else END,
#         {"executor": "executor", END: END}
#     )
#     builder.add_edge("executor", END)

#     # Compile — optionally with a checkpointer for persistence
#     return builder.compile(
#         checkpointer=checkpointer,
#         interrupt_before=["executor"],   # human-in-the-loop pause before any trade
#     )

# def get_graph():
#     memory = SqliteSaver.from_conn_string("trading_state.db")
#     return build_graph(checkpointer=memory)




# from langgraph.checkpoint.memory import InMemorySaver
# from langgraph.graph import StateGraph, END
# from langgraph.checkpoint.sqlite import SqliteSaver
# from shared.state import TradingState
# from orchestrator.nodes import data_fetcher_node, strategy_node
# from agents.risk_manager import risk_manager_node      # ← Ollama node
# from agents.supervisor import supervisor_node           # ← Ollama node
# from orchestrator.nodes import executor_node

# def build_graph(checkpointer=None):
#     builder = StateGraph(TradingState)

#     builder.add_node("data_fetcher",  data_fetcher_node)
#     builder.add_node("strategies",    strategy_node)
#     builder.add_node("risk_manager",  risk_manager_node)
#     builder.add_node("supervisor",    supervisor_node)
#     builder.add_node("executor",      executor_node)

#     builder.set_entry_point("data_fetcher")
#     builder.add_edge("data_fetcher", "strategies")
#     builder.add_edge("strategies",   "risk_manager")
#     builder.add_edge("risk_manager", "supervisor")
#     builder.add_conditional_edges(
#         "supervisor",
#         lambda state: "executor" if state["final_decision"] == "BUY" else END,
#         {"executor": "executor", END: END}
#     )
#     builder.add_edge("executor", END)

#     return builder.compile(
#         checkpointer=checkpointer,
#         interrupt_before=["executor"],
#     )

# def get_graph():
#     # memory = SqliteSaver.from_conn_string("trading_state.db")
    

#     memory = InMemorySaver()

#     graph = build_graph(checkpointer=memory)
#     return build_graph(checkpointer=memory)









from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from shared.state import TradingState
from shared.models import StrategySignal
from orchestrator.nodes import (
    data_fetcher_node,
    strategy_node,
    executor_node,
)
from agents.risk_manager import risk_manager_node
from agents.supervisor import supervisor_node


def build_graph(checkpointer=None):
    builder = StateGraph(TradingState)

    builder.add_node("data_fetcher", data_fetcher_node)
    builder.add_node("strategies",   strategy_node)
    builder.add_node("risk_manager", risk_manager_node)
    builder.add_node("supervisor",   supervisor_node)
    builder.add_node("executor",     executor_node)

    builder.set_entry_point("data_fetcher")
    builder.add_edge("data_fetcher", "strategies")
    builder.add_edge("strategies",   "risk_manager")
    builder.add_edge("risk_manager", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda state: "executor" if state["final_decision"] == "BUY" else END,
        {"executor": "executor", END: END},
    )
    builder.add_edge("executor", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["executor"],
    )


def get_graph():
    # Register StrategySignal so msgpack can serialize it cleanly
    # memory = SqliteSaver.from_conn_string(
    #     "trading_state.db",
    #     allowed_msgpack_modules=[("shared.models", "StrategySignal")],
    # )
    memory = InMemorySaver()
    return build_graph(checkpointer=memory)