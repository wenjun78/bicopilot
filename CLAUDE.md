# bicopilot: working notes for AI assistants and contributors

This repository (`wenjun78/bicopilot`) is the one Render deploys (`https://bicopilot.onrender.com`) and the
portfolio links to. `wenjun78/bi-copilot` is the legacy repository; do not push new work there.

- Business definitions live in `semantic/metrics.yaml` only. Never hard-code a metric expression, a status
  filter or a fiscal rule in a prompt, a node or a test; read it from the semantic layer.
- The deterministic compiler (`semantic.compile_sql`) must keep scoring 100 % on `eval/questions.jsonl`
  (`python eval/run.py --min 1.0`). When adding a resolver feature, add a question with gold SQL.
- Every change: `ruff format . && ruff check . && pytest && python eval/run.py --min 0.9`.
- Keep the zero-infra path working: no API key, no Docker, no Postgres must still answer questions.
- Copyright is credited as "wjun78" (LICENSE). Commits are authored as `wenjun78 <151641864+wenjun78@users.noreply.github.com>`
  (the owner's GitHub account); never with a handle or email that reveals a year of birth.
- The only data is the public Olist Brazilian e-commerce dataset (`data/raw/olist.zip`, CC BY-NC-SA 4.0), loaded by
  `data/build_olist.py`. Never add Lenovo or any other private company data, names or numbers.
- Relative time is pinned to the as-of date in `semantic/metrics.yaml` (`as_of_default: 2018-08-31`); tests use the same.
