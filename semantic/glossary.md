# Data glossary

This file is chunked by `##` heading and indexed (keyword + embedding) for the retriever.
Each chunk is small on purpose: the SQL agent only sees the chunks relevant to the question.

## Table fact_orders

One row per order line. Columns: order_id (int), order_date (date, join to dim_date.date_key),
customer_id (int, join to dim_customer), product_id (int, join to dim_product), sales_rep_id (int,
join to dim_sales_rep), channel ('direct' | 'partner' | 'online'), status ('completed' | 'cancelled' |
'returned'), quantity (int), unit_price (USD after discount), discount_pct (0-30), net_amount
(quantity * unit_price, USD), cost_amount (quantity * unit cost, USD). Alias `o`.

## Table dim_date

One row per calendar day from 2024-04-01. Columns: date_key (date, primary key), year, quarter
(calendar 1-4), month (1-12), month_name ('Jan'..'Dec'), year_month ('YYYY-MM'), fiscal_year
(int, FY starts 1 April so 2025-04-01 is fiscal_year 2026), fiscal_quarter (1-4, FQ1 = Apr-Jun),
fiscal_period ('FY26-Q1'). Always use this table for time grouping instead of date functions,
so the same SQL runs on SQLite and PostgreSQL. Alias `d`.

## Table dim_customer

Customer master. Columns: customer_id (int), customer_name, segment ('Enterprise' | 'SMB' |
'Public Sector' | 'Education'), industry ('Manufacturing', 'Financial Services', 'Healthcare', 'Retail',
'Technology', 'Government', 'Logistics'), country, region ('APAC' | 'EMEA' | 'NA' | 'LATAM'), tier
('Gold' | 'Silver' | 'Bronze'), onboarded_date, is_active (1 active, 0 churned). Alias `c`.

## Table dim_product

Product master. Columns: product_id (int), product_name, category ('Laptop' | 'Desktop' | 'Server' |
'Service' | 'Accessory'), family (e.g. 'Rack 2U', 'Premier Support'), list_price (USD before discount),
unit_cost (USD). Alias `p`.

## Table dim_sales_rep

Sales representatives. Columns: sales_rep_id (int), rep_name, region (rep's home region), team
(e.g. 'APAC Enterprise', 'EMEA Commercial'). A rep's region may differ from the customer's region.
Alias `r`.

## Table fact_targets

Revenue target per region per fiscal quarter. Columns: region, fiscal_year, fiscal_quarter,
target_amount (USD). Attainment = completed revenue for that region and fiscal quarter divided by
target_amount. Join on dim_customer.region = fact_targets.region AND dim_date.fiscal_year =
fact_targets.fiscal_year AND dim_date.fiscal_quarter = fact_targets.fiscal_quarter. Alias `t`.

## Definition: revenue

Revenue (also "sales", "net sales", "turnover", "bookings") is SUM(fact_orders.net_amount) over
order lines with status = 'completed'. It is net of discount. Cancelled and returned lines are
excluded. Currency is USD.

## Definition: gross margin

Gross margin in USD is SUM(net_amount - cost_amount) on completed lines. Gross margin % is that
divided by revenue, times 100. When someone says just "margin", ask whether they mean the USD
amount or the percentage.

## Definition: fiscal calendar

The fiscal year starts on 1 April. FY2026 runs 1 Apr 2025 to 31 Mar 2026. Fiscal quarters: FQ1
Apr-Jun, FQ2 Jul-Sep, FQ3 Oct-Dec, FQ4 Jan-Mar. "Q4" means fiscal Q4 (Jan-Mar) unless the
question says "calendar". dim_date.fiscal_period is the display label, e.g. 'FY26-Q3'.

## Definition: customer region vs rep region

"Revenue by region" uses the customer's region (dim_customer.region). Only use the sales rep's
region (dim_sales_rep.region) when the question is explicitly about reps, sellers or teams.

## Definition: active customers

Active customers for a period = COUNT(DISTINCT customer_id) with at least one completed order
in that period. This is different from dim_customer.is_active, which is a master-data churn flag.

## Definition: average order value

Average order value (AOV, "average deal size") = revenue / COUNT(DISTINCT order_id) over
completed orders. Note one order can have several order lines, so count distinct order_id.

## Definition: cancellation and return rates

Cancellation rate % = cancelled lines / all lines * 100. Return rate % = returned lines / all
lines * 100. These are the only metrics that include non-completed lines in the denominator.

## Definition: target attainment

Attainment % = completed revenue / target_amount * 100 for a region and fiscal quarter. Targets
exist only at region x fiscal quarter grain; do not compute attainment by product or by month.

## Definition: discount

discount_pct is the percentage taken off list price on that line. unit_price is already net of
the discount, so revenue must NOT subtract the discount again. "Average discount" is
AVG(discount_pct) on completed lines.

## Definition: top N

"Top customers" or "top products" means order by the requested metric descending and take the
first N; N defaults to 10. "Bottom" means ascending.

## Definition: period comparison

Year-over-year (YoY) compares the same fiscal period in consecutive fiscal years. Quarter-over-
quarter (QoQ) compares consecutive fiscal quarters. Compute both periods in one query with
conditional aggregation (CASE WHEN d.fiscal_year = X) rather than two queries.
