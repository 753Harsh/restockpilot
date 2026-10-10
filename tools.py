"""Tools the agents can call. Each one is a plain function plus a LangChain @tool wrapper.

The same plain functions are shared over MCP in mcp_server.py.
"""
import json
import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool

import db
import knowledge
import rules
from data_store import SKUS, STOCK, SUPPLIERS

NEWS_DIR = Path(__file__).parent / "data" / "news"
OUT_DIR = Path(__file__).parent / "output"
RUN_DATE = date(2026, 10, 12)       # fixed "today" so every run gives the same numbers


# ------------------------------------------------------------- plain functions
def stock_position(sku: str) -> dict:
    if sku not in SKUS:
        return {"error": f"unknown SKU {sku}"}
    s = STOCK[sku]
    return {"sku": sku, "name": SKUS[sku]["name"], "category": SKUS[sku]["category"],
            "on_hand": s["on_hand"], "on_order": s["on_order"], "usual_supplier": s["usual_supplier"],
            "shelf_capacity": SKUS[sku]["shelf_capacity"], "shelf_life_days": SKUS[sku]["shelf_life_days"]}


def upcoming_events(sku: str, days: int = 7) -> list:
    if sku not in SKUS:
        return [{"error": f"unknown SKU {sku}"}]
    evs = rules.events_for(SKUS[sku]["category"], RUN_DATE, int(days))
    return [{"event": e["event"], "start": e["start"].isoformat(), "end": e["end"].isoformat(),
             "uplift": e["uplift"]} for e in evs]


HIDDEN_STYLE = re.compile(r"display\s*:\s*none|font-size\s*:\s*[01]px|color\s*:\s*(white|#fff\b|#ffffff)", re.I)


def scrape_news(page: str) -> dict:
    """Read a news web page (a file in data/news or an http link) and return clean visible text.

    Hidden text and sentences that try to give orders to an AI are removed and reported.
    """
    if page.startswith("http"):
        html = requests.get(page, timeout=15, headers={"User-Agent": "RestockPilot/1.0"}).text
    else:
        path = NEWS_DIR / Path(page).name
        if not path.exists():
            return {"page": page, "error": "page not found"}
        html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    hidden = []
    for tag in soup.find_all(style=HIDDEN_STYLE):
        hidden.append(tag.get_text(" ", strip=True)[:160])
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else page
    article = soup.find("article") or soup.body
    text = " ".join(p.get_text(" ", strip=True) for p in article.find_all("p"))
    clean, injected = rules.scan_for_injection(text)
    _, injected_hidden = rules.scan_for_injection(" ".join(hidden))
    return {"page": page, "title": title, "text": clean,
            "hidden_text_removed": hidden, "injection_found": injected + injected_hidden}


def place_purchase_order(case_id, line, decision, decided_by):
    """Pretend to send a PO to the supplier: write it to output/purchase_orders.json and the database."""
    OUT_DIR.mkdir(exist_ok=True)
    po = {
        "po_id": "PO-" + uuid.uuid4().hex[:8].upper(), "case_id": case_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sku": line["sku"], "sku_name": SKUS[line["sku"]]["name"],
        "supplier_id": line["supplier_id"], "supplier_name": SUPPLIERS[line["supplier_id"]]["name"],
        "qty": line["qty"], "unit_cost": line["unit_cost"], "value": line["value"],
        "priority": line["priority"], "decision": decision, "decided_by": decided_by,
        "reasons": line.get("approval_reasons", []),
    }
    if decision in ("AUTO", "APPROVED"):           # only real orders go to the supplier file
        path = OUT_DIR / "purchase_orders.json"
        existing = json.loads(path.read_text()) if path.exists() else []
        existing.append(po)
        path.write_text(json.dumps(existing, indent=2))
    else:
        po["po_id"] = po["po_id"].replace("PO-", "LOG-")
    db.save_po(po)                                  # every decision goes to the audit log
    return po


def notify_n8n(payload: dict):
    """Send high-priority cases to the n8n webhook (Slack, email, SMS). Silent if not set up."""
    url = os.getenv("N8N_WEBHOOK_URL")
    if not url:
        return "n8n webhook not set (skipped)"
    try:
        r = requests.post(url, json=payload, timeout=10)
        return f"n8n webhook answered {r.status_code}"
    except Exception as e:
        return f"n8n webhook failed: {type(e).__name__}"


# ------------------------------------------------------------- LangChain tools
@tool
def get_stock_position(sku: str) -> str:
    """Stock on hand, stock already ordered, usual supplier, shelf space and shelf life for one SKU."""
    return json.dumps(stock_position(sku))


@tool
def get_sales_summary(sku: str) -> str:
    """Last 28 days of sales for one SKU: 7-day average, older average, spread, weekend effect, trend %."""
    if sku not in SKUS:
        return json.dumps({"error": f"unknown SKU {sku}"})
    return json.dumps(rules.sales_summary(sku))


@tool
def get_upcoming_events(sku: str, days: int = 7) -> str:
    """Festivals, matches or weather from the store calendar that touch this SKU in the next `days` days."""
    return json.dumps(upcoming_events(sku, days))


@tool
def scrape_news_page(page: str) -> str:
    """Read one market news page and return its clean text. Hidden or suspicious text is removed."""
    return json.dumps(scrape_news(page))


@tool
def search_knowledge_base(query: str, kb: str = "both") -> str:
    """Search the procurement policy and supplier contract documents. kb: procurement_policy, supplier_contracts or both."""
    hits, trace = knowledge.agentic_search(query, kb)
    return json.dumps({"trace": trace, "results": [{"source": h["source"], "text": h["text"][:600]} for h in hits]})


ANALYST_TOOLS = [get_stock_position, get_sales_summary, get_upcoming_events]
