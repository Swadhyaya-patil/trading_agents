from typing import TypedDict, Annotated, Optional
from langgraph.graph.message import add_messages


class TradingState(TypedDict):
    symbol:         str
    code:           str
    interval:       str
    from_date:      Optional[str]
    to_date:        Optional[str]
    # df removed — stored in df_cache.py, never in LangGraph state
    # signals stored as list[dict] not list[StrategySignal] so msgpack
    # can serialize them cleanly through MemorySaver checkpoints.
    # Every strategy already returns StrategySignal(BaseModel) — we call
    # .model_dump() before storing here, and rebuild in risk/supervisor.
    signals:        list[dict]
    risk_approved:  bool
    final_decision: Optional[str]
    reasoning:      list[str]
    metadata:       dict
    messages:       Annotated[list, add_messages]
