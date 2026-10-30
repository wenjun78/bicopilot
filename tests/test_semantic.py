from datetime import date

from bicopilot.semantic import Filter, Plan, TimeFilter

AS_OF = date(2018, 8, 31)  # last complete month in the Olist data


def test_calendar_periods(copilot):
    s = copilot.semantic
    assert s.calendar
    assert s.fiscal_of(date(2018, 2, 1)) == (2018, 1)
    assert s.fiscal_of(AS_OF) == (2018, 3)
    assert s.previous_quarter(2018, 1) == (2017, 4)
    assert s.period_label(2018, 2) == "2018 Q2"


def test_alias_resolution_and_filters(copilot):
    r = copilot.semantic.resolve("gmv by region in SP 2017", AS_OF)
    assert r.metrics == ["revenue"]
    assert r.dimensions == ["region"]  # "by region" keeps the grouping even though SP filters it
    assert r.filters == [Filter(dimension="state", value="SP")]
    assert r.time == TimeFilter(fiscal_year=2017, label="2017")


def test_two_letter_state_codes_need_a_cue(copilot):
    r = copilot.semantic.resolve("customers compared to last year", AS_OF)
    assert not r.filters  # "to" must not match the state TO
    assert copilot.semantic.resolve("orders in to", AS_OF).filters == [Filter(dimension="state", value="TO")]


def test_relative_time_resolves_against_as_of(copilot):
    r = copilot.semantic.resolve("orders last quarter", AS_OF)
    assert (r.time.fiscal_year, r.time.fiscal_quarter) == (2018, 2)
    r = copilot.semantic.resolve("revenue last month", AS_OF)
    assert (r.time.year_month_from, r.time.year_month_to) == ("2018-07", "2018-07")
    r = copilot.semantic.resolve("orders in Q1 2018", AS_OF)
    assert (r.time.fiscal_year, r.time.fiscal_quarter) == (2018, 1)


def test_ambiguity_needs_clarification(copilot):
    r = copilot.semantic.resolve("what is the delivery performance", AS_OF)
    assert "delivery" in r.ambiguous or "performance" in r.ambiguous
    plan = copilot.semantic.plan_from_resolution(r)
    assert plan.needs_clarification and len(plan.options) >= 2


def test_specific_phrase_beats_ambiguous_one(copilot):
    r = copilot.semantic.resolve("late delivery rate by seller state", AS_OF)
    assert r.metrics == ["late_delivery_rate"] and r.dimensions == ["seller_state"] and not r.ambiguous


def test_sellers_is_dimension_when_metric_present(copilot):
    r = copilot.semantic.resolve("top 5 sellers by revenue", AS_OF)
    assert r.metrics == ["revenue"] and r.dimensions == ["seller"] and r.top_n == 5 and not r.ambiguous


def test_worst_means_highest_for_bad_is_high_metrics(copilot):
    r = copilot.semantic.resolve("worst 5 states by late delivery rate", AS_OF)
    assert r.order == "desc" and r.top_n == 5
    assert copilot.semantic.resolve("worst 5 states by revenue", AS_OF).order == "asc"


def test_numeric_value_filter_needs_dimension_word(copilot):
    r = copilot.semantic.resolve("revenue from orders with review score 5", AS_OF)
    assert r.metrics == ["revenue"] and r.filters == [Filter(dimension="review_score", value="5")]
    assert not copilot.semantic.resolve("top 5 categories by revenue", AS_OF).filters


def test_compile_sql_uses_governed_definitions(copilot):
    plan = Plan(metrics=["revenue"], dimensions=["state"], time=TimeFilter(fiscal_year=2017))
    sql, notes = copilot.semantic.compile_sql(plan)
    assert "SUM(oi.price) AS revenue" in sql and "FROM fact_order_items oi" in sql
    assert "oi.status = 'delivered'" in sql and "d.year = 2017" in sql
    assert "JOIN dim_customer c" in sql and "JOIN dim_product" not in sql
    assert sql.endswith("LIMIT 100")


def test_compile_pivots_order_metric_to_item_grain_for_product_dims(copilot):
    plan = Plan(metrics=["avg_review_score"], dimensions=["category_group"])
    sql, notes = copilot.semantic.compile_sql(plan)
    assert "FROM fact_order_items oi" in sql and "JOIN fact_orders o" in sql and "JOIN dim_product p" in sql
    assert any("item grain" in n for n in notes)


def test_compile_comparison(copilot):
    plan = Plan(metrics=["revenue"], dimensions=["region"], time=TimeFilter(fiscal_year=2018), compare="yoy")
    sql, notes = copilot.semantic.compile_sql(plan)
    assert "d.year = 2017" in sql and "d.year = 2018" in sql
    assert "change_pct" in sql and any("YOY" in n for n in notes)
