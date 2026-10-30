# Data glossary: Olist e-commerce warehouse

Chunked by `##` heading and indexed (keyword + embedding) for the retriever. Source data: Olist, Brazilian
E-Commerce Public Dataset (Kaggle, CC BY-NC-SA 4.0), orders from September 2016 to August 2018, amounts in BRL.

## Table fact_orders

One row per order (99,441). Columns: order_id (text key), customer_key (int, join to dim_customer),
purchase_date (date, join to dim_date.date_key), purchase_ts, status ('delivered' 96,478 | 'shipped' |
'canceled' 625 | 'unavailable' | 'invoiced' | 'processing' | 'created' | 'approved'), approved_ts, carrier_ts,
delivered_ts, estimated_delivery_date, delivery_days (purchase to delivery, NULL unless delivered),
delivered_late (1/0, NULL unless delivered), days_late (negative = early), item_count, product_value (sum of
item prices, BRL), freight_value (BRL), payment_value (sum of payments, BRL), payment_type (main type),
installments (max), review_score (latest review 1-5, NULL for 768 orders), review_has_text. Alias `o`.

## Table fact_order_items

One row per item line (112,650). Columns: order_id, item_seq (1..21), product_key (join to dim_product),
seller_key (join to dim_seller), customer_key (join to dim_customer, denormalised from the order),
purchase_date (join to dim_date), status (denormalised from the order), price (BRL), freight_value (BRL),
shipping_limit_date. Use this base for revenue, freight, items and anything by product or seller. Alias `oi`.

## Table fact_payments

One row per payment record (103,886). Columns: order_id, payment_seq, payment_type ('credit_card' |
'boleto' | 'voucher' | 'debit_card' | 'not_defined'), installments (1-24), payment_value (BRL). 2,961 orders
have more than one record (e.g. voucher plus card). fact_orders.payment_type keeps the largest record's
type and fact_orders.installments the maximum. Alias `pay`.

## Table fact_reviews

One row per review (99,224). Columns: review_id, order_id, review_score (1-5), has_title, has_message,
comment_title and comment_message (Portuguese free text, 40,977 with a message; masked for the viewer
role), review_date, answered_ts. 547 orders carry more than one review; fact_orders.review_score is the
latest. Alias `r`.

## Table dim_customer

Golden customer record, one row per person (96,096). The raw data issues a new customer_id for every
order; rows are merged on customer_unique_id, so 99,441 order-level IDs collapse to 96,096 people and
2,997 of them are repeat buyers (one person had 17 IDs). Columns: customer_key (int), customer_unique_id,
customer_state (2-letter UF, e.g. SP, RJ, MG), customer_city, zip_prefix, region (Southeast | South |
Northeast | North | Center-West), first_order_date, last_order_date, order_count, is_repeat (1/0),
source_id_count (raw IDs merged). Alias `c`.

## Table dim_seller

Sellers (3,095). Columns: seller_key (int), seller_id, seller_state, seller_city, seller_region. Most
sellers are in SP. A seller's state can differ from the buyer's state; "by state" means the customer's
state unless the question is about sellers. Alias `s`.

## Table dim_product

Products (32,951). Columns: product_key (int), product_id, category (English, 73 values, 'unknown' for
610 products without a category), category_pt (Portuguese source name), category_group (10 coarse groups:
Home & Furniture, Beauty & Health, Electronics, Sports & Leisure, Fashion, Books & Stationery, Baby & Pets,
Auto & Industry, Food & Drink, Other), weight_g, length_cm, height_cm, width_cm, photos_qty, name_length,
description_length. There are no product names in the source data, only IDs and categories. Alias `p`.

## Table dim_date

One row per calendar day from 2016-09-04 to 2018-11. Columns: date_key (date), year, quarter (1-4),
month (1-12), month_name, year_month ('2018-07'), year_quarter ('2018-Q3'), day_of_week (1 = Monday),
is_weekend. Always group and filter time through this table so the same SQL runs on SQLite and
PostgreSQL. Alias `d`.

## Table dim_geo

One row per zip prefix (19,010) with mean lat/lng, city, state and the number of raw points collapsed
(from 1,000,163 raw geolocation rows). Join on dim_customer.zip_prefix or dim_seller zip for maps. Alias `g`.

## Definition: revenue (GMV)

Revenue, also GMV, sales, turnover: SUM(fact_order_items.price) over item lines whose order status is
'delivered'. Freight is excluded. Currency BRL. Canceled, unavailable and in-flight orders do not count.

## Definition: freight and freight ratio

Freight revenue = SUM(oi.freight_value) on delivered lines. Freight ratio % = freight / product revenue
x 100. Freight is paid by the customer and set per item and seller distance, so the ratio is highest for
far-away states (North, Northeast) and heavy categories (furniture).

## Definition: orders and orders placed

"Orders" = delivered orders (COUNT DISTINCT order_id with status 'delivered'). "Orders placed" or "all
orders" = any status. Cancellation rate % = canceled / all orders placed x 100.

## Definition: customers and repeat customers

Customers = COUNT(DISTINCT customer_key) with a delivered order in the period, counted on the golden
record, never on the raw per-order customer_id. Repeat customer rate % = buying customers with
is_repeat = 1 / buying customers x 100. Only about 3% of people bought more than once.

## Definition: average order value and basket size

Average order value (AOV, average ticket) = AVG(fact_orders.product_value) over delivered orders, BRL,
freight excluded. Items per order (basket size) = AVG(fact_orders.item_count) over delivered orders.

## Definition: review score and low review rate

Average review score = AVG(fact_orders.review_score) over orders that have a review (1-5 stars; overall
about 4.1). Low review rate % = reviewed orders with score 1 or 2 / reviewed orders x 100. Late deliveries
are the strongest driver of low scores.

## Definition: delivery days, on-time and late delivery

Delivery days = days from purchase to delivered_ts, delivered orders only (mean about 12.5). On-time
delivery % = delivered orders with delivered_late = 0 / delivered orders with a known outcome x 100
(about 93%). Late delivery % = the complement (about 7%). Lateness is measured against the estimated
delivery date shown to the customer at purchase.

## Definition: payment types and installments

Payment type is the order's main record: credit_card (about 74% of orders), boleto (bank slip, 19%),
voucher, debit_card. Average installments = AVG(fact_orders.installments); only credit cards carry
installments above 1.

## Definition: calendar and as-of date

Periods are calendar years, quarters ('2018-Q2') and months ('2018-07'). The as-of date is 2018-08-31,
the last complete month: "last month" = 2018-07, "last quarter" = 2018-Q2, "this year" = 2018 to date,
"last year" = 2017. Orders after August 2018 are a sparse tail (20 orders) and fall outside the as-of
window. Year-over-year compares the same calendar window one year earlier.

## Definition: location

"State" and "region" are the customer's (dim_customer) unless the question is about sellers, merchants
or vendors (dim_seller). Brazilian macro-regions: Southeast (SP, RJ, MG, ES), South (PR, SC, RS),
Northeast (BA, PE, CE, ...), North (AM, PA, ...), Center-West (DF, GO, MT, MS). SP alone is about 42%
of customers.

## Definition: top N

"Top states / categories / sellers" means order by the requested metric descending and take the first
N; N defaults to 10. "Bottom" or "worst" means ascending.

## Definition: period comparison

Year-over-year (YoY) compares the same calendar window in consecutive years; quarter-over-quarter (QoQ)
compares consecutive quarters. Compute both periods in one query with two subqueries joined on the
dimension, or conditional aggregation (CASE WHEN d.year = X).

## Data quality notes

Known issues in the source, kept visible rather than silently fixed: 8 orders marked delivered without a
delivery timestamp (delivery metrics skip them); 610 products with no category ('unknown'); 2 categories
without an official English translation (translated here); 547 orders with duplicate reviews (latest
kept); 1 order without any payment record; geolocation points outside Brazil dropped before averaging.
