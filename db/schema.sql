-- ============================================================
-- SmartRetail 360 — Database Schema
-- PostgreSQL 16
-- ============================================================

-- Drop tables in reverse dependency order (for clean reruns)
DROP TABLE IF EXISTS drift_reports CASCADE;
DROP TABLE IF EXISTS model_runs CASCADE;
DROP TABLE IF EXISTS customer_features CASCADE;
DROP TABLE IF EXISTS demand_weekly CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

-- ============================================================
-- DIMENSION TABLES (the "who" and "what")
-- ============================================================

CREATE TABLE customers (
    customer_id     VARCHAR(50)  PRIMARY KEY,
    country         VARCHAR(100),
    first_purchase  DATE,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

COMMENT ON TABLE customers IS 'One row per unique customer';
COMMENT ON COLUMN customers.customer_id IS 'Unique customer identifier from source data';

CREATE TABLE products (
    stock_code      VARCHAR(20)  PRIMARY KEY,
    description     TEXT         NOT NULL,
    unit_price      NUMERIC(10, 2),
    is_active       BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

COMMENT ON TABLE products IS 'Product catalogue — one row per unique SKU';

-- ============================================================
-- FACT TABLES (the "what happened")
-- ============================================================

CREATE TABLE orders (
    order_id        BIGSERIAL    PRIMARY KEY,
    invoice_no      VARCHAR(20)  NOT NULL,
    customer_id     VARCHAR(50)  REFERENCES customers(customer_id),
    invoice_date    TIMESTAMPTZ  NOT NULL,
    country         VARCHAR(100),
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Indexes: columns we will frequently filter or join on
CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_invoice_date ON orders(invoice_date);

CREATE TABLE order_items (
    item_id         BIGSERIAL    PRIMARY KEY,
    order_id        BIGINT       REFERENCES orders(order_id),
    stock_code      VARCHAR(20)  REFERENCES products(stock_code),
    quantity        INTEGER      NOT NULL,
    unit_price      NUMERIC(10, 2) NOT NULL,
    -- Generated column: computed automatically, always consistent
    line_total      NUMERIC(12, 2) GENERATED ALWAYS AS (quantity * unit_price) STORED
);

CREATE INDEX idx_items_order_id   ON order_items(order_id);
CREATE INDEX idx_items_stock_code ON order_items(stock_code);

-- ============================================================
-- ANALYTICAL MART TABLES (pre-computed for fast queries)
-- ============================================================

CREATE TABLE customer_features (
    feature_id      BIGSERIAL    PRIMARY KEY,
    customer_id     VARCHAR(50)  REFERENCES customers(customer_id),
    snapshot_date   DATE         NOT NULL,
    -- RFM features
    recency_days    INTEGER,
    frequency       INTEGER,
    monetary_value  NUMERIC(12, 2),
    -- Derived features
    avg_order_value NUMERIC(10, 2),
    return_rate     NUMERIC(5, 4),
    days_active     INTEGER,
    -- Model outputs (filled after prediction job runs)
    churn_score     NUMERIC(5, 4),
    churn_label     BOOLEAN,
    UNIQUE(customer_id, snapshot_date)
);

CREATE TABLE demand_weekly (
    demand_id       BIGSERIAL    PRIMARY KEY,
    stock_code      VARCHAR(20)  REFERENCES products(stock_code),
    week_start      DATE         NOT NULL,
    units_sold      INTEGER,
    revenue         NUMERIC(12, 2),
    forecast_units  INTEGER,
    forecast_lower  INTEGER,
    forecast_upper  INTEGER,
    UNIQUE(stock_code, week_start)
);

-- ============================================================
-- MLOPS TABLES (tracking model runs and drift)
-- ============================================================

CREATE TABLE model_runs (
    run_id          UUID         PRIMARY KEY,
    model_name      VARCHAR(100),
    run_date        TIMESTAMPTZ,
    mlflow_run_id   VARCHAR(200),
    metrics         JSONB,
    parameters      JSONB,
    status          VARCHAR(50)
);

CREATE TABLE drift_reports (
    report_id       BIGSERIAL    PRIMARY KEY,
    model_name      VARCHAR(100),
    report_date     DATE,
    drift_detected  BOOLEAN,
    psi_score       NUMERIC(8, 6),
    details         JSONB
);