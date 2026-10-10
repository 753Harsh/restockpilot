"""Plain-code rules. The AI suggests; these functions decide.

1. a filter for text that tries to give orders to the AI (prompt injection)
2. the demand forecast, safety stock and reorder point (simple maths)
3. supplier scoring
4. the approval gate: AUTO, MANAGER or NEVER for every order line
"""
import math
import re
from datetime import date, timedelta

from data_store import EVENTS, SKUS, SUPPLIERS, sales_for

# ---------------------------------------------------------------- limits
Z_95 = 1.65                 # 95 percent service level
REVIEW_DAYS = 1             # we review stock every day
AUTO_LIMIT = 25_000         # Rs, auto-approve up to this
MANAGER_LIMIT = 200_000     # Rs, above this the tool must not place the order
MSME_MAX_PAYMENT_DAYS = 45  # MSMED Act section 15
PERISHABLE_SHELF_USE = 0.70 # sell within 70 percent of shelf life
NEWS_FACTOR_MIN, NEWS_FACTOR_MAX = 0.7, 1.5

# ---------------------------------------------------------------- 1. injection filter
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|earlier) (instructions|rules)",
    r"disregard (your|the|all) (rules|instructions|policy)",
    r"(system|admin) (override|mode)",
    r"you are now",
    r"do not ask (a |the )?manager",
    r"approve (all|every) orders?",
    r"(skip|bypass) (the )?(approval|manager)",
    r"note to ai",
    r"is now (fully )?approved",
]


def scan_for_injection(text: str):
    """Return (clean_text, list_of_matches). Matching sentences are removed."""
    if not text:
        return "", []
    found = []
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        hits = [p for p in INJECTION_PATTERNS if re.search(p, sentence, re.I)]
        if hits:
            found.append(sentence.strip()[:160])
        else:
            kept.append(sentence)
    return " ".join(kept).strip(), found


# ---------------------------------------------------------------- 2. forecast maths
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0


def sales_summary(sku: str):
    """Facts about past sales: averages, spread, weekend effect and trend."""
    rows = sales_for(sku)                       # oldest first: [(date, units)]
    units = [u for _, u in rows]
    last7, older = units[-7:], units[:-7]
    weekday = [u for d, u in rows if d.weekday() < 5]
    weekend = [u for d, u in rows if d.weekday() >= 5]
    base = 0.6 * _mean(last7) + 0.4 * _mean(older)      # weighted moving average
    trend = (_mean(last7) - _mean(older)) / _mean(older) if _mean(older) else 0.0
    return {
        "sku": sku,
        "avg_last_7": round(_mean(last7), 1),
        "avg_days_8_to_28": round(_mean(older), 1),
        "base_daily": round(base, 1),
        "std_daily": round(_std(units), 1),
        "weekend_factor": round(_mean(weekend) / _mean(weekday), 2) if weekday else 1.0,
        "trend_pct": round(trend * 100, 1),
    }


def events_for(category: str, start: date, days: int):
    """Calendar events touching this category in [start, start+days)."""
    end = start + timedelta(days=days - 1)
    out = []
    for e in EVENTS:
        if category in e["categories"] and e["start"] <= end and e["end"] >= start:
            out.append(e)
    return out


def forecast(sku: str, run_date: date, horizon: int, event_names=None, news_factor=1.0):
    """Day-by-day forecast for `horizon` days. Only calendar events are used for uplift.

    event_names: the events the analyst asked for. Any name not in the calendar
    (or not touching this SKU) is ignored, so the AI cannot invent an uplift.
    """
    info = SKUS[sku]
    s = sales_summary(sku)
    allowed = {e["event"]: e for e in events_for(info["category"], run_date, horizon)}
    use = allowed if event_names is None else {n: allowed[n] for n in event_names if n in allowed}
    days = []
    for i in range(horizon):
        d = run_date + timedelta(days=i)
        q = s["base_daily"] * (s["weekend_factor"] if d.weekday() >= 5 else 1.0)
        for e in use.values():
            if e["start"] <= d <= e["end"]:
                q *= e["uplift"]
        days.append(q * news_factor)
    return {
        "daily": [round(x, 1) for x in days],
        "total": round(sum(days), 1),
        "avg_daily": round(sum(days) / horizon, 1),
        "std_daily": s["std_daily"],
        "events_used": list(use.keys()),
        "events_ignored": [n for n in (event_names or []) if n not in use],
    }


def clamp_news_factor(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 1.0
    return max(NEWS_FACTOR_MIN, min(NEWS_FACTOR_MAX, x))


def priority_from_cover(cover_days, lead_time, order_needed):
    if not order_needed:
        return "P4"
    if cover_days < lead_time:
        return "P1"
    if cover_days < lead_time + 1:
        return "P2"
    return "P3"


def order_quantity(sku, supplier_id, stock, fc, horizon, run_date):
    """Reorder point and order quantity, following the reorder policy document."""
    info, sup = SKUS[sku], SUPPLIERS[supplier_id]
    lead = sup["lead_time_days"]
    need_days = max(horizon, lead + REVIEW_DAYS)
    first_days = fc["daily"][:lead] if lead <= len(fc["daily"]) else fc["daily"]
    lead_demand = sum(first_days)
    safety = Z_95 * fc["std_daily"] * math.sqrt(lead)
    rop = lead_demand + safety
    position = stock["on_hand"] + stock["on_order"]
    cover = position / fc["avg_daily"] if fc["avg_daily"] else 99.0
    demand_needed = sum(fc["daily"][:need_days])
    raw = demand_needed + safety - position
    order_needed = position < rop or (horizon > lead + REVIEW_DAYS and raw > 0)
    qty = 0
    notes = []
    if order_needed and raw > 0:
        pack = info["case_pack"]
        qty = int(math.ceil(raw / pack) * pack)
        # perishables: do not order more than we can sell within 70% of shelf life
        if info["shelf_life_days"] <= 30:
            sell_days = max(1, int(info["shelf_life_days"] * PERISHABLE_SHELF_USE))
            limit = sum(fc["daily"][:sell_days]) + safety - position
            limit = max(0, int(math.floor(limit / pack) * pack))
            if qty > limit:
                notes.append(f"cut from {qty} to {limit} so stock sells within 70% of shelf life")
                qty = limit
        space = info["shelf_capacity"] - stock["on_hand"]
        space = max(0, int(math.floor(space / pack) * pack))
        if qty > space:
            notes.append(f"cut from {qty} to {space} to fit shelf space")
            qty = space
    else:
        order_needed = False
    return {
        "lead_time": lead,
        "safety_stock": round(safety, 1),
        "reorder_point": round(rop, 1),
        "position": position,
        "cover_days": round(cover, 1),
        "suggested_qty": qty,
        "order_needed": order_needed and qty > 0,
        "priority": priority_from_cover(cover, lead, order_needed and qty > 0),
        "notes": notes,
    }


# ---------------------------------------------------------------- 3. supplier scoring
def supplier_score(supplier_id):
    """40% price + 30% fill rate + 20% on-time + 10% lead time (scorecard document)."""
    s = SUPPLIERS[supplier_id]
    price = max(0.0, min(1.0, 1 - (s["price_index"] - 0.8) / 0.4))
    lead = 1 / s["lead_time_days"]
    return round(0.4 * price + 0.3 * s["fill_rate"] + 0.2 * s["on_time_rate"] + 0.1 * lead, 3)


def candidate_suppliers(sku):
    """Every supplier that sells this SKU's category, best score first. Blocked ones are listed but marked."""
    cat = SKUS[sku]["category"]
    out = []
    for sid, s in SUPPLIERS.items():
        if cat in s["categories"]:
            out.append({"supplier_id": sid, "name": s["name"], "status": s["status"],
                        "lead_time_days": s["lead_time_days"], "score": supplier_score(sid)})
    return sorted(out, key=lambda c: -c["score"])


# ---------------------------------------------------------------- 4. approval gate
def approval_label(line, risk_flags):
    """Return (label, reasons). label is AUTO, MANAGER, NEVER or NO_ORDER."""
    if line["qty"] <= 0:
        return "NO_ORDER", ["no order needed"]
    sup = SUPPLIERS[line["supplier_id"]]
    info = SKUS[line["sku"]]
    never, manager = [], []
    if sup["status"] == "blocked":
        never.append(f"supplier {sup['name']} is blocked")
    if sup["msme"] and sup["payment_days"] > MSME_MAX_PAYMENT_DAYS:
        never.append(f"MSME supplier on {sup['payment_days']} day terms (law allows max 45)")
    if line["value"] > MANAGER_LIMIT:
        never.append(f"value Rs {line['value']:,.0f} is above Rs 2,00,000")
    if line["qty"] > info["shelf_capacity"]:
        never.append(f"quantity {line['qty']} is above shelf capacity {info['shelf_capacity']}")
    if line["unit_cost"] >= info["mrp"]:
        never.append("purchase price is not below MRP")
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]\dZ[A-Z0-9]", sup["gstin"] or ""):
        never.append("supplier has no valid GSTIN")
    if never:
        return "NEVER", never
    if line["value"] > AUTO_LIMIT:
        manager.append(f"value Rs {line['value']:,.0f} is above Rs 25,000")
    if sup["status"] == "new":
        manager.append("supplier is new and not vetted")
    for f in risk_flags:
        manager.append(f"risk: {f}")
    if manager:
        return "MANAGER", manager
    return "AUTO", ["within auto-approve limits"]
