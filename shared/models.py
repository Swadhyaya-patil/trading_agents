from typing import List
from pydantic import BaseModel


class StrategySignal(BaseModel):
    strategy:   str
    symbol:     str
    signal:     str        # "BUY" | "SELL" | "HOLD"
    confidence: float
    reasoning:  List[str]
    metadata:   dict = {}

    # ── LangGraph msgpack serialization ───────────────────────────────
    # MemorySaver uses msgpack to checkpoint state between nodes.
    # Pydantic BaseModel is not msgpack-serializable by default, which
    # causes: "Type is not msgpack serializable: StrategySignal"
    # Fix: teach LangGraph how to serialize/deserialize this class.

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, cls):
            return v
        if isinstance(v, dict):
            return cls(**v)
        raise ValueError(f"Cannot validate StrategySignal from {type(v)}")

    def model_dump_json_safe(self) -> dict:
        """Plain dict safe for msgpack — no Pydantic types."""
        return {
            "strategy":   self.strategy,
            "symbol":     self.symbol,
            "signal":     self.signal,
            "confidence": self.confidence,
            "reasoning":  list(self.reasoning),
            "metadata":   dict(self.metadata),
        }
