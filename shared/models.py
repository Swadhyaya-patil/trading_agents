from dataclasses import dataclass
from typing import List


@dataclass
class StrategySignal:

    strategy: str
    symbol: str

    signal: str
    confidence: float

    reasoning: List[str]

    metadata: dict