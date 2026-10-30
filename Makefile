.PHONY: data demo test eval lint web docker report

data:            ## build the Olist warehouse CSVs from data/raw/olist.zip
	python data/build_olist.py

demo: data       ## zero-infra demo: SQLite + in-memory vectors + deterministic compiler, UI at :8080 if built
	python -m bicopilot

test:
	pytest -q

eval:            ## execution-accuracy report -> eval/report.md
	python eval/run.py --min 0.8

lint:
	ruff check . && ruff format --check .

web:             ## build the React UI into web/dist (served by the API at /)
	cd web && npm install && npm run build

docker:          ## Postgres + pgvector + API
	docker compose up --build

report:          ## second agent compiles a briefing over A2A (API must be running)
	python agents/reporting_agent.py --base http://127.0.0.1:8080 --out report.md
