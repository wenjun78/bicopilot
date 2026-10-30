# Interview drill: bi-copilot

Ten questions an interviewer will ask about this repo. Answer each in 60 seconds, out loud, without
looking. Then open the file named and check yourself.

**1. Walk me through what happens when I type "top 5 categories by revenue last quarter".**
Router classifies it as a data question. Planner agent: the resolver maps "revenue" to the `revenue`
metric id (SUM of item price on delivered lines, base table fact_order_items), "categories" to the
`category` dimension, "top 5" to top_n=5, and "last quarter" to the last complete calendar quarter
before the as-of date (2018-Q2 when as-of is 2018-08-31). The retriever pulls the metric card, the
dimension card, the table docs and the matching dictionary chunk, then reranks. SQL agent: explore
with bound tools (describe dim_product, validate the compiler draft), write, guard, execute read-only
with a row cap. Analyst agent writes the summary and chart spec; the critic checks it against the rows.
Every node writes a trace event. (`agents/graph.py`)

**2. Why a semantic layer instead of letting the model write SQL against the schema?**
Because the hard failures are semantic, not syntactic: revenue that includes canceled orders, "customers"
counted on the raw per-order IDs instead of the golden record, state taken from the seller instead of the
buyer. The YAML makes each metric's expression
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
`metrics.yaml` has an `ambiguous` map ("delivery" -> delivery days or on-time rate; "performance" -> three metrics). The resolver flags a phrase unless a more
specific alias also matched ("on-time delivery rate" wins over "delivery"). The planner then returns
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
Time grouping uses `dim_date` columns (year, quarter, year_month, year_quarter) instead of date functions,
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

**11. Where does the data come from and what did you have to fix?**
Olist's public Brazilian e-commerce dataset (Kaggle, CC BY-NC-SA): nine raw tables, 99k orders. The loader
builds a star schema and resolves the master-data issues instead of hiding them: a golden customer record
merges 99,441 per-order IDs into 96,096 people (one person had 17 IDs; 2,997 repeat buyers only become
visible after the merge), duplicate reviews are collapsed to the latest, 610 products without a category
go to 'unknown', two untranslated categories are translated, geolocation collapses a million points to one
row per zip prefix. The dictionary's quality notes list every one of these. (`data/build_olist.py`)

**12. How does the viewer role work, and why three layers?**
Columns are flagged `masked: true` in the dictionary (customer IDs, review text). Layer one rewrites the
SQL on the sqlglot AST so masked columns become a literal before anything runs; layer two post-filters
the result rows by column name in case an alias or expression smuggled one through; layer three tells the
agents in the prompt not to select or describe them. Chat and the SQL console share all three. One layer
is a policy; three is a control. (`access.py`)

---

Three things *not* to say: that the compiler "uses AI" (it does not; that is the point), that the eval
proves the LLM path is accurate (it proves the floor), and that A2A here is a full protocol implementation
(it is the card + `message/send` shape; no streaming, no push notifications).

**13. It took 35 seconds to answer. What did you do about it?**
Counted the model calls: six or seven in sequence on the full agent path. Then asked which of them add
information for a question the semantic layer already understands: none before the analysis. So when the
resolver names the metric, nothing is ambiguous and no word of the question is unexplained, the plan and
SQL come from the compiler, the router and reranker run without the model, and the model only interprets
the rows and criticises that interpretation. Measured 14.3 s on the live service with two of those cuts
still to make; about 9 s expected now. The trace shows which path ran, so a slow answer is explainable:
it had a word the layer could not place, or a pasted file. (`agents/planner.py`, `agents/sql_agent.py`)

**14. A business user asks "where does 6.8 % come from?" What do they see?**
"How this was calculated" under the statement: the analyst's one-sentence method written from the SQL,
then the semantic layer's own account: the metric's definition and formula, the mandatory filters
(delivered orders only, delivery flag recorded), the period the question resolved to and the as-of date,
the grouping and the question's filters. It is deterministic, so it is there even when the model is down.
Viewers get that and the SQL; they do not get the agents' trace or retrieval ranks. (`semantic.method_detail`,
`web/src/components/MethodPanel.tsx`)
