# Sales briefing - 2026-11-02

Compiled by reporting-agent from `bi-copilot` v0.1.0 over A2A.

## revenue by region this fiscal year

_completed_ - Southeast leads revenue at BRL 4.79M (66% of the 5 groups shown).

- 5 groups returned
- top group: Southeast

Note: "this year" resolved to 2018 (calendar periods, as of 2018-08-31).

| region | revenue |
|---|---|
| Southeast | 4,786,051.31 |
| South | 1,033,908.97 |
| Northeast | 785,374.79 |
| Center-West | 451,261.13 |
| North | 161,528.92 |

<details><summary>SQL</summary>

```sql
SELECT
  c.region AS region,
  SUM(oi.price) AS revenue
FROM fact_order_items AS oi
JOIN dim_date AS d
  ON oi.purchase_date = d.date_key
JOIN dim_customer AS c
  ON oi.customer_key = c.customer_key
WHERE
  oi.status = 'delivered' AND d.year = 2018
GROUP BY
  c.region
ORDER BY
  revenue DESC NULLS FIRST
LIMIT 100
```

</details>

## revenue by region year over year FY2026

_completed_ - Southeast leads revenue at BRL 8.65M (65% of the 5 groups shown).

- 5 groups returned
- top group: Southeast

Note: "fy2026" resolved to 2026 (calendar periods, as of 2018-08-31).

| region | revenue |
|---|---|
| Southeast | 8,650,035.74 |
| South | 1,900,796.24 |
| Northeast | 1,497,114.09 |
| Center-West | 846,358.19 |
| North | 327,193.85 |

<details><summary>SQL</summary>

```sql
SELECT
  c.region AS region,
  SUM(oi.price) AS revenue
FROM fact_order_items AS oi
JOIN dim_customer AS c
  ON oi.customer_key = c.customer_key
WHERE
  oi.status = 'delivered'
GROUP BY
  c.region
ORDER BY
  revenue DESC NULLS FIRST
LIMIT 100
```

</details>

## gross margin % by product category this fiscal year

_input-required_ - Which metric should I use?

## top 5 customers by revenue last quarter

_completed_ - one-time leads revenue at BRL 2.67M (95% of the 2 groups shown).

- 2 groups returned
- top group: one-time

Note: "last quarter" resolved to 2018 Q2 (calendar periods, as of 2018-08-31).

| customer_type | revenue |
|---|---|
| one-time | 2,671,765.26 |
| repeat | 135,391.38 |

<details><summary>SQL</summary>

```sql
SELECT
  CASE WHEN c.is_repeat = 1 THEN 'repeat' ELSE 'one-time' END AS customer_type,
  SUM(oi.price) AS revenue
FROM fact_order_items AS oi
JOIN dim_date AS d
  ON oi.purchase_date = d.date_key
JOIN dim_customer AS c
  ON oi.customer_key = c.customer_key
WHERE
  oi.status = 'delivered' AND d.year = 2018 AND d.quarter = 2
GROUP BY
  CASE WHEN c.is_repeat = 1 THEN 'repeat' ELSE 'one-time' END
ORDER BY
  revenue DESC NULLS FIRST
LIMIT 5
```

</details>

## cancellation rate by channel

_completed_ - Cancellation rate is 0.6% for the requested scope.

| cancellation_rate |
|---|
| 0.63 |

<details><summary>SQL</summary>

```sql
SELECT
  SUM(CASE WHEN o.status = 'canceled' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) AS cancellation_rate
FROM fact_orders AS o
LIMIT 500
```

</details>

## orders this quarter vs last quarter

_completed_ - Order count is 12,510 vs 19,646 in the prior period (-36.3%).

Note: "this quarter vs last" resolved to 2018 Q3 (calendar periods, as of 2018-08-31).

| current_period | previous_period | change | change_pct |
|---|---|---|---|
| 12510 | 19646 | -7136 | -36.32 |

<details><summary>SQL</summary>

```sql
SELECT
  cur.order_count AS current_period,
  prev.order_count AS previous_period,
  cur.order_count - prev.order_count AS change,
  (
    cur.order_count - prev.order_count
  ) * 100.0 / NULLIF(prev.order_count, 0) AS change_pct
FROM (
  SELECT
    COUNT(DISTINCT o.order_id) AS order_count
  FROM fact_orders AS o
  JOIN dim_date AS d
    ON o.purchase_date = d.date_key
  WHERE
    o.status = 'delivered' AND d.year = 2018 AND d.quarter = 3
) AS cur, (
  SELECT
    COUNT(DISTINCT o.order_id) AS order_count
  FROM fact_orders AS o
  JOIN dim_date AS d
    ON o.purchase_date = d.date_key
  WHERE
    o.status = 'delivered' AND d.year = 2018 AND d.quarter = 2
) AS prev
LIMIT 500
```

</details>
