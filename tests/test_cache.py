from bicopilot.cache import MemoryCache, cache_key, make_cache
from bicopilot.sqlguard import guard


def test_cache_key_normalises_whitespace_and_case():
    a = cache_key("Revenue  by Region", "", "2026-09-28", "v1")
    assert a == cache_key("revenue by region", "", "2026-09-28", "v1")
    assert a != cache_key("revenue by region", "", "2026-09-28", "v2")


def test_memory_cache_ttl_and_eviction():
    c = MemoryCache(ttl_s=60, max_items=2)
    c.set("a", {"x": 1})
    c.set("b", {"x": 2})
    c.set("c", {"x": 3})
    assert c.get("a") is None and c.get("c") == {"x": 3}


def test_unreachable_redis_falls_back_to_memory():
    assert make_cache("redis://127.0.0.1:1/0", 60).name == "memory"


def test_second_ask_is_served_from_cache(copilot):
    copilot.cache.clear()
    first = copilot.ask("orders by payment type 2017")
    second = copilot.ask("orders by payment type 2017")
    assert first["cached"] is False and second["cached"] is True
    assert second["rows"] == first["rows"] and second["id"] != first["id"]
    assert copilot.ask("what is the delivery performance")["cached"] is False  # clarifications are never cached


def test_guard_transpiles_to_other_dialects():
    sql = "SELECT c.region, SUM(o.product_value) AS revenue FROM fact_orders o JOIN dim_customer c ON o.customer_key = c.customer_key GROUP BY c.region"
    assert "LIMIT 500" in guard(sql, "mysql", 500).sql
    assert "TOP 500" in guard(sql, "mssql", 500).sql
