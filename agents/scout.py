"""Agent 2, the Supplier Scout: "what is happening in the market, and who should supply?"

Part A: reads (scrapes) the market news pages and turns each into a signal.
Part B: searches the two knowledge bases (agentic RAG) and picks a supplier per SKU
        from a fixed candidate list.
Code then checks: supplier must be a real candidate and not blocked, the demand
factor is kept between 0.7 and 1.5, and a news factor is dropped when a calendar
event already covers the same effect (no double counting).
"""
import json
import re
from typing import List

from pydantic import BaseModel, Field

import knowledge
import rules
from data_store import SKUS, STOCK, SUPPLIERS
from llm import skill
from tools import RUN_DATE, scrape_news


class NewsSignal(BaseModel):
    page: str
    summary: str
    affected_skus: List[str] = Field(default_factory=list)
    affected_suppliers: List[str] = Field(default_factory=list)
    demand_factor: float = 1.0
    risk_note: str = ""


class SupplierChoice(BaseModel):
    sku: str
    supplier_id: str
    reason: str
    source_doc: str


class ScoutPlan(BaseModel):
    signals: List[NewsSignal] = Field(default_factory=list)
    choices: List[SupplierChoice]


# words that link a news page to a SKU (besides the SKU's own name)
SYNONYMS = {"SKU-401": ["cola", "cold drink"], "SKU-402": ["chips", "namkeen"],
            "SKU-403": ["noodles"], "SKU-501": ["ice cream", "frozen dessert"],
            "SKU-103": ["paneer"], "SKU-101": ["milk"], "SKU-201": ["onion"],
            "SKU-203": ["potato"], "SKU-303": ["sunflower", "cooking oil"]}
UP = ["stock up", "pushing up demand", "more than double", "spike", "rise in demand"]
DOWN = ["have fallen", "has fallen", "cutting", "cools"]
RISK = ["paused production", "shutdown", "shut down", "strike", "quality problem", "stock limit"]
SUPPLIER_DOCS = {"Dairy": "01_dairy_suppliers.md", "Fruits & Veg": "02_fresh_produce_suppliers.md",
                 "Staples": "03_fmcg_staples_suppliers.md", "Snacks": "03_fmcg_staples_suppliers.md",
                 "Beverages": "03_fmcg_staples_suppliers.md", "Bakery": "04_bakery_frozen_festive_suppliers.md",
                 "Frozen": "04_bakery_frozen_festive_suppliers.md", "Festive": "04_bakery_frozen_festive_suppliers.md"}


def _backup_signal(page, text, skus):
    low = text.lower()
    hit_skus = [s for s in skus if any(w in low for w in SYNONYMS.get(s, [SKUS[s]["name"].split()[0].lower()]))]
    sups = [sid for sid, s in SUPPLIERS.items() if s["name"].lower() in low]
    factor = 1.2 if any(w in low for w in UP) else 0.8 if any(w in low for w in DOWN) else 1.0
    risk = "page reports a supply problem" if any(w in low for w in RISK) else ""
    first = re.split(r"(?<=\.)\s", text.split(" ", 3)[-1])[0]
    return NewsSignal(page=page, summary=first[:220], affected_skus=hit_skus,
                      affected_suppliers=sups if risk else [], demand_factor=factor if hit_skus else 1.0,
                      risk_note=risk)


def _backup_choice(sku, risky, avoid, cover_by_sku):
    usual = STOCK[sku]["usual_supplier"]
    cands = rules.candidate_suppliers(sku)
    cands.sort(key=lambda c: c["supplier_id"] != usual)          # usual supplier first, then by score
    for c in cands:
        sid = c["supplier_id"]
        if c["status"] != "approved" or sid in risky or sid in avoid.get(sku, []):
            continue
        reason = (f"Usual supplier, approved, score {c['score']}, lead time {c['lead_time_days']} day(s)."
                  if sid == usual else
                  f"Best scored approved alternative ({c['score']}), lead time {c['lead_time_days']} day(s).")
        return SupplierChoice(sku=sku, supplier_id=sid, reason=reason,
                              source_doc="supplier_contracts/" + SUPPLIER_DOCS[SKUS[sku]["category"]])
    return None


def run(case, llm, log, feedback=None, avoid=None, cover_by_sku=None):
    avoid = avoid or {}
    # ---- Part A: scrape the pages (code does the fetching and the injection filter)
    pages, injections = [], []
    for p in case.get("news", []):
        page = scrape_news(p)
        pages.append(page)
        for bad in page.get("injection_found", []):
            injections.append(f"{p}: {bad}")

    # ---- knowledge base (agentic RAG) for each SKU and for the policy
    rag, rag_trace = [], []
    for sku in case["skus"]:
        q = f"supplier for {SKUS[sku]['name']} {SKUS[sku]['category']}"
        hits, trace = knowledge.agentic_search(q, "supplier_contracts", llm)
        rag += hits[:2]
        rag_trace.append(" > ".join(trace))
    hits, trace = knowledge.agentic_search("news demand change limit risk supplier approval", "procurement_policy", llm)
    rag += hits[:2]
    rag_trace.append(" > ".join(trace))

    candidates = {s: rules.candidate_suppliers(s) for s in case["skus"]}
    plan = None
    if llm is not None:
        try:
            prompt = (skill("SCOUT")
                      + f"\n\nCase: {case['trigger']}\nSKUs: {case['skus']}\nRun date: {RUN_DATE}"
                      + "\n\nNews pages (cleaned):\n" + json.dumps([{"page": p["page"], "title": p.get("title"), "text": p.get("text")} for p in pages])
                      + "\n\nCandidate suppliers per SKU:\n" + json.dumps(candidates)
                      + "\n\nUsual supplier per SKU:\n" + json.dumps({s: STOCK[s]["usual_supplier"] for s in case["skus"]})
                      + "\n\nDays of cover with the usual supplier:\n" + json.dumps(cover_by_sku or {})
                      + "\n\nKnowledge base passages:\n" + "\n\n".join(f"[{h['source']}] {h['text'][:500]}" for h in rag)
                      + (f"\n\nThe Finance Controller sent your plan back:\n{feedback}" if feedback else ""))
            plan = llm.with_structured_output(ScoutPlan).invoke(prompt)
            log("scout", f"AI read {len(pages)} pages and chose {len(plan.choices)} suppliers")
        except Exception as e:
            log("scout", f"AI failed ({type(e).__name__}: {str(e)[:120]}), using backup rules")
            plan = None
    if plan is None:
        signals = [_backup_signal(p["page"], p.get("text", ""), case["skus"]) for p in pages]
        risky = {s for sig in signals if sig.risk_note for s in sig.affected_suppliers}
        choices = [c for c in (_backup_choice(s, risky, avoid, cover_by_sku) for s in case["skus"]) if c]
        plan = ScoutPlan(signals=signals, choices=choices)

    # ---- code checks the AI's work
    checks = []
    signals = []
    for sig in plan.signals:
        d = sig.model_dump()
        d["affected_skus"] = [s for s in d["affected_skus"] if s in case["skus"]]
        d["affected_suppliers"] = [s for s in d["affected_suppliers"] if s in SUPPLIERS]
        f = rules.clamp_news_factor(d["demand_factor"])
        if f != d["demand_factor"]:
            checks.append(f"{sig.page}: demand factor {d['demand_factor']} limited to {f}")
        d["demand_factor"] = f
        signals.append(d)
    risky = {s for sig in signals if sig["risk_note"] for s in sig["affected_suppliers"]}
    choices = {}
    for c in plan.choices:
        if c.sku not in case["skus"]:
            continue
        ok_ids = {x["supplier_id"] for x in candidates[c.sku]}
        sup = SUPPLIERS.get(c.supplier_id)
        if c.supplier_id not in ok_ids or sup is None or sup["status"] == "blocked":
            checks.append(f"{c.sku}: AI picked {c.supplier_id}, not allowed, code replaced it")
            continue
        choices[c.sku] = c.model_dump()
    for sku in case["skus"]:
        if sku not in choices:
            fb = _backup_choice(sku, risky, avoid, cover_by_sku)
            if fb:
                choices[sku] = fb.model_dump()
                checks.append(f"{sku}: supplier filled in by code ({fb.supplier_id})")
    return {"signals": signals, "choices": choices, "injections": injections,
            "hidden_text": [h for p in pages for h in p.get("hidden_text_removed", [])],
            "rag_trace": rag_trace, "rag_sources": sorted({h["source"] for h in rag}),
            "checks": checks, "risky_suppliers": sorted(risky)}
