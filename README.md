# RestockPilot

Domain expert AI agents for **dark store stock replenishment**.
Individual project for *Agentic AI for Business Automation*, PGDM (BDA) 2025-27, Term 4, FORE School of Management.

ZipBasket is a made-up quick commerce company. Its Saket dark store (DS-07) must reorder milk, vegetables,
snacks and festival items every day. RestockPilot does the first draft of each reorder:

| Agent | Question it answers | How |
|---|---|---|
| 1. Demand Analyst | What will we sell, is stock running low? | Calls lookup tools in a ReAct loop (stock, sales, event calendar) |
| 2. Supplier Scout | What is the market saying, who should supply? | Scrapes market news pages, searches two knowledge bases (agentic RAG), picks suppliers |
| 3. Finance Controller | Is the plan right and safe? | Reviews the plan, sends it back once if needed, writes the purchase memo |

Plain code (`rules.py`) does the maths (forecast, safety stock, reorder point, quantity) and the safety rules
(prompt-injection filter, approval gate: AUTO / MANAGER / NEVER). A store manager approves risky lines.
All data is made up.

## Run it

```bash
python -m venv venv && venv\Scripts\activate        # Windows  (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                                # then put your Groq key in .env
streamlit run app.py
```

* No key? It runs in **backup mode** (fixed rules instead of AI), so the demo always works.
* Tests: `python test_cases.py --backup` (or `--wait 60` in AI mode to stay inside the free Groq limits).
* MCP: `python mcp_demo.py` starts `mcp_server.py` and calls its tools.
* PostgreSQL (optional): set `DATABASE_URL` in `.env`; otherwise SQLite in `output/`.
* n8n: import the files in `n8n/` (alert webhook, news scraper, Pinecone policy chat) and set
  `N8N_WEBHOOK_URL` to the alert webhook URL.
* Ollama (optional): `LLM_PROVIDER=ollama` and `OLLAMA_MODEL=llama3.1`.
* `data/make_data.py` rebuilds the sample data.

## Files

```
app.py               Streamlit UI (3 pages)
workflow.py          LangGraph graph: send-back loop, manager interrupt, checkpointer
agents/analyst.py    Agent 1, Demand Analyst
agents/scout.py      Agent 2, Supplier Scout
agents/controller.py Agent 3, Finance Controller
rules.py             Safety filter, forecast and reorder maths, supplier score, approval gate
tools.py             Lookup tools, news scraper, pretend purchase order tool, n8n alert
knowledge.py         Two knowledge bases in ChromaDB with TF-IDF vectors, self-correcting search
llm.py               Groq / Ollama connection, reads SKILL.md
db.py                PostgreSQL or SQLite log of every decision
data_store.py        Loads the sample data
SKILL.md             Agent instructions with version history
mcp_server.py        MCP server sharing the tools; mcp_demo.py is the client
test_cases.py        10 sample cases (4 trick cases) with expected results
data/                Sample store data, cases and news pages (all made up)
knowledge_base/      procurement_policy/ and supplier_contracts/
n8n/                 Exported n8n workflows
```
