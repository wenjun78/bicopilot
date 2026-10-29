# bi-copilot

An intelligent-BI agent: ask a sales warehouse questions in plain language and get a governed answer
(the number, the SQL that produced it, the rows, a chart) from a team of LangGraph agents.

```
"top 5 customers by gross margin last quarter"
   -> planner  : metric=gross_margin, dimension=customer, time=FY2027 Q1 ("last quarter" vs the as-of date), top_n=5
   -> sql agent: SELECT c.customer_name, SUM(o.net_amount - o.cost_amount) ... WHERE o.status='completed' AND d.fiscal_year=2027 AND d.fiscal_quarter=1 ... LIMIT 5
   -> analyst  : "Orion University 003 leads gross margin at USD 289,887 (28% of the 5 groups shown)."  + bar chart spec
   -> critic   : pass
```

Everything the agents know about the business comes from a **semantic layer** (`semantic/metrics.yaml`):
what "revenue" means (completed orders only, net of discount, USD), that the fiscal year starts in April,
that "region" is the customer's region and not the rep's. The model never guesses a definition. Change
the YAML and every prompt, the deterministic compiler and the eval set follow.

![Chat: answer, chart, clarification chips](https://raw.githubusercontent.com/wenjun78/bi-copilot/main/docs/chat.png)

Runs with **zero infrastructure** (SQLite, in-memory vectors, no API key: a deterministic compiler stands
in for the model) and with **Postgres + pgvector + an LLM** for the real thing. Same code path either way.

## Detailed engineering case study

For project background, business objectives, architecture, a step-by-step request walkthrough, explanations of technical terms (RAG, LangGraph, RRF, ReAct, A2A), engineering decisions, evaluation evidence, limitations, intended impact, and technical interview questions, see **[PROJECT_CASE_STUDY.md](PROJECT_CASE_STUDY.md)**.

This is a prototype using synthetic data. Deterministic evaluation results must not be presented as measured LLM accuracy or real business impact.

## What is in the box

| Piece | Where | What it does |
|---|---|---|
| Semantic layer | `semantic/metrics.yaml`, `semantic/glossary.md` | 11 governed metrics, 16 dimensions with aliases and allowed values, 6 business rules (calibers), ambiguity map |
| Field mapping | `src/bicopilot/semantic.py` | Phrase -> metric/dimension id, filter values, fiscal/relative time, top-N, YoY/QoQ; flags ambiguous phrases ("margin") |
| Deterministic compiler | same | Plan -> SQL from the YAML alone. The LLM refines it; without an LLM it *is* the answer |
| Hybrid RAG | `src/bicopilot/retrieval.py` | BM25 + embeddings fused with RRF over glossary chunks and metric/dimension cards, plus structured hits from the resolver. Vector store: in-memory, **pgvector** or **Milvus** (Milvus Lite embedded, or a server / Zilliz URI) |
| Reranking | `src/bicopilot/rerank.py` | Second pass over the fused candidates: LLM-scored, `flashrank` cross-encoder (local ONNX), or a deterministic lexical reranker; before/after positions in the trace |
| Multi-agent graph | `src/bicopilot/agents/` | Supervisor + 3 sub-graphs (planner, SQL agent, analyst) on one shared state. Patterns: Plan-and-Execute, ReAct with **bound tools** (`describe_table`, `sample_values`, `validate_sql`) and a repair loop, Reflection (critic) |
| Public-exposure controls | `src/bicopilot/limits.py` | Per-visitor minute/day limits on a salted IP hash, site-wide daily cap counted from the runs table, Bearer-protected `/runs`, proxy-aware client IP |
| Model-path safety | `src/bicopilot/service.py` | If the model path raises (outage, schema mismatch), the question is re-run on the deterministic path, labelled, and never cached |
| Answer cache | `src/bicopilot/cache.py` | Redis (shared across replicas) or in-process LRU; key includes the semantic-layer hash so a definition change invalidates every answer |
| SQL guard | `src/bicopilot/sqlguard.py` | sqlglot parse, single SELECT only, table allowlist, LIMIT enforcement, dialect transpile (Postgres -> SQLite/Postgres) |
| Executor | `src/bicopilot/db.py` | Read-only run with timeout and row cap |
| API | `src/bicopilot/api.py` | `POST /chat`, `GET /catalog`, `GET /runs`, `GET /runs/{id}` (trace), health |
| A2A surface | `src/bicopilot/a2a.py`, `agents/reporting_agent.py` | Agent Card at `/.well-known/agent.json`, JSON-RPC `message/send` -> task with artifacts. A second agent compiles a briefing over it; `.github/workflows/weekly-report.yml` runs it every Monday and commits `reports/` (process automation) |
| UI | `web/` | React + Vite + Recharts chat: answer, clarification chips, chart / table / SQL / agent-trace tabs, metric catalog |
| Eval | `eval/` | 60 evaluation questions (50 with hand-written gold SQL); execution accuracy by tag, status accuracy, repair count -> `eval/report.md` |
| Tests | `tests/` | 50 tests: resolver, compiler, guard, tools, rerankers, Milvus store, cache, full graph, API, A2A. CI runs them on SQLite + Milvus Lite and on Postgres + pgvector + Redis |

## Run it

```bash
git clone https://github.com/wenjun78/bicopilot.git && cd bi-copilot
python -m venv .venv && source .venv/bin/activate     # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
python data/generate.py          # 24k synthetic orders, 400 customers, 60 products, FY2025-FY2027
python -m bicopilot              # API on http://127.0.0.1:8080  (POST /chat works immediately)
```

UI: `cd web && npm install && npm run build`, then restart the API and open http://127.0.0.1:8080.
For hot reload during UI work: `npm run dev` (proxies to the API on 8080).

With a model: put `LLM_PROVIDER=openai`, `LLM_API_KEY=...`, `EMBEDDING_PROVIDER=openai` in `.env`
(see `.env.example`). Any OpenAI-compatible endpoint works via `LLM_BASE_URL`.

Postgres + pgvector + Redis: `docker compose up --build` (API on 8080, Postgres on 5432, Redis on 6379) or set
`DATABASE_URL=postgresql+psycopg://...`, `VECTOR_BACKEND=pgvector` and `CACHE_URL=redis://...` against your own
instances (Neon works). Milvus instead of pgvector: `pip install -e ".[milvus]"` and `VECTOR_BACKEND=milvus`
(Milvus Lite file by default; `MILVUS_URI`/`MILVUS_TOKEN` for a server or Zilliz Cloud). Cross-encoder reranking:
`pip install -e ".[rerank]"` and `RERANKER=flashrank`; with a key, `RERANKER=llm`.

```bash
make test        # pytest
make eval        # eval/run.py --min 0.8 -> eval/report.md
make report      # agents/reporting_agent.py: discovers the Agent Card, asks 6 questions over A2A, writes report.md
```

## How a question flows

```
                      ┌────────────┐
  question ─────────► │   router   │ greeting / meta / out-of-scope ──► respond
                      └─────┬──────┘
                            │ data question
                 ┌──────────▼──────────┐
                 │   PLANNER agent     │  resolve (field mapping) → plan (LLM corrects draft) → retrieve (hybrid RAG)
                 └──────────┬──────────┘
            needs clarification? ──────────────────────────────────────► respond (options)
                            │
                 ┌──────────▼──────────┐
                 │     SQL agent       │  explore (ReAct: describe_table / sample_values / validate_sql, ≤4 calls)
                 │                     │  → write (compiler draft + observations) → guard → execute
                 │                     │       ▲                                        │ error
                 │                     │       └───────────── repair ◄─────────────────┘  (max 2)
                 └──────────┬──────────┘
            unrecoverable? ────────────────────────────────────────────► respond (error + attempts)
                            │
                 ┌──────────▼──────────┐
                 │   ANALYST agent     │  interpret (summary, insights, chart spec, caveats) → critic
                 └──────────┬──────────┘
            critic: revise ──────────────► back to SQL agent with a fix hint (max 1)
                            │ pass
                            ▼
                         respond
```

Every node appends to a trace (`/runs/{id}`), so the UI can show which agent did what, which chunks
were retrieved and why (BM25 rank, dense rank, structured), every SQL attempt and the critic's verdict.

![Trace tab: plan, agent timings, retrieval ranks, critic](https://raw.githubusercontent.com/wenjun78/bi-copilot/main/docs/trace.png)

## Design choices worth asking about

- **Governed metrics, not free-form text-to-SQL.** Text-to-SQL on a raw schema fails on *meaning*
  ("revenue" with cancelled orders in it) long before it fails on syntax. The resolver maps language to
  metric ids; the compiler emits the metric's canonical expression and mandatory filters; the LLM is
  asked to *refine* a correct draft, not invent one. Hallucinated columns cannot survive the guard.
- **Clarify, don't guess.** "margin" maps to two metrics; "customers" alone has no metric. The planner
  returns options and the UI renders them as chips; the follow-up is merged with the pending question.
- **Relative time is resolved against an as-of date, with the fiscal calendar**, and the answer says
  what it resolved to (`"last quarter" resolved to FY2027 Q1`). YoY on an incomplete year compares
  year-to-date windows, not a half year against a full one.
- **The deterministic path is the test oracle.** 60 questions (50 with hand-written gold SQL); the eval
  compares *result sets*, not SQL strings. The compiler scores 100 % on it, which makes it a floor:
  if the LLM path scores lower, the model made it worse.
- **Portable SQL.** Time grouping goes through `dim_date` columns rather than date functions, so the
  same SQL runs on SQLite (CI, laptop) and Postgres (production). The guard transpiles with sqlglot.
- **Tools are bound, not implied.** The SQL agent's exploration step is a real `bind_tools` loop: the model
  decides whether to look at a table, sample a column's values or validate a draft, and sees each observation
  before writing. Tools are read-only and bounded, and every call is in the trace.
- **Rerank after fusion.** RRF is recall-oriented; the reranker (LLM, cross-encoder or lexical) reorders a wider
  pool before the context is cut, with structured hits and base table docs pinned so a reranker can never drop them.
- **A2A as a first-class surface.** The copilot is a tool other agents call. `reporting_agent.py`
  has no database access; it only has the Agent Card.

## Repository map

```
semantic/        metrics.yaml (the contract), glossary.md (RAG chunks)
data/            schema.sql, generate.py (deterministic synthetic warehouse), csv/ (generated)
src/bicopilot/   config, db, semantic, retrieval, rerank, tools, cache, sqlguard, llm, prompts, service, api, a2a
src/bicopilot/agents/  state, planner, sql_agent, analyst, graph (supervisor)
agents/          reporting_agent.py (second agent, talks A2A)
eval/            questions.jsonl, run.py, report.md
tests/           test suite (SQLite by default; TEST_DATABASE_URL switches to Postgres)
web/             React UI (Vite). web/dist is served by the API at /
```

## Deploy

**API on Render (free tier), about 15 minutes.**

1. render.com → **New → Blueprint** → connect this repo → Render reads `render.yaml` (one Docker web service,
   Singapore, health check on `/healthz`; `ADMIN_TOKEN` and `SECRET_SALT` are generated for you).
2. When asked for the values the blueprint leaves blank, paste your OpenAI key into `LLM_API_KEY`. Leave
   `DATABASE_URL` empty to run on the SQLite warehouse built into the image (question logs are lost on redeploy,
   fine for a demo); or paste a Neon Postgres URL with the `vector` extension enabled to persist logs and use pgvector.
3. First deploy builds the UI and the image (~5 min). The service address is `https://bicopilot.onrender.com`
   (Render adds a suffix if the name is taken; then set `CORS_ORIGINS`/`VITE_API_URL` accordingly).
4. `python scripts/smoke_live.py https://bicopilot.onrender.com` — health, three questions, a clarification
   round trip, the agent card. A `FALLBACK:` line means the model path raised and the deterministic compiler
   answered instead; the service stays up either way.

Public-exposure controls set by the blueprint: 6 questions/minute and 25/day per visitor (hashed IP),
300 model-backed answers/day site-wide, `/runs` behind `ADMIN_TOKEN`. Free-tier services sleep after 15 idle
minutes; the UI polls `/healthz` for up to 90 s while the server wakes and answers from the pre-computed
bundle meanwhile. A 5-minute uptime ping on `/healthz` keeps it warm.

**UI on GitHub Pages**: `.github/workflows/pages.yml` builds `web/` with `VITE_BASE=/bicopilot/` and
`VITE_API_URL` (repository variable; defaults to `https://bicopilot.onrender.com`) and publishes to
`https://<user>.github.io/bicopilot/`. The same build is copied into the portfolio site.

## Limits

- The semantic layer is one star schema. Multi-fact questions (attainment vs targets) are left to the LLM
  path with the glossary's join hints; the compiler does not emit them.
- The deterministic compiler covers breakdowns, filters, fiscal/calendar/relative time, top-N and YoY/QoQ.
  Nested rankings, share-of-total and window functions are LLM-only.
- Synthetic data: shapes are realistic (fiscal Q4 push, August dip, Gold-tier discounts), values are not.

## License

MIT, (c) wjun78.
