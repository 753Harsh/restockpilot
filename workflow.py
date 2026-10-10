"""The LangGraph workflow that joins the three agents, the code rules and the manager.

intake -> analyst -> scout -> planner -> controller --(send back once)--> scout
                                              |
                                              v
                                       approval_gate (pauses for the manager)
                                              |
                                              v
                                    place_orders -> final_memo -> END
"""
import operator
import uuid
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import db
import rules
from agents import analyst, controller, scout
from data_store import SKUS, STOCK, SUPPLIERS, load_cases
from llm import get_llm, mode_label
from tools import RUN_DATE, notify_n8n, place_purchase_order


class State(TypedDict, total=False):
    case: dict
    run_id: str
    mode: str
    log: Annotated[list, operator.add]
    injections: list
    findings: dict
    analyst_summary: str
    tool_calls: list
    cover_usual: dict
    scout: dict
    lines: list
    review: dict
    attempts: int
    decisions: dict
    orders: list
    n8n: str
    memo: str


def _logger(state, bucket):
    def log(step, msg):
        bucket.append(f"[{step}] {msg}")
        db.log(state["run_id"], state["case"]["case_id"], state["mode"], step, msg)
    return log


# ------------------------------------------------------------------ nodes
def intake(state):
    case = dict(state["case"])
    notes, found = rules.scan_for_injection(case.get("notes", ""))
    trig, found2 = rules.scan_for_injection(case.get("trigger", ""))
    case["notes"], case["trigger"] = notes, trig
    out = [f"[intake] case {case['case_id']} received, mode: {state['mode']}"]
    if found + found2:
        out.append(f"[intake] safety filter removed {len(found + found2)} instruction(s) aimed at the AI")
    return {"case": case, "injections": found + found2, "log": out, "attempts": 0}


def analyst_node(state):
    out = []
    findings, summary, calls = analyst.run(state["case"], get_llm(), _logger(state, out))
    cover = {}
    for sku in state["case"]["skus"]:
        fc = rules.forecast(sku, RUN_DATE, state["case"]["horizon_days"], findings[sku]["events_to_apply"])
        st = STOCK[sku]
        cover[sku] = round((st["on_hand"] + st["on_order"]) / max(fc["avg_daily"], 0.1), 1)
    out.append(f"[analyst] {summary}")
    return {"findings": findings, "analyst_summary": summary, "tool_calls": calls, "cover_usual": cover, "log": out}


def scout_node(state):
    out = []
    review = state.get("review") or {}
    feedback = "; ".join(review.get("problems", [])) if review.get("send_back") else None
    res = scout.run(state["case"], get_llm(), _logger(state, out), feedback=feedback,
                    avoid=review.get("avoid") if review.get("send_back") else None,
                    cover_by_sku=state.get("cover_usual"))
    if res["injections"]:
        out.append(f"[scout] removed {len(res['injections'])} suspicious sentence(s) from news pages")
    for c in res["checks"]:
        out.append(f"[scout] code check: {c}")
    out.append("[scout] suppliers: " + ", ".join(f"{k}->{v['supplier_id']}" for k, v in res["choices"].items()))
    return {"scout": res, "injections": state.get("injections", []) + res["injections"], "log": out}


def _news_factor(sku, signals, events_used):
    """Multiply news factors for this SKU, but drop any news about an event already in the calendar."""
    factor, pages, dropped = 1.0, [], []
    from data_store import EVENTS
    kws = [k for e in EVENTS if e["event"] in events_used for k in e["keywords"]]
    for sig in signals:
        if sku not in sig["affected_skus"] or sig["demand_factor"] == 1.0:
            continue
        text = (sig["page"] + " " + sig["summary"]).lower()
        if any(k in text for k in kws):
            dropped.append(sig["page"])
            continue
        factor *= sig["demand_factor"]
        pages.append(sig["page"])
    return rules.clamp_news_factor(factor), pages, dropped


def _make_line(case, sku, supplier_id, kind, findings, signals, risky, qty_override=None, choice=None):
    info, sup, stock = SKUS[sku], SUPPLIERS[supplier_id], STOCK[sku]
    events = findings[sku]["events_to_apply"]
    factor, pages, dropped = _news_factor(sku, signals, events)
    fc = rules.forecast(sku, RUN_DATE, case["horizon_days"], events, factor)
    q = rules.order_quantity(sku, supplier_id, stock, fc, case["horizon_days"], RUN_DATE)
    qty = q["suggested_qty"] if qty_override is None else int(qty_override)
    unit_cost = round(info["unit_cost"] * sup["price_index"], 2)
    line = {
        "line_id": f"{case['case_id']}-{sku}-{kind}", "kind": kind, "sku": sku, "sku_name": info["name"],
        "supplier_id": supplier_id, "supplier_name": sup["name"], "lead_time": q["lead_time"],
        "forecast_avg": fc["avg_daily"], "events_used": fc["events_used"], "news_factor": factor,
        "news_pages": pages, "news_dropped": dropped, "on_hand": stock["on_hand"], "on_order": stock["on_order"],
        "safety_stock": q["safety_stock"], "reorder_point": q["reorder_point"], "cover_days": q["cover_days"],
        "priority": q["priority"] if qty > 0 else "P4", "qty": qty, "unit_cost": unit_cost,
        "value": round(qty * unit_cost, 2), "calc_notes": q["notes"],
        "reason": (choice or {}).get("reason", "requested in the case notes"),
        "source_doc": (choice or {}).get("source_doc", ""),
    }
    flags = []
    if supplier_id in risky:
        flags.append(f"{sup['name']} has a news risk")
    if factor != 1.0:
        flags.append(f"demand changed x{factor} by news ({', '.join(pages)})")
    line["label"], line["approval_reasons"] = rules.approval_label(line, flags)
    return line


def planner(state):
    """Plain code: forecast, quantities, money and approval labels for every SKU."""
    case, sc = state["case"], state["scout"]
    lines, out = [], []
    for sku in case["skus"]:
        choice = sc["choices"].get(sku)
        if not choice:
            out.append(f"[planner] no allowed supplier for {sku}")
            continue
        policy = _make_line(case, sku, choice["supplier_id"], "policy", state["findings"],
                            sc["signals"], sc["risky_suppliers"], choice=choice)
        req_sup = case.get("requested_supplier")
        req_qty = (case.get("requested_qty") or {}).get(sku)
        if (req_sup and SKUS[sku]["category"] in SUPPLIERS[req_sup]["categories"]) or req_qty:
            req = _make_line(case, sku, req_sup or choice["supplier_id"], "requested", state["findings"],
                             sc["signals"], sc["risky_suppliers"], qty_override=req_qty)
            if req["label"] in ("AUTO", "MANAGER"):
                lines.append(req)                         # the request is allowed, use it
                continue
            lines.append(req)                             # keep the blocked request on record
            policy["kind"] = "replacement"
            policy["line_id"] = policy["line_id"].replace("-policy", "-replacement")
        lines.append(policy)
    for ln in lines:
        out.append(f"[planner] {ln['sku_name']}: {ln['qty']} from {ln['supplier_name']}, "
                   f"Rs {ln['value']:,.0f}, {ln['priority']}, {ln['label']}")
    return {"lines": lines, "log": out}


def controller_node(state):
    out = []
    r = controller.review(state["lines"], state["scout"]["risky_suppliers"], state.get("attempts", 0),
                          get_llm(), _logger(state, out))
    lines = state["lines"]
    if r["send_back"]:
        out.append("[controller] sent the plan back to the Scout: " + "; ".join(r["problems"]))
    else:
        out.append("[controller] plan accepted" + (f" with notes: {'; '.join(r['problems'])}" if r["problems"] else ""))
        # a problem code can still see after the one send-back goes to the manager
        for ln in lines:
            if ln["sku"] in r["avoid"] and ln["label"] == "AUTO":
                ln["label"] = "MANAGER"
                ln["approval_reasons"] = ["controller concern still open after send-back"]
                out.append(f"[controller] {ln['sku_name']} moved to manager approval")
    return {"review": r, "lines": lines, "attempts": state.get("attempts", 0) + 1, "log": out}


def after_review(state):
    return "scout" if state["review"]["send_back"] else "approval_gate"


def approval_gate(state):
    decisions = {}
    for ln in state["lines"]:
        decisions[ln["line_id"]] = {"AUTO": "AUTO", "NEVER": "BLOCKED", "NO_ORDER": "NO_ORDER"}.get(ln["label"], "WAITING")
    waiting = [ln for ln in state["lines"] if ln["label"] == "MANAGER"]
    out = []
    if waiting:
        answer = interrupt({"question": "Manager approval needed",
                            "lines": [{k: ln[k] for k in ("line_id", "sku_name", "supplier_name", "qty", "value",
                                                          "priority", "approval_reasons")} for ln in waiting]})
        for ln in waiting:
            ok = str(answer.get(ln["line_id"], "reject")).lower().startswith("approve")
            decisions[ln["line_id"]] = "APPROVED" if ok else "REJECTED"
            out.append(f"[manager] {ln['sku_name']}: {decisions[ln['line_id']]}")
    else:
        out.append("[approval] nothing needed a manager")
    return {"decisions": decisions, "log": out}


def place_orders(state):
    orders, out = [], []
    who = {"AUTO": "system (auto-approve)", "APPROVED": "store manager", "REJECTED": "store manager",
           "BLOCKED": "rules engine"}
    for ln in state["lines"]:
        d = state["decisions"][ln["line_id"]]
        if d in who:
            orders.append(place_purchase_order(state["case"]["case_id"], ln, d, who[d]))
    placed = [o for o in orders if o["decision"] in ("AUTO", "APPROVED")]
    out.append(f"[orders] {len(placed)} purchase order(s) placed, {len(orders) - len(placed)} decision(s) logged")
    urgent = [ln for ln in state["lines"] if ln["priority"] == "P1" or ln["label"] in ("MANAGER", "NEVER")]
    n8n = "no alert needed"
    if urgent:
        n8n = notify_n8n({"case_id": state["case"]["case_id"], "title": state["case"]["title"],
                          "store": "DS-07 Saket", "lines": [{k: ln[k] for k in ("sku_name", "supplier_name", "qty",
                          "value", "priority", "label")} for ln in urgent],
                          "decisions": state["decisions"]})
    out.append(f"[n8n] {n8n}")
    return {"orders": orders, "n8n": n8n, "log": out}


def final_memo(state):
    out = []
    text = controller.memo(state["case"], state["lines"], state["decisions"], get_llm(), _logger(state, out))
    out.append("[controller] final memo written")
    return {"memo": text, "log": out}


# ------------------------------------------------------------------ graph
def build_graph():
    g = StateGraph(State)
    g.add_node("intake", intake)
    g.add_node("analyst", analyst_node)
    g.add_node("scout", scout_node)
    g.add_node("planner", planner)
    g.add_node("controller", controller_node)
    g.add_node("approval_gate", approval_gate)
    g.add_node("place_orders", place_orders)
    g.add_node("final_memo", final_memo)
    g.add_edge(START, "intake")
    g.add_edge("intake", "analyst")
    g.add_edge("analyst", "scout")
    g.add_edge("scout", "planner")
    g.add_edge("planner", "controller")
    g.add_conditional_edges("controller", after_review, {"scout": "scout", "approval_gate": "approval_gate"})
    g.add_edge("approval_gate", "place_orders")
    g.add_edge("place_orders", "final_memo")
    g.add_edge("final_memo", END)
    return g.compile(checkpointer=InMemorySaver())


GRAPH = build_graph()


def start(case_id, thread_id=None):
    """Run a case until it finishes or pauses for the manager. Returns (thread_id, state, pause)."""
    case = next(c for c in load_cases() if c["case_id"] == case_id)
    thread_id = thread_id or f"{case_id}-{uuid.uuid4().hex[:6]}"
    cfg = {"configurable": {"thread_id": thread_id}}
    result = GRAPH.invoke({"case": case, "run_id": thread_id, "mode": mode_label(), "log": []}, cfg)
    return thread_id, GRAPH.get_state(cfg).values, (result.get("__interrupt__") or [None])[0]


def resume(thread_id, answers: dict):
    cfg = {"configurable": {"thread_id": thread_id}}
    GRAPH.invoke(Command(resume=answers), cfg)
    return GRAPH.get_state(cfg).values
