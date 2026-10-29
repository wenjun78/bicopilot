# bi-copilot: working notes for AI assistants and contributors

- Business definitions live in `semantic/metrics.yaml` only. Never hard-code a metric expression, a status
  filter or a fiscal rule in a prompt, a node or a test; read it from the semantic layer.
- The deterministic compiler (`semantic.compile_sql`) must keep scoring 100 % on `eval/questions.jsonl`
  (`python eval/run.py --min 1.0`). When adding a resolver feature, add a question with gold SQL.
- Every change: `ruff format . && ruff check . && pytest && python eval/run.py --min 0.9`.
- Keep the zero-infra path working: no API key, no Docker, no Postgres must still answer questions.
- Copyright is credited as "wjun78" (LICENSE). Commits are authored as `wenjun78 <151641864+wenjun78@users.noreply.github.com>`
  (the owner's GitHub account); never with a handle or email that reveals a year of birth.
- No real company data, names or numbers anywhere; `data/generate.py` is the only data source.
