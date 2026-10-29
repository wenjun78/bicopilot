from bicopilot.tools import build_tools


def names(tools):
    return {t.name: t for t in tools}


def test_describe_and_sample_are_bounded(copilot):
    t = names(build_tools(copilot.engine, 500))
    cols = t["describe_table"].invoke({"table": "dim_customer"})
    assert '"region"' in cols and '"tier"' in cols
    assert t["describe_table"].invoke({"table": "users"}).startswith("error")
    vals = t["sample_values"].invoke({"table": "dim_customer", "column": "region"})
    assert vals == '["APAC", "EMEA", "LATAM", "NA"]'
    assert t["sample_values"].invoke({"table": "dim_customer", "column": "password"}).startswith("error")


def test_validate_sql_tool_uses_guard(copilot):
    t = names(build_tools(copilot.engine, 500))
    assert "LIMIT 500" in t["validate_sql"].invoke({"sql": "SELECT region FROM dim_customer"})
    assert t["validate_sql"].invoke({"sql": "DELETE FROM fact_orders"}).startswith("error")


def test_sql_agent_explores_with_tools_before_writing(copilot):
    r = copilot.ask("revenue by country in APAC FY2026")
    nodes = [e["node"] for e in r["trace"]]
    assert nodes.index("sql.explore") < nodes.index("sql.write")
    tools = [s["tool"] for s in r["tool_steps"]]
    assert tools == ["describe_table", "sample_values", "validate_sql"]
    assert r["tool_steps"][1]["args"] == {"table": "dim_customer", "column": "region"}
    assert r["status"] == "answered" and r["row_count"] > 0
