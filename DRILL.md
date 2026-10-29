# Interview drill: bi-copilot

Ten questions an interviewer will ask about this repo. Answer each in 60 seconds, out loud, without
looking. Then open the file named and check yourself.

**1. Walk me through what happens when I type "top 5 customers by gross margin last quarter".**
Router classifies it as a data question. Planner agent: the resolver maps "gross margin" to the
`gross_margin` metric id, "customers" to the `customer` dimension, "top 5" to top_n=5, and "last quarter"
to the last complete fiscal quarter before the as-of date (FY2027 Q1 when as-of is Sep 2026). The
retriever pulls the metric card, the dimension card and the fact/date table docs. SQL agent: the compiler
produces a draft from the YAML, the model refines it, the guard parses and allow-lists it, the executor
runs it read-only with a row cap. Analyst agent writes the summary and chart spec; the critic checks the
answer against the rows. Every node writes a trace event. (`agents/graph.py`)

**2. Why a semantic layer instead of letting the model write SQL against the schema?**
Because the hard failures are semantic, not syntactic: revenue that includes cancelled orders, "Q1" taken
as calendar Q1, region taken from the rep instead of the customer. The YAML makes each metric's expression
and mandatory filter the only version that exists; the compiler emits it, the prompt quotes it, the eval
gold SQL uses it. Changing a definition is a one-line YAML edit, not a prompt hunt. (`semantic/metrics.yaml`)

**3. What does "hybrid RAG" mean here and what is in the index?**
Three signals fused: BM25 over chunk text, dense embeddings (OpenAI or a hash embedder when offline) with
reciprocal rank fusion, and structured hits: whatever the resolver already mapped is forced into the context
with a bonus. Then a reranker reorders a wider candidate pool (LLM-scored, a flashrank cross-encoder, or a
deterministic lexical scorer) with structured hits and the base table docs pinned. Chunks are glossary sections
(one per `##`), one card per metric, one per dimension. Few dozen chunks, so BM25 is in memory; vectors are in
memory, pgvector or Milvus (Milvus Lite embedded for dev and CI, a server or Zilliz in production) behind one
interface. (`retrieval.py`, `rerank.py`)

**4. Which agent design patterns are in the graph and where?**
Plan-and-Execute: the planner emits a structured plan with steps, the supervisor executes it. ReAct: the SQL
agent's explore node binds three read-only tools (`describe_table`, `sample_values`, `validate_sql`) with
`bind_tools`; the model reasons, calls, observes, and writes SQL with the observations in context; a guard and
executor error comes back as an observation and it writes again (max 2 repairs). Reflection: the critic reviews
the draft answer and can send the supervisor back to the SQL agent with a fix hint (max 1 loop). Each agent is a
compiled LangGraph sub-graph added as a node of the supervisor graph. (`agents/`, `tools.py`)

**5. How do you stop it from running something dangerous?**
The guard is the only route to the database: sqlglot parse, exactly one statement, SELECT only, every
table in an allowlist, LIMIT added or lowered, comments and fences stripped. The executor refuses anything
that is not SELECT/WITH, sets a statement timeout and a row cap, and the DB role in production is
read-only. (`sqlguard.py`, `db.py`)

**6. How do you handle ambiguity?**
`metrics.yaml` has an `ambiguous` map ("margin" -> USD or %). The resolver flags a phrase unless a more
specific alias also matched ("gross margin %" wins over "margin"). The planner then returns
`needs_clarification` with options; the API returns status `clarify`, the UI shows chips, and the reply is
merged with the pending question on the next turn. Over A2A the task state is `input-required`.

**7. How do you measure whether it is right?**
`eval/questions.jsonl`: 60 questions, 50 with hand-written gold SQL. The runner executes both and compares
result sets (rounded, order-insensitive unless the gold has ORDER BY + LIMIT), plus status accuracy for the
clarify/refuse cases. Reported by tag so you can see that comparisons or relative time regress. The
deterministic compiler scores 100 %, which is the floor the LLM path must meet. (`eval/run.py`)

**8. What is A2A and what did you implement?**
Agent-to-Agent: a discovery document (Agent Card at `/.well-known/agent.json` listing skills and the
endpoint) and JSON-RPC `message/send` that returns a task with a status and artifacts (text part + data part
with SQL, rows, chart). `reporting_agent.py` is a second agent that only knows the card: it asks six
questions and compiles a Markdown briefing. (`a2a.py`, `agents/reporting_agent.py`)

**9. Why does the same SQL run on SQLite and Postgres?**
Time grouping uses `dim_date` columns (fiscal_year, fiscal_period, year_month) instead of date functions,
the compiler emits ANSI, and the guard transpiles with sqlglot to the engine's dialect. CI runs the full
test suite on both. (`sqlguard.py`, `.github/workflows/ci.yml`)

**10. How does it perform under load, and what would you do next?**
Answers are cached (Redis across replicas, in-process otherwise) under a key that includes the semantic-layer
hash, so a definition change invalidates everything; the guard caps rows and the executor sets a statement
timeout; the model is called at most planner + explore + write + analyst + critic times per question, and zero
times on a cache hit. Next: multi-fact metrics (attainment vs targets) in the compiler; LLM-as-judge on the
analyst's summary (numbers in the summary must appear in the rows); per-user row-level security pushed into
the WHERE clause from the caller's identity (the same pattern as the MDM assistant's geo filter); streaming
trace events over SSE; Elasticsearch in place of in-memory BM25 once the corpus outgrows one process.

---

Three things *not* to say: that the compiler "uses AI" (it does not; that is the point), that the eval
proves the LLM path is accurate (it proves the floor), and that A2A here is a full protocol implementation
(it is the card + `message/send` shape; no streaming, no push notifications).
