-- Portable DDL (SQLite and PostgreSQL). Star schema for a B2B hardware/services business.

CREATE TABLE IF NOT EXISTS dim_date (
    date_key        DATE PRIMARY KEY,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,          -- calendar quarter 1-4
    month           INTEGER NOT NULL,          -- 1-12
    month_name      VARCHAR(10) NOT NULL,
    year_month      VARCHAR(7) NOT NULL,       -- 'YYYY-MM'
    fiscal_year     INTEGER NOT NULL,          -- FY starts 1 April: Apr 2025 - Mar 2026 = FY2026
    fiscal_quarter  INTEGER NOT NULL,          -- 1-4, FQ1 = Apr-Jun
    fiscal_period   VARCHAR(8) NOT NULL        -- 'FY26-Q1'
);

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id     INTEGER PRIMARY KEY,
    customer_name   VARCHAR(80) NOT NULL,
    segment         VARCHAR(20) NOT NULL,      -- Enterprise | SMB | Public Sector | Education
    industry        VARCHAR(30) NOT NULL,
    country         VARCHAR(30) NOT NULL,
    region          VARCHAR(10) NOT NULL,      -- APAC | EMEA | NA | LATAM
    tier            VARCHAR(10) NOT NULL,      -- Gold | Silver | Bronze
    onboarded_date  DATE NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 -- 1 = active, 0 = churned
);

CREATE TABLE IF NOT EXISTS dim_product (
    product_id      INTEGER PRIMARY KEY,
    product_name    VARCHAR(80) NOT NULL,
    category        VARCHAR(20) NOT NULL,      -- Laptop | Desktop | Server | Service | Accessory
    family          VARCHAR(30) NOT NULL,
    list_price      NUMERIC(12,2) NOT NULL,
    unit_cost       NUMERIC(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_sales_rep (
    sales_rep_id    INTEGER PRIMARY KEY,
    rep_name        VARCHAR(60) NOT NULL,
    region          VARCHAR(10) NOT NULL,
    team            VARCHAR(30) NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_orders (
    order_id        INTEGER PRIMARY KEY,
    order_date      DATE NOT NULL,             -- joins dim_date.date_key
    customer_id     INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    sales_rep_id    INTEGER NOT NULL,
    channel         VARCHAR(10) NOT NULL,      -- direct | partner | online
    status          VARCHAR(10) NOT NULL,      -- completed | cancelled | returned
    quantity        INTEGER NOT NULL,
    unit_price      NUMERIC(12,2) NOT NULL,    -- price after discount
    discount_pct    NUMERIC(5,2) NOT NULL,
    net_amount      NUMERIC(14,2) NOT NULL,    -- quantity * unit_price
    cost_amount     NUMERIC(14,2) NOT NULL     -- quantity * unit_cost
);

CREATE TABLE IF NOT EXISTS fact_targets (
    region          VARCHAR(10) NOT NULL,
    fiscal_year     INTEGER NOT NULL,
    fiscal_quarter  INTEGER NOT NULL,
    target_amount   NUMERIC(14,2) NOT NULL,
    PRIMARY KEY (region, fiscal_year, fiscal_quarter)
);

CREATE INDEX IF NOT EXISTS ix_orders_date ON fact_orders(order_date);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON fact_orders(customer_id);
CREATE INDEX IF NOT EXISTS ix_orders_product ON fact_orders(product_id);
