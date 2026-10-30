-- Olist warehouse: star schema built from the public Brazilian E-Commerce dataset (Olist, Kaggle, CC BY-NC-SA 4.0).
-- Portable DDL (SQLite and PostgreSQL). One row of fact_order_items = one item line; fact_orders = one order.

CREATE TABLE IF NOT EXISTS dim_date (
    date_key        DATE PRIMARY KEY,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,          -- calendar quarter 1-4
    month           INTEGER NOT NULL,
    month_name      VARCHAR(10) NOT NULL,
    year_month      VARCHAR(7) NOT NULL,       -- 'YYYY-MM'
    year_quarter    VARCHAR(7) NOT NULL,       -- '2018-Q1'
    day_of_week     INTEGER NOT NULL,          -- 1 = Monday
    is_weekend      INTEGER NOT NULL
);

-- Golden record: one row per real person (customer_unique_id). The raw data issues a new customer_id per order,
-- so 99,441 order-customers collapse to 96,096 people; this is the master-data step the copilot answers over.
CREATE TABLE IF NOT EXISTS dim_customer (
    customer_key        INTEGER PRIMARY KEY,
    customer_unique_id  VARCHAR(32) NOT NULL UNIQUE,
    customer_state      VARCHAR(2) NOT NULL,   -- state of the latest order
    customer_city       VARCHAR(60) NOT NULL,
    zip_prefix          VARCHAR(5) NOT NULL,
    region              VARCHAR(12) NOT NULL,  -- Brazilian macro-region derived from state
    first_order_date    DATE,
    last_order_date     DATE,
    order_count         INTEGER NOT NULL DEFAULT 0,
    is_repeat           INTEGER NOT NULL DEFAULT 0,   -- 1 if more than one order
    source_id_count     INTEGER NOT NULL DEFAULT 1    -- raw customer_id values merged into this record
);

CREATE TABLE IF NOT EXISTS dim_seller (
    seller_key      INTEGER PRIMARY KEY,
    seller_id       VARCHAR(32) NOT NULL UNIQUE,
    seller_state    VARCHAR(2) NOT NULL,
    seller_city     VARCHAR(60) NOT NULL,
    seller_region   VARCHAR(12) NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_product (
    product_key         INTEGER PRIMARY KEY,
    product_id          VARCHAR(32) NOT NULL UNIQUE,
    category            VARCHAR(60) NOT NULL,   -- English; 'unknown' when the source has no category
    category_pt         VARCHAR(60),            -- original Portuguese name
    category_group      VARCHAR(30) NOT NULL,   -- coarse grouping of the 73 categories
    weight_g            INTEGER,
    length_cm           INTEGER,
    height_cm           INTEGER,
    width_cm            INTEGER,
    photos_qty          INTEGER,
    name_length         INTEGER,
    description_length  INTEGER
);

CREATE TABLE IF NOT EXISTS fact_orders (
    order_id                VARCHAR(32) PRIMARY KEY,
    customer_key            INTEGER NOT NULL,
    purchase_date           DATE NOT NULL,          -- joins dim_date.date_key
    purchase_ts             TIMESTAMP NOT NULL,
    status                  VARCHAR(12) NOT NULL,   -- delivered | shipped | canceled | unavailable | invoiced | processing | created | approved
    approved_ts             TIMESTAMP,
    carrier_ts              TIMESTAMP,
    delivered_ts            TIMESTAMP,
    estimated_delivery_date DATE,
    delivery_days           REAL,                   -- purchase -> delivered, NULL unless delivered
    delivered_late          INTEGER,                -- 1 if delivered after the estimate, 0 if on time, NULL unless delivered
    days_late               REAL,                   -- delivered - estimated (negative = early), NULL unless delivered
    item_count              INTEGER NOT NULL DEFAULT 0,
    product_value           NUMERIC(12,2) NOT NULL DEFAULT 0,  -- SUM(item price)
    freight_value           NUMERIC(12,2) NOT NULL DEFAULT 0,  -- SUM(item freight)
    payment_value           NUMERIC(12,2),          -- SUM(payments); NULL when no payment record
    payment_type            VARCHAR(12),            -- main payment type (largest value)
    installments            INTEGER,                -- max installments across payments
    review_score            INTEGER,                -- latest review for the order, NULL if none
    review_has_text         INTEGER                 -- 1 if the review carries a comment
);

CREATE TABLE IF NOT EXISTS fact_order_items (
    order_id        VARCHAR(32) NOT NULL,
    item_seq        INTEGER NOT NULL,
    product_key     INTEGER NOT NULL,
    seller_key      INTEGER NOT NULL,
    customer_key    INTEGER NOT NULL,
    purchase_date   DATE NOT NULL,
    status          VARCHAR(12) NOT NULL,       -- denormalised from fact_orders for cheap filtering
    price           NUMERIC(12,2) NOT NULL,     -- BRL
    freight_value   NUMERIC(12,2) NOT NULL,     -- BRL
    shipping_limit_date DATE,
    PRIMARY KEY (order_id, item_seq)
);

CREATE TABLE IF NOT EXISTS fact_payments (
    order_id            VARCHAR(32) NOT NULL,
    payment_seq         INTEGER NOT NULL,
    payment_type        VARCHAR(12) NOT NULL,   -- credit_card | boleto | voucher | debit_card | not_defined
    installments        INTEGER NOT NULL,
    payment_value       NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (order_id, payment_seq)
);

CREATE TABLE IF NOT EXISTS fact_reviews (
    review_id           VARCHAR(32) NOT NULL,
    order_id            VARCHAR(32) NOT NULL,
    review_score        INTEGER NOT NULL,       -- 1-5
    has_title           INTEGER NOT NULL,
    has_message         INTEGER NOT NULL,
    comment_title       VARCHAR(200),           -- Portuguese; masked for the viewer role
    comment_message     TEXT,                   -- Portuguese; masked for the viewer role
    review_date         DATE NOT NULL,
    answered_ts         TIMESTAMP,
    PRIMARY KEY (review_id, order_id)
);

-- One row per zip prefix (the raw file has 1M rows with many points per prefix); mean coordinates.
CREATE TABLE IF NOT EXISTS dim_geo (
    zip_prefix      VARCHAR(5) PRIMARY KEY,
    lat             REAL NOT NULL,
    lng             REAL NOT NULL,
    city            VARCHAR(60) NOT NULL,
    state           VARCHAR(2) NOT NULL,
    point_count     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_orders_date ON fact_orders(purchase_date);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON fact_orders(customer_key);
CREATE INDEX IF NOT EXISTS ix_items_order ON fact_order_items(order_id);
CREATE INDEX IF NOT EXISTS ix_items_date ON fact_order_items(purchase_date);
CREATE INDEX IF NOT EXISTS ix_items_product ON fact_order_items(product_key);
CREATE INDEX IF NOT EXISTS ix_items_seller ON fact_order_items(seller_key);
CREATE INDEX IF NOT EXISTS ix_reviews_order ON fact_reviews(order_id);
