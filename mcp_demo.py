"""MCP client demo: starts mcp_server.py, lists its tools and calls a few of them.

Run:  python mcp_demo.py
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])
    async with stdio_client(server, errlog=open(os.devnull, "w")) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            print("Tools shared by the RestockPilot MCP server:")
            for t in listed.tools:
                print(f"  - {t.name}: {t.description.splitlines()[0]}")
            for name, args in [("get_stock_position", {"sku": "SKU-201"}),
                               ("rank_suppliers", {"sku": "SKU-201"}),
                               ("scrape_news", {"page": "wholesale_deal.html"}),
                               ("search_policy", {"query": "MSME payment terms 45 days"})]:
                res = await session.call_tool(name, args)
                items = []
                for c in res.content:                       # a list comes back as several items
                    try:
                        items.append(json.loads(c.text))
                    except Exception:
                        items.append(c.text)
                text = json.dumps(items[0] if len(items) == 1 else items, indent=1)
                print(f"\n>>> {name}({args})\n{text[:700]}")


if __name__ == "__main__":
    asyncio.run(main())
