"""Agent 1, the Demand Analyst: "what will this store sell, and is stock running low?"

AI mode: the model decides which lookup tools to call (ReAct loop), then gives
structured findings. Code then checks the work: every SKU must have been looked up,
and only real calendar events are accepted.
Backup mode: the same facts come from plain rules.
"""
import json
from typing import List, Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

import rules
from data_store import SKUS
from llm import skill
from tools import ANALYST_TOOLS, RUN_DATE, stock_position, upcoming_events

TOOLS = {t.name: t for t in ANALYST_TOOLS}


class SkuFinding(BaseModel):
    sku: str
    trend: Literal["up", "flat", "down"]
    events_to_apply: List[str] = Field(default_factory=list, description="calendar event names, exactly as returned")
    stock_risk: str = Field(description="one sentence on whether stock is at risk")


class DemandFindings(BaseModel):
    items: List[SkuFinding]
    summary: str = Field(description="two sentences for the planner")


def _backup(case):
    items = []
    for sku in case["skus"]:
        s = rules.sales_summary(sku)
        trend = "up" if s["trend_pct"] > 5 else "down" if s["trend_pct"] < -5 else "flat"
        evs = [e["event"] for e in upcoming_events(sku, case["horizon_days"])]
        pos = stock_position(sku)
        cover = (pos["on_hand"] + pos["on_order"]) / max(s["base_daily"], 0.1)
        risk = (f"About {cover:.1f} days of stock left at the usual rate"
                + (", which is tight." if cover < 2 else "."))
        items.append(SkuFinding(sku=sku, trend=trend, events_to_apply=evs, stock_risk=risk))
    names = ", ".join(SKUS[s]["name"] for s in case["skus"])
    return DemandFindings(items=items, summary=f"Checked sales, stock and the calendar for {names}.")


def run(case, llm, log):
    """Returns (findings dict per SKU, summary, list of tool calls made)."""
    calls = []
    findings = None
    if llm is not None:
        try:
            model = llm.bind_tools(ANALYST_TOOLS)
            msgs = [SystemMessage(skill("ANALYST")),
                    HumanMessage(f"Case {case['case_id']}: {case['trigger']}\nSKUs: {', '.join(case['skus'])}\n"
                                 f"Planning window: {case['horizon_days']} days from {RUN_DATE}.")]
            results = []
            for _ in range(12):                                   # think -> call tool -> read result
                ai = model.invoke(msgs)
                msgs.append(ai)
                if not ai.tool_calls:
                    break
                for tc in ai.tool_calls:
                    fn = TOOLS.get(tc["name"])
                    out = fn.invoke(tc["args"]) if fn else json.dumps({"error": "no such tool"})
                    calls.append({"tool": tc["name"], "args": tc["args"]})
                    results.append(f"{tc['name']}({json.dumps(tc['args'])}) -> {out}")
                    msgs.append(ToolMessage(content=out, tool_call_id=tc["id"]))
            prompt = (skill("ANALYST") + "\n\nTool results:\n" + "\n".join(results)
                      + f"\n\nGive your findings for SKUs {case['skus']}.")
            findings = llm.with_structured_output(DemandFindings).invoke(prompt)
            log("analyst", f"AI made {len(calls)} tool calls")
        except Exception as e:
            log("analyst", f"AI failed ({type(e).__name__}: {str(e)[:120]}), using backup rules")
            findings = None
    if findings is None:
        findings = _backup(case)
        calls = [{"tool": t, "args": {"sku": s}} for s in case["skus"]
                 for t in ("get_stock_position", "get_sales_summary", "get_upcoming_events")]

    # ---- code checks the AI's work
    by_sku = {f.sku: f.model_dump() for f in findings.items if f.sku in case["skus"]}
    backup = {f.sku: f.model_dump() for f in _backup(case).items}
    for sku in case["skus"]:
        looked_up = {c["tool"] for c in calls if c["args"].get("sku") == sku}
        missing = {"get_stock_position", "get_sales_summary", "get_upcoming_events"} - looked_up
        if sku not in by_sku:
            by_sku[sku] = backup[sku]
            by_sku[sku]["checked_by_code"] = "AI gave no finding, code filled it in"
        elif missing:
            by_sku[sku]["checked_by_code"] = f"AI skipped {sorted(missing)}, code re-ran them"
        real = [e["event"] for e in upcoming_events(sku, case["horizon_days"])]
        chosen = by_sku[sku]["events_to_apply"]
        bad = [e for e in chosen if e not in real]          # invented or wrong events are removed
        missed = [e for e in real if e not in chosen]       # the calendar is the source of truth
        if bad:
            by_sku[sku]["events_rejected"] = bad
        if missed:
            by_sku[sku]["events_added_by_code"] = missed
        by_sku[sku]["events_to_apply"] = real
    return by_sku, findings.summary, calls
