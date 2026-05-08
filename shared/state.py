# from typing import TypedDict, Annotated, Optional
# from langgraph.graph.message import add_messages
# from shared.models import StrategySignal

# class TradingState(TypedDict):
#     symbol: str
#     interval: str
#     from_date: str
#     to_date: str
#     df: Optional[object]            # enriched DataFrame
#     signals: list[StrategySignal]   # collected from all strategy agents
#     risk_approved: bool             # set by risk manager
#     final_decision: Optional[str]   # BUY / SELL / HOLD
#     reasoning: list[str]            # audit trail
#     messages: Annotated[list, add_messages]   # LLM message history




from typing import TypedDict, Annotated, Optional
from langgraph.graph.message import add_messages
from shared.models import StrategySignal

class TradingState(TypedDict):
    symbol: str
    interval: str
    from_date: str
    to_date: str
    df: Optional[object]
    signals: list[StrategySignal]
    risk_approved: bool
    final_decision: Optional[str]
    reasoning: list[str]
    metadata: dict              # ← add this
    messages: Annotated[list, add_messages]