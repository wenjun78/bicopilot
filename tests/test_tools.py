from bicopilot.tools import build_tools


def names(tools):
    return {t.name: t for t in tools}


def test_describe_and_sample_are_bounded(copilot):
    t = names(build_tools(copilot.engine, 500))
    cols = t["describe_table"].invoke({"table": "dim_customer"})
    assert '"region"' in cols and '"is_repeat"' in cols
    assert t["describe_table"].invoke({"table": "users"}).startswith("error")
    vals = t["sample_values"].invoke({"table": "dim_customer", "column": "region"})
    assert vals == '["Center-West", "North", "Northeast", "South", "Southeast"]'
    assert t["sample_values"].invoke({"table": "dim_customer", "column": "password"}).startswith("error")


def test_validate_sql_tool_uses_guard(copilot):
    t = names(build_tools(copilot.engine, 500))
    assert "LIMIT 500" in t["validate_sql"].invoke({"sql": "SELECT region FROM dim_customer"})
    assert t["validate_sql"].invoke({"sql": "DELETE FROM fact_orders"}).startswith("error")


def test_sql_agent_explores_with_tools_before_writing(copilot):
    # "kindly" is not a word the semantic layer explains, so this leaves the fast path and the ReAct loop runs
    copilot.cache.clear()
    r = copilot.ask("revenue by city in SP 2017 kindly")
    nodes = [e["node"] for e in r["trace"]]
    assert nodes.index("sql.explore") < nodes.index("sql.write")
    tools = [s["tool"] for s in r["tool_steps"]]
    assert tools == ["describe_table", "sample_values", "validate_sql"]
    assert r["tool_steps"][1]["args"] == {"table": "dim_customer", "column": "customer_state"}
    assert r["status"] == "answered" and r["row_count"] > 0


def test_explore_failure_does_not_break_the_model_path(copilot, monkeypatch):
    """If the tool loop raises (e.g. the provider rejects function tools), the SQL agent still writes from the
    compiler draft instead of the whole model path collapsing to the deterministic fallback."""

    def boom(*_a, **_k):
        raise RuntimeError("Function tools with reasoning_effort are not supported")

    monkeypatch.setattr(copilot.llm, "react", boom)
    copilot.cache.clear()
    r = copilot.ask("revenue by state 2017 kindly", session_id="explore-fail")  # "kindly" keeps it off the fast path
    assert r["status"] == "answered" and r["row_count"] > 0
    explore = [e for e in r["trace"] if e.get("node") == "sql.explore"]
    assert explore and "RuntimeError" in str(explore[0])
    assert "deterministic fallback" not in (r.get("answer") or "").lower()
