"""A small MCP server that shares RestockPilot's lookup tools with any MCP client
(Claude Desktop, n8n's MCP Client node, or mcp_demo.py).

Run:  python mcp_server.py      (stdio transport)
"""
from mcp.server.fastmcp import FastMCP

import knowledge
import rules
import tools

mcp = FastMCP("restockpilot")


@mcp.tool()
def get_stock_position(sku: str) -> dict:
    """Stock on hand, stock on order, usual supplier, shelf space and shelf life for one SKU."""
    return tools.stock_position(sku)


@mcp.tool()
def get_sales_summary(sku: str) -> dict:
    """28-day sales summary for one SKU (averages, spread, weekend effect, trend)."""
    return rules.sales_summary(sku)


@mcp.tool()
def get_upcoming_events(sku: str, days: int = 7) -> list:
    """Calendar events (festivals, matches, weather) that affect this SKU in the next `days` days."""
    return tools.upcoming_events(sku, days)


@mcp.tool()
def rank_suppliers(sku: str) -> list:
    """Suppliers for this SKU's category with status and score (40% price, 30% fill, 20% on-time, 10% lead time)."""
    return rules.candidate_suppliers(sku)


@mcp.tool()
def scrape_news(page: str) -> dict:
    """Read a market news page and return clean text, with hidden or suspicious text removed."""
    return tools.scrape_news(page)


@mcp.tool()
def search_policy(query: str) -> list:
    """Search the procurement policy and supplier knowledge bases."""
    hits, _ = knowledge.agentic_search(query)
    return [{"source": h["source"], "score": h["score"], "text": h["text"][:500]} for h in hits]


if __name__ == "__main__":
    mcp.run()
