from dotenv import load_dotenv
load_dotenv()

print("jai shree ram")
from agents.historical_agent import HistoricalAgent

from agents.strategies.momentum_agent import MomentumAgent
from agents.strategies.stochastic_agent import StochasticAgent
from agents.strategies.breakout_agent import BreakoutAgent

# from orchestrator.coordinator import TradingCoordinator
import orchestrator.coordinator