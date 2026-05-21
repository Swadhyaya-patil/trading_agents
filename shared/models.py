# from dataclasses import dataclass
# from typing import List


# @dataclass
# class StrategySignal:

#     strategy: str
#     symbol: str

#     signal: str
#     confidence: float

#     reasoning: List[str]

#     metadata: dict




from typing import List, Optional
from pydantic import BaseModel


class StrategySignal(BaseModel):
    strategy:   str
    symbol:     str
    signal:     str                  # "BUY" | "SELL" | "HOLD"
    confidence: float
    reasoning:  List[str]
    metadata:   dict = {}