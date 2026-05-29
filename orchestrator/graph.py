from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from shared.state import TradingState
from orchestrator.nodes import (
    data_fetcher_node,
    strategy_node,
    trade_llm_node,
    executor_node,
)
from agents.risk_manager import risk_manager_node
from agents.supervisor import supervisor_node


def build_graph(checkpointer=None):
    builder = StateGraph(TradingState)

    builder.add_node("data_fetcher", data_fetcher_node)
    builder.add_node("strategies",   strategy_node)
    builder.add_node("trade_llm",    trade_llm_node)
    builder.add_node("risk_manager", risk_manager_node)
    builder.add_node("supervisor",   supervisor_node)
    builder.add_node("executor",     executor_node)

    builder.set_entry_point("data_fetcher")
    builder.add_edge("data_fetcher", "strategies")
    builder.add_edge("strategies",   "trade_llm")
    builder.add_edge("trade_llm",    "risk_manager")
    builder.add_edge("risk_manager", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda state: "executor" if state["final_decision"] in ("BUY", "SELL") else END,
        {"executor": "executor", END: END},
    )
    builder.add_edge("executor", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["executor"],
    )


def get_graph():
    memory = MemorySaver()
    return build_graph(checkpointer=memory)
