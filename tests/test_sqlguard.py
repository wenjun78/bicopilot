import pytest

from bicopilot.sqlguard import GuardError, guard


def test_adds_limit_and_transpiles():
    r = guard(
        "SELECT c.region, SUM(o.product_value) FROM fact_orders o JOIN dim_customer c ON o.customer_key = c.customer_key GROUP BY c.region",
        "sqlite",
        500,
    )
    assert r.added_limit and "LIMIT 500" in r.sql and r.tables == {"fact_orders", "dim_customer"}


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM fact_orders",
        "DROP TABLE dim_customer",
        "SELECT 1; SELECT 2",
        "SELECT * FROM users",
        "INSERT INTO fact_orders VALUES (1)",
        "UPDATE dim_customer SET tier = 'Gold'",
        "",
    ],
)
def test_rejects_non_select_and_unknown_tables(sql):
    with pytest.raises(GuardError):
        guard(sql)


def test_lowers_oversized_limit_and_strips_fences():
    r = guard("```sql\nSELECT order_id FROM fact_orders LIMIT 99999;\n```", "postgresql", 500)
    assert "LIMIT 500" in r.sql and any("lowered" in w for w in r.warnings)


def test_cte_allowed():
    r = guard("WITH x AS (SELECT region FROM dim_customer) SELECT region FROM x", "sqlite")
    assert r.tables == {"dim_customer"}
