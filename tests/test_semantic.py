from datetime import date

from bicopilot.semantic import Filter, Plan, TimeFilter

AS_OF = date(2026, 9, 28)  # FY2027 Q2


def test_fiscal_calendar(copilot):
    s = copilot.semantic
    assert s.fiscal_of(date(2025, 4, 1)) == (2026, 1)
    assert s.fiscal_of(date(2026, 3, 31)) == (2026, 4)
    assert s.fiscal_of(AS_OF) == (2027, 2)
    assert s.previous_quarter(2027, 1) == (2026, 4)


def test_alias_resolution_and_filters(copilot):
    r = copilot.semantic.resolve("net sales by geo in APAC FY26", AS_OF)
    assert r.metrics == ["revenue"]
    assert r.dimensions == ["region"]  # "by geo" keeps the grouping even though APAC filters it
    assert r.filters == [Filter(dimension="region", value="APAC")]
    assert r.time == TimeFilter(fiscal_year=2026, label="fy26")


def test_relative_time_resolves_against_as_of(copilot):
    r = copilot.semantic.resolve("orders last quarter", AS_OF)
    assert (r.time.fiscal_year, r.time.fiscal_quarter) == (2027, 1)
    r = copilot.semantic.resolve("orders in Q3", AS_OF)  # Q3 not yet complete in FY2027 -> FY2026 Q3
    assert (r.time.fiscal_year, r.time.fiscal_quarter) == (2026, 3)


def test_ambiguity_needs_clarification(copilot):
    r = copilot.semantic.resolve("what was the margin in EMEA", AS_OF)
    assert "margin" in r.ambiguous and set(r.ambiguous["margin"]) == {"gross_margin", "gross_margin_pct"}
    plan = copilot.semantic.plan_from_resolution(r)
    assert plan.needs_clarification and len(plan.options) == 2


def test_specific_phrase_beats_ambiguous_one(copilot):
    r = copilot.semantic.resolve("gross margin % by product category", AS_OF)
    assert r.metrics == ["gross_margin_pct"] and not r.ambiguous


def test_customers_is_dimension_when_metric_present(copilot):
    r = copilot.semantic.resolve("top 5 customers by revenue", AS_OF)
    assert r.metrics == ["revenue"] and r.dimensions == ["customer"] and r.top_n == 5 and not r.ambiguous


def test_compile_sql_uses_governed_definitions(copilot):
    plan = Plan(metrics=["revenue"], dimensions=["region"], time=TimeFilter(fiscal_year=2026))
    sql, notes = copilot.semantic.compile_sql(plan)
    assert "SUM(o.net_amount) AS revenue" in sql
    assert "o.status = 'completed'" in sql
    assert "d.fiscal_year = 2026" in sql
    assert "JOIN dim_customer c" in sql and "JOIN dim_product" not in sql
    assert sql.endswith("LIMIT 100")


def test_compile_comparison(copilot):
    plan = Plan(metrics=["revenue"], dimensions=["region"], time=TimeFilter(fiscal_year=2026), compare="yoy")
    sql, notes = copilot.semantic.compile_sql(plan)
    assert "d.fiscal_year = 2025" in sql and "d.fiscal_year = 2026" in sql
    assert "change_pct" in sql and any("YOY" in n for n in notes)
