"""RestockPilot: Streamlit app with three pages.

1. Run a restock case   - the three agents, the code rules and the manager approval
2. Market news & policy - scrape a news page, ask the knowledge base
3. Store dashboard      - stock cover, purchase order log, how it works, run the tests

Run:  streamlit run app.py
"""
import base64
import json
import os
import zlib
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="RestockPilot", page_icon="📦", layout="wide")

# Streamlit Cloud keeps keys in "Secrets"; copy them into the environment before anything reads them
try:
    for key in ("GROQ_API_KEY", "GROQ_MODEL", "LLM_PROVIDER", "DATABASE_URL", "N8N_WEBHOOK_URL"):
        if key in st.secrets:
            os.environ[key] = str(st.secrets[key])
except Exception:
    pass

import db                                        # noqa: E402
import knowledge                                 # noqa: E402
import rules                                     # noqa: E402
import workflow                                  # noqa: E402
from data_store import SKUS, STOCK, SUPPLIERS, load_cases   # noqa: E402
from llm import ai_enabled, get_llm, mode_label  # noqa: E402
from tools import RUN_DATE, scrape_news          # noqa: E402

st.markdown("""
<style>
.badge {padding:2px 9px;border-radius:10px;font-size:0.8rem;font-weight:600;color:white}
.AUTO {background:#2e7d32} .MANAGER {background:#ef6c00} .NEVER {background:#c62828} .NO_ORDER {background:#78909c}
.P1 {background:#c62828} .P2 {background:#ef6c00} .P3 {background:#1565c0} .P4 {background:#78909c}
.step {border-left:3px solid #1565c0;padding:2px 10px;margin:2px 0;font-family:monospace;font-size:0.85rem}
</style>""", unsafe_allow_html=True)


def badge(text):
    return f'<span class="badge {text}">{text}</span>'


# ---------------------------------------------------------------- sidebar
st.sidebar.title("📦 RestockPilot")
st.sidebar.caption("Dark store replenishment agents for ZipBasket, DS-07 Saket (made-up company, made-up data)")
page = st.sidebar.radio("Page", ["Run a restock case", "Market news & policy", "Store dashboard & how it works"])
force = st.sidebar.toggle("Force backup mode (no AI)", value=os.getenv("FORCE_BACKUP") == "1")
os.environ["FORCE_BACKUP"] = "1" if force else "0"

# Replay: show a run saved from another server (used to make full-size report screenshots)
REPLAY = {}
if os.getenv("REPLAY_DIR"):
    for f in sorted(Path(os.getenv("REPLAY_DIR")).glob("*.json")):
        REPLAY[f.stem] = json.loads(f.read_text())
    pick = st.sidebar.selectbox("Recorded run", ["(none)"] + list(REPLAY))
    if pick != "(none)":
        rec = REPLAY[pick]
        st.session_state.run = {"tid": rec["thread"], "state": rec["state"], "pause": rec["pause"],
                                "cid": rec["case_id"], "replay": rec}
st.sidebar.markdown(f"**Mode:** {st.session_state.get('run', {}).get('replay', {}).get('mode') or mode_label()}")
_where = "recorded run" if st.session_state.get("run", {}).get("replay") else db.backend()
st.sidebar.markdown(f"**Run date:** {RUN_DATE:%d %b %Y}  \n**Database:** {_where}")

CASES = {c["case_id"]: c for c in load_cases()}


# ---------------------------------------------------------------- page 1
def record_box(run, stage):
    """A compact copy of this run, so it can be saved for the project report."""
    rec = {"case_id": run["cid"], "thread": run["tid"], "stage": stage, "mode": mode_label(),
           "recorded_at": datetime.now().strftime("%d %b %Y %H:%M"), "state": run["state"], "pause": run["pause"]}
    blob = base64.b64encode(zlib.compress(json.dumps(rec, default=str).encode())).decode()
    with st.expander("Run record (for the project report)"):
        st.code(blob, language=None, wrap_lines=True)


def show_lines(lines):
    rows = []
    for ln in lines:
        rows.append(f"<tr><td>{ln['sku_name']}<br><small>{ln['kind']}</small></td><td>{ln['supplier_name']}"
                    f"<br><small>lead {ln['lead_time']} d</small></td><td>{ln['forecast_avg']}/day"
                    f"<br><small>{', '.join(ln['events_used']) or 'no event'}"
                    f"{' | news x' + str(ln['news_factor']) if ln['news_factor'] != 1 else ''}</small></td>"
                    f"<td>{ln['on_hand']} + {ln['on_order']}<br><small>{ln['cover_days']} days cover</small></td>"
                    f"<td>{ln['reorder_point']}<br><small>SS {ln['safety_stock']}</small></td>"
                    f"<td><b>{ln['qty']}</b></td><td>Rs {ln['value']:,.0f}</td><td>{badge(ln['priority'])}</td>"
                    f"<td>{badge(ln['label'])}<br><small>{'; '.join(ln['approval_reasons'])}</small></td></tr>")
    st.markdown("<table style='width:100%;font-size:0.9rem'><tr><th>SKU</th><th>Supplier</th><th>Forecast</th>"
                "<th>On hand + on order</th><th>Reorder point</th><th>Qty</th><th>Value</th><th>Priority</th>"
                "<th>Decision</th></tr>" + "".join(rows) + "</table>", unsafe_allow_html=True)
    notes = [f"{ln['sku_name']}: {n}" for ln in lines for n in ln["calc_notes"]]
    notes += [f"{ln['sku_name']}: news from {', '.join(ln['news_dropped'])} ignored, the calendar event already covers it"
              for ln in lines if ln["news_dropped"]]
    if notes:
        st.caption("Code adjustments: " + " | ".join(notes))


def page_run():
    st.title("Run a restock case")
    st.write("Three AI agents check one restock request. Plain code does the maths and the safety rules, "
             "and the store manager approves anything risky.")
    c1, c2 = st.columns([3, 1])
    rep = (st.session_state.get("run") or {}).get("replay")
    cid = c1.selectbox("Sample case", list(CASES), format_func=lambda k: f"{k}  {CASES[k]['title']}",
                       index=list(CASES).index(rep["case_id"]) if rep else 0, disabled=bool(rep))
    case = CASES[cid]
    with c1.expander("Case details", expanded=True):
        st.write(f"**Trigger:** {case['trigger']}")
        st.write(f"**SKUs:** {', '.join(SKUS[s]['name'] for s in case['skus'])}   |   "
                 f"**Planning window:** {case['horizon_days']} days   |   **News pages:** {', '.join(case['news']) or 'none'}")
        if case.get("notes"):
            st.write(f"**Notes from the requester:** {case['notes']}")
    if c2.button("▶ Run case", type="primary", use_container_width=True):
        with st.spinner("Agents working..."):
            tid, state, pause = workflow.start(cid)
        st.session_state.run = {"tid": tid, "state": state, "pause": pause.value if pause else None, "cid": cid}
    run = st.session_state.get("run")
    if run and run.get("replay"):
        cid = run["cid"]
        st.caption(f"Recorded run of {cid}, {run['replay']['mode']}, {run['replay']['recorded_at']}")
    if not run or run["cid"] != cid:
        st.info("Pick a case and press Run case.")
        return
    state = run["state"]

    if state.get("injections"):
        st.error("🛡️ Safety filter caught text that tried to give orders to the AI. It was removed before any agent saw it:\n\n"
                 + "\n".join(f"- {x}" for x in state["injections"]))

    st.subheader("Agent 1 - Demand Analyst: what will we sell?")
    st.caption(state.get("analyst_summary", ""))
    f_rows = []
    for sku, f in state["findings"].items():
        s = rules.sales_summary(sku)
        f_rows.append({"SKU": SKUS[sku]["name"], "7-day avg": s["avg_last_7"], "Trend": f["trend"],
                       "Trend %": s["trend_pct"], "Events applied": ", ".join(f["events_to_apply"]) or "-",
                       "Stock risk": f["stock_risk"], "Code check": f.get("checked_by_code", "ok")})
    st.dataframe(pd.DataFrame(f_rows), hide_index=True, use_container_width=True)
    st.caption(f"Lookups made: {len(state.get('tool_calls', []))}  ("
               + ", ".join(sorted({c['tool'] for c in state.get('tool_calls', [])})) + ")")

    st.subheader("Agent 2 - Supplier Scout: what is the market saying, who should supply?")
    sc = state["scout"]
    if sc["signals"]:
        st.dataframe(pd.DataFrame([{"Page": s["page"], "What it means": s["summary"],
                                    "SKUs": ", ".join(s["affected_skus"]) or "-",
                                    "Demand factor": s["demand_factor"], "Risk": s["risk_note"] or "-"}
                                   for s in sc["signals"]]), hide_index=True, use_container_width=True)
    st.dataframe(pd.DataFrame([{"SKU": SKUS[k]["name"], "Supplier": SUPPLIERS[v["supplier_id"]]["name"],
                                "Reason": v["reason"], "Source": v["source_doc"]}
                               for k, v in sc["choices"].items()]), hide_index=True, use_container_width=True)
    with st.expander("Knowledge base search trace (agentic RAG)"):
        for t in sc["rag_trace"]:
            st.write("• " + t)

    st.subheader("Plan built by code, checked by Agent 3 - Finance Controller")
    show_lines(state["lines"])
    rv = state["review"]
    if state.get("attempts", 1) > 1:
        st.warning("↩️ The Finance Controller sent the first plan back to the Scout once. The plan above is the revised one.")
    if rv.get("comment"):
        st.caption("Controller: " + rv["comment"])

    if not run.get("replay"):
        record_box(run, "approval" if run["pause"] else "result")
    if run["pause"]:
        st.subheader("🧑‍💼 Manager approval needed")
        answers = {}
        for ln in run["pause"]["lines"]:
            a, b = st.columns([3, 1])
            a.markdown(f"**{ln['sku_name']}** - {ln['qty']} from {ln['supplier_name']}, Rs {ln['value']:,.0f} "
                       f"{badge(ln['priority'])}<br><small>{'; '.join(ln['approval_reasons'])}</small>",
                       unsafe_allow_html=True)
            answers[ln["line_id"]] = b.radio("Decision", ["approve", "reject"], key=ln["line_id"], horizontal=True)
        if not run.get("replay") and st.button("Submit decision", type="primary"):
            with st.spinner("Placing orders..."):
                state = workflow.resume(run["tid"], answers)
            st.session_state.run = {**run, "state": state, "pause": None}
            st.rerun()
        return

    st.subheader("Result")
    placed = [o for o in state.get("orders", []) if o["decision"] in ("AUTO", "APPROVED")]
    m1, m2, m3 = st.columns(3)
    m1.metric("Purchase orders placed", len(placed))
    m2.metric("Order value", f"Rs {sum(o['value'] for o in placed):,.0f}")
    m3.metric("Lines blocked or rejected", len(state.get("orders", [])) - len(placed))
    st.success("**Purchase memo**\n\n" + state.get("memo", ""))
    if state.get("orders"):
        st.dataframe(pd.DataFrame([{k: o[k] for k in ("po_id", "sku_name", "supplier_name", "qty", "value",
                                                        "decision", "decided_by")} for o in state["orders"]]),
                     hide_index=True, use_container_width=True)
    st.caption(f"n8n alert: {state.get('n8n')}")
    with st.expander("Full workflow log"):
        for line in state["log"]:
            st.markdown(f"<div class='step'>{line}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------- page 2
def page_news():
    st.title("Market news & policy")
    st.subheader("Read a news page (web scraping + safety filter)")
    pages = sorted(p.name for p in (Path(__file__).parent / "data" / "news").glob("*.html"))
    pg = st.selectbox("News page", pages)
    url = st.text_input("...or paste a live web link (works when the server has internet)")
    if st.button("Scrape and summarise"):
        res = scrape_news(url or pg)
        st.write(f"**{res.get('title', '')}**")
        st.write(res.get("text") or res.get("error"))
        if res.get("hidden_text_removed"):
            st.error("Hidden text removed: " + " | ".join(res["hidden_text_removed"]))
        if res.get("injection_found"):
            st.error("Instructions aimed at the AI removed: " + " | ".join(res["injection_found"]))
        llm = get_llm()
        if llm is not None and res.get("text"):
            try:
                st.info("AI summary: " + llm.invoke("Summarise for a dark store purchase planner in two "
                                                    f"sentences:\n{res['text']}").content)
            except Exception as e:
                st.caption(f"AI summary unavailable ({type(e).__name__})")

    st.subheader("Ask the knowledge base")
    st.caption("Two knowledge bases: procurement policy and supplier contracts. Search, grade, rewrite once, search again.")
    q = st.text_input("Question", "Can we accept 90 days credit from an MSME supplier?")
    if st.button("Ask"):
        ans, hits, trace = knowledge.answer(q, get_llm())
        st.write(ans)
        st.caption("Search trace: " + " > ".join(trace))
        st.dataframe(pd.DataFrame([{"Source": h["source"], "Section": h["heading"], "Similarity": h["score"]}
                                   for h in hits]), hide_index=True)


# ---------------------------------------------------------------- page 3
def page_dashboard():
    st.title("Store dashboard & how it works")
    rows = []
    for sku, info in SKUS.items():
        s = rules.sales_summary(sku)
        st_ = STOCK[sku]
        rows.append({"SKU": info["name"], "Category": info["category"], "On hand": st_["on_hand"],
                     "Avg daily sales": s["base_daily"],
                     "Days of cover": round((st_["on_hand"] + st_["on_order"]) / max(s["base_daily"], 0.1), 1),
                     "Usual supplier": SUPPLIERS[st_["usual_supplier"]]["name"]})
    df = pd.DataFrame(rows)
    st.subheader("Days of stock left (before any new order)")
    st.bar_chart(df.set_index("SKU")["Days of cover"], horizontal=True, height=480)
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.subheader(f"Purchase order log ({db.backend()})")
    log = db.recent_pos()
    st.dataframe(pd.DataFrame(log) if log else pd.DataFrame(columns=["po_id"]), hide_index=True, use_container_width=True)

    st.subheader("How it works")
    st.graphviz_chart("""digraph { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#e3f2fd", fontname=Helvetica];
      intake [label="Intake\\n(safety filter)", fillcolor="#ffebee"]; analyst [label="Agent 1\\nDemand Analyst\\n(tools)"];
      scout [label="Agent 2\\nSupplier Scout\\n(scraping + RAG)"]; planner [label="Planner\\n(code: forecast,\\nreorder point, rules)", fillcolor="#fff3e0"];
      controller [label="Agent 3\\nFinance Controller"]; gate [label="Approval gate\\n(manager)", fillcolor="#fff3e0"];
      orders [label="Place orders\\n+ n8n alert", fillcolor="#e8f5e9"]; memo [label="Final memo", fillcolor="#e8f5e9"];
      intake -> analyst -> scout -> planner -> controller -> gate -> orders -> memo;
      controller -> scout [label="send back once", style=dashed, color="#c62828"]; }""")
    st.markdown("""
| Rule (plain code) | What it does |
|---|---|
| Safety filter | Removes sentences in notes or news pages that try to give orders to the AI, and hidden web text |
| Forecast | Weighted moving average x weekend effect x calendar events x news factor (0.7 to 1.5) |
| Reorder point | Lead-time demand + safety stock (1.65 x spread x square root of lead time, 95% service) |
| Quantity limits | Full cases only, shelf space, perishables must sell within 70% of shelf life |
| Priority | P1 stock runs out before the truck arrives, P2 within a day of it, P3 order due, P4 no order |
| AUTO | Up to Rs 25,000, approved supplier, no news risk |
| MANAGER | Rs 25,000 to 2,00,000, new supplier, news risk, or news changed demand |
| NEVER | Blocked supplier, MSME on more than 45 day terms, above Rs 2,00,000, above shelf space, price not below MRP, no GSTIN |
""")
    if st.button("Run the 10 test cases in AI mode (slow: waits between cases for the free Groq limit)"):
        import subprocess, sys
        with st.spinner("Running tests with AI, this takes several minutes..."):
            out = subprocess.run([sys.executable, "test_cases.py", "--wait", "20"], capture_output=True, text=True,
                                 cwd=os.path.dirname(os.path.abspath(__file__)), env={**os.environ, "FORCE_BACKUP": "0"})
        st.code(out.stdout[-6000:] or out.stderr[-3000:])
    if st.button("Run the 10 test cases (backup mode)"):
        import subprocess, sys
        with st.spinner("Running tests..."):
            out = subprocess.run([sys.executable, "test_cases.py", "--backup"], capture_output=True, text=True,
                                 cwd=os.path.dirname(os.path.abspath(__file__)))
        st.code(out.stdout[-4000:] or out.stderr[-2000:])


{"Run a restock case": page_run, "Market news & policy": page_news,
 "Store dashboard & how it works": page_dashboard}[page]()
