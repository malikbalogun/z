"""
Backward compatibility: use `from bot.orchestrator import TradingBot` (recommended).
"""

from bot.orchestrator import TradingBot as RealTrader

__all__ = ["RealTrader"]
