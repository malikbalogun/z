"""
Reference: external tools/repos aligned with this bot (from user research).

Sources discussed in chat [[Polymarket bot articles]](b7db2423-ffd8-4245-b83b-2cf593ce376a):

1. evan-kolberg/prediction-market-backtesting — NautilusTrader backtests (Polymarket/Kalshi). Use offline for strategy validation.
2. TauricResearch/TradingAgents — multi-agent research stack; optional external orchestration.
3. mvanhorn/last30days-skill — news windowing; feed keywords into Admin → Signals or future news agent.
4. FiatFiorino/polymarket-assistant-tool — indicators; mirrored lightly via order-book imbalance gate + CEX gates, plus optional agents for Gamma-vs-CLOB lag, YES+NO ask-sum bundle arb, and rolling z-score dislocation.
5. firecrawl/firecrawl — scrape → text; use upstream to populate Signals (URLs) manually or via n8n.
6. pydantic/pydantic-ai — agent framework; this codebase uses FastAPI + DB settings instead.
7. n8n-io/n8n — wire Firecrawl/Tavily → webhook → Signals API (external).
8. tavily-ai/tavily-mcp — search MCP; external to push research into Signals.
9. txbabaxyz/collectmarkets2 — wallet trade history; mirrored via `bot/wallet_trades.py` + admin endpoint.
10. txbabaxyz/mlmodelpoly — CEX→fair value; mirrored via multi-CEX bundle + optional min-edge bps using agent reference mid.

In-repo execution stack also adds spread / resolution-time / exposure / daily-notional gates and a consecutive-failure circuit breaker (see `Settings` + `orchestrator`).

This module is documentation-only (no runtime imports required).
"""

REPOS = (
    ("prediction-market-backtesting", "https://github.com/evan-kolberg/prediction-market-backtesting"),
    ("TradingAgents", "https://github.com/TauricResearch/TradingAgents"),
    ("last30days-skill", "https://github.com/mvanhorn/last30days-skill"),
    ("polymarket-assistant-tool", "https://github.com/FiatFiorino/polymarket-assistant-tool"),
    ("firecrawl", "https://github.com/firecrawl/firecrawl"),
    ("pydantic-ai", "https://github.com/pydantic/pydantic-ai"),
    ("n8n", "https://github.com/n8n-io/n8n"),
    ("tavily-mcp", "https://github.com/tavily-ai/tavily-mcp"),
    ("collectmarkets2", "https://github.com/txbabaxyz/collectmarkets2"),
    ("mlmodelpoly", "https://github.com/txbabaxyz/mlmodelpoly"),
)
