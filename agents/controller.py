"""Agent 3, the Finance Controller: "is this purchase plan right and safe?"

review(): checks the plan and can send it back to the Scout once.
memo():   writes the final purchase memo for the store manager.
Code runs its own checks too, so a problem is caught even if the AI misses it.
"""
import json
from typing import List

from pydantic import BaseModel, Field

from data_store import SUPPLIERS
from llm import skill
import rules


class Review(BaseModel):
    send_back: bool
    problems: List[str] = Field(default_factory=list)
    comment: str = ""


def code_checks(lines, risky_suppliers):
    """Problems plain code can see. Returns (problems, avoid map {sku: [supplier ids]})."""
    problems, avoid = [], {}
    for ln in lines:
        if ln["kind"] == "requested":
            continue
        if not ln.get("reason") or not ln.get("source_doc"):
            problems.append(f"{ln['sku']}: no reason or source document")
        if ln["supplier_id"] in risky_suppliers:
            problems.append(f"{ln['sku']}: {ln['supplier_name']} has a news risk")
            avoid.setdefault(ln["sku"], []).append(ln["supplier_id"])
        if ln["qty"] > 0 and ln["lead_time"] > ln["cover_days"]:
            faster = [c for c in rules.candidate_suppliers(ln["sku"])
                      if c["status"] == "approved" and c["lead_time_days"] < ln["lead_time"]
                      and c["supplier_id"] not in risky_suppliers]
            if faster:
                problems.append(f"{ln['sku']}: {ln['supplier_name']} takes {ln['lead_time']} days but stock "
                                f"lasts {ln['cover_days']} days; {faster[0]['name']} is faster")
                avoid.setdefault(ln["sku"], []).append(ln["supplier_id"])
    return problems, avoid


def review(lines, risky_suppliers, attempt, llm, log):
    problems, avoid = code_checks(lines, risky_suppliers)
    comment = ""
    if llm is not None:
        try:
            table = [{k: ln[k] for k in ("sku", "sku_name", "supplier_id", "supplier_name", "lead_time",
                                          "cover_days", "qty", "value", "reason", "source_doc")} for ln in lines]
            r = llm.with_structured_output(Review).invoke(
                skill("CONTROLLER") + "\n\nPurchase plan:\n" + json.dumps(table)
                + f"\nSuppliers with a news risk: {risky_suppliers}\nReview the plan.")
            comment = r.comment
            for p in r.problems:
                if p not in problems:
                    problems.append("AI: " + p)
            log("controller", f"AI review: send_back={r.send_back}, {len(r.problems)} problems")
        except Exception as e:
            log("controller", f"AI failed ({type(e).__name__}), using code checks only")
    # only code-confirmed problems can trigger a send-back; it happens at most once
    send_back = bool(avoid) and attempt == 0
    return {"send_back": send_back, "problems": problems, "avoid": avoid, "comment": comment}


def memo(case, lines, decisions, llm, log):
    placed = [l for l in lines if decisions.get(l["line_id"]) in ("AUTO", "APPROVED")]
    blocked = [l for l in lines if l["label"] == "NEVER"]
    rejected = [l for l in lines if decisions.get(l["line_id"]) == "REJECTED"]
    total = sum(l["value"] for l in placed)
    facts = {"case": case["title"], "placed": [(l["sku_name"], l["qty"], l["supplier_name"], l["value"]) for l in placed],
             "blocked": [(l["sku_name"], l["supplier_name"], l["approval_reasons"]) for l in blocked],
             "rejected_by_manager": [l["sku_name"] for l in rejected], "total_value": total}
    if llm is not None:
        try:
            text = llm.invoke(skill("CONTROLLER") + "\n\nWrite the final purchase memo (under 120 words) "
                              "from these facts only:\n" + json.dumps(facts)).content
            log("controller", "AI wrote the memo")
            return text
        except Exception as e:
            log("controller", f"AI memo failed ({type(e).__name__}), using template")
    parts = [f"Purchase memo for case {case['case_id']}: {case['title']}."]
    if placed:
        parts.append("Ordered: " + "; ".join(f"{q} x {n} from {s} (Rs {v:,.0f})" for n, q, s, v in facts["placed"])
                     + f". Total Rs {total:,.0f}.")
    else:
        parts.append("No purchase order was placed.")
    for n, s, why in facts["blocked"]:
        parts.append(f"Blocked: {n} from {s}, because {why[0]}.")
    if rejected:
        parts.append("Rejected by the manager: " + ", ".join(facts["rejected_by_manager"]) + ".")
    return " ".join(parts)
