"""
ETL Pipeline: Raw Excel → PostgreSQL

This module handles the complete ingestion of the UCI Online Retail II dataset.

Pipeline stages:
    1. Extract  — Read Excel file from disk
    2. Validate — Check schema and data types match expectations
    3. Clean    — Remove/fix records that would corrupt downstream analysis
    4. Transform — Reshape to match our PostgreSQL schema
    5. Load     — Upsert into PostgreSQL tables

Design decisions:
    - We use UPSERT (INSERT ... ON CONFLICT DO NOTHING) instead of plain INSERT.
      This means running the script twice will not create duplicate records.
      This is critical for idempotency — the ability to run a pipeline multiple
      times safely and get the same result.

    - We load in this order: customers → products → orders → order_items
      Because order_items has FOREIGN KEY constraints referencing the other three,
      the referenced tables must exist first.

    - We commit in batches of 1000 rows, not all at once. Loading 1M rows in
      a single transaction means if it fails at row 999,999, you lose everything
      and restart. Batches limit the blast radius of failures.
"""

import hashlib
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text

from src.utils.db import get_db_session
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

RAW_DATA_PATH = Path("data/raw/online_retail_II.xlsx")

# Stock codes that are NOT real products — operational/admin codes
NON_PRODUCT_CODES = {
    "POST",        # Postage
    "D",           # Discount
    "M",           # Manual
    "BANK CHARGES",
    "CRUK",        # Charity donation
    "C2",          # Carriage
    "DOT",         # Dotcom postage
}

BATCH_SIZE = 1_000   # How many rows to insert per database transaction


# ── Stage 1: Extract ─────────────────────────────────────────────────────────

def extract(filepath: Path = RAW_DATA_PATH) -> pd.DataFrame:
    """
    Read the raw Excel file and combine both annual sheets.

    Args:
        filepath: Path to the .xlsx file

    Returns:
        Combined DataFrame with all rows from both sheets

    Raises:
        FileNotFoundError: If the Excel file does not exist at the given path
    """
    if not filepath.exists():
        raise FileNotFoundError(
            f"Dataset not found at {filepath}. "
            f"Please download online_retail_II.xlsx from UCI ML Repository "
            f"and place it in data/raw/"
        )

    logger.info(f"Reading Excel file from {filepath}")

    # dtype overrides prevent pandas from guessing wrong types.
    # Customer ID could be read as float (12345.0) if not forced to string.
    # StockCode could be read as integer, dropping leading zeros.
    sheets = pd.read_excel(
        filepath,
        sheet_name=None,          # Load ALL sheets → returns dict {name: DataFrame}
        dtype={
            "Customer ID": str,
            "StockCode": str,
            "Invoice": str,
        },
    )

    df = pd.concat(sheets.values(), ignore_index=True)
    logger.info(f"Extracted {len(df):,} raw rows from {len(sheets)} sheets")
    return df


# ── Stage 2: Validate ────────────────────────────────────────────────────────

EXPECTED_COLUMNS = {
    "Invoice", "StockCode", "Description",
    "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"
}

def validate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Check the DataFrame has the structure we expect.

    We validate BEFORE cleaning — we want to catch schema changes in the
    source data immediately, not silently produce wrong results.

    Args:
        df: Raw DataFrame from extract()

    Returns:
        The same DataFrame (unchanged) if validation passes

    Raises:
        ValueError: If expected columns are missing
    """
    missing_cols = EXPECTED_COLUMNS - set(df.columns)
    if missing_cols:
        raise ValueError(
            f"Source data is missing expected columns: {missing_cols}. "
            f"The dataset schema may have changed."
        )

    logger.info("Schema validation passed")
    return df


# ── Stage 3: Clean ───────────────────────────────────────────────────────────

def clean(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove and fix records that would corrupt downstream analysis.

    Every removal is logged with a count so we can audit what was dropped.
    If we ever drop more than expected, the logs will show it.

    Args:
        df: Validated raw DataFrame

    Returns:
        Cleaned DataFrame ready for transformation
    """
    initial_count = len(df)
    logger.info(f"Starting cleaning with {initial_count:,} rows")

    # ── 1. Remove cancellations ──────────────────────────────────────────────
    # Invoices starting with 'C' are returns/cancellations.
    # We model forward-looking churn based on purchase history.
    # Including cancellations would inflate purchase counts and confuse the model.
    cancellations_mask = df["Invoice"].str.startswith("C", na=False)
    cancellations_count = cancellations_mask.sum()
    df = df[~cancellations_mask]
    logger.info(f"Removed {cancellations_count:,} cancellation rows (Invoice starts with C)")

    # ── 2. Remove rows with missing Customer ID ──────────────────────────────
    # ~25% of rows have no Customer ID (guest checkouts).
    # We cannot build per-customer features for anonymous users.
    missing_customer_mask = df["Customer ID"].isnull()
    missing_customer_count = missing_customer_mask.sum()
    df = df[~missing_customer_mask]
    logger.info(f"Removed {missing_customer_count:,} rows with missing Customer ID")

    # ── 3. Remove rows with non-positive quantity ────────────────────────────
    # Negative quantities appear for returns and manual adjustments.
    # Zero quantity rows are data entry errors.
    bad_qty_mask = df["Quantity"] <= 0
    bad_qty_count = bad_qty_mask.sum()
    df = df[~bad_qty_mask]
    logger.info(f"Removed {bad_qty_count:,} rows with quantity <= 0")

    # ── 4. Remove rows with non-positive price ───────────────────────────────
    # Zero or negative prices are samples, internal transfers, or errors.
    bad_price_mask = df["Price"] <= 0
    bad_price_count = bad_price_mask.sum()
    df = df[~bad_price_mask]
    logger.info(f"Removed {bad_price_count:,} rows with price <= 0")

    # ── 5. Remove non-product stock codes ───────────────────────────────────
    # Administrative codes pollute the product catalogue and demand forecasts.
    non_product_mask = df["StockCode"].isin(NON_PRODUCT_CODES)
    non_product_count = non_product_mask.sum()
    df = df[~non_product_mask]
    logger.info(f"Removed {non_product_count:,} rows with non-product stock codes")

    # ── 6. Remove duplicate rows ─────────────────────────────────────────────
    # True duplicates: same invoice, product, quantity, price, and date.
    # These are data entry errors in the source system.
    before_dedup = len(df)
    df = df.drop_duplicates(
        subset=["Invoice", "StockCode", "Quantity", "Price", "InvoiceDate"]
    )
    dedup_count = before_dedup - len(df)
    logger.info(f"Removed {dedup_count:,} duplicate rows")

    # ── 7. Standardise text columns ──────────────────────────────────────────
    # Strip whitespace from string columns to prevent "UK " ≠ "UK" mismatches.
    df["Customer ID"] = df["Customer ID"].str.strip()
    df["StockCode"] = df["StockCode"].str.strip()
    df["Country"] = df["Country"].str.strip()
    df["Description"] = df["Description"].str.strip().str.title()

    final_count = len(df)
    total_removed = initial_count - final_count
    logger.info(
        f"Cleaning complete: {final_count:,} rows remain "
        f"({total_removed:,} removed, "
        f"{total_removed/initial_count*100:.1f}% of raw data)"
    )

    return df


# ── Stage 4: Transform ───────────────────────────────────────────────────────

def transform(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Reshape the flat DataFrame into our relational schema.

    The source data is one flat table with one row per order line.
    Our schema separates this into: customers, products, orders, order_items.

    Args:
        df: Cleaned DataFrame

    Returns:
        Dictionary mapping table name → DataFrame ready to insert
    """
    logger.info("Transforming data to relational schema")

    # ── Customers ────────────────────────────────────────────────────────────
    # One row per unique Customer ID
    # first_purchase = earliest InvoiceDate for that customer
    customers = (
        df.groupby("Customer ID")
        .agg(
            country=("Country", "first"),
            first_purchase=("InvoiceDate", "min"),
        )
        .reset_index()
        .rename(columns={"Customer ID": "customer_id"})
    )
    customers["first_purchase"] = customers["first_purchase"].dt.date
    logger.info(f"Prepared {len(customers):,} unique customers")

    # ── Products ─────────────────────────────────────────────────────────────
    # One row per unique StockCode
    # Take the most recent description and median price for each SKU
    # (descriptions and prices vary slightly across invoices for the same SKU)
    products = (
        df.sort_values("InvoiceDate")
        .groupby("StockCode")
        .agg(
            description=("Description", "last"),   # Most recent description
            unit_price=("Price", "median"),         # Median price (robust to outliers)
        )
        .reset_index()
        .rename(columns={"StockCode": "stock_code"})
    )
    logger.info(f"Prepared {len(products):,} unique products")

    # ── Orders ───────────────────────────────────────────────────────────────
    # One row per unique Invoice (a single shopping session)
    orders = (
        df.groupby("Invoice")
        .agg(
            customer_id=("Customer ID", "first"),
            invoice_date=("InvoiceDate", "first"),
            country=("Country", "first"),
        )
        .reset_index()
        .rename(columns={"Invoice": "invoice_no"})
    )
    logger.info(f"Prepared {len(orders):,} unique orders")

    # ── Order Items ──────────────────────────────────────────────────────────
    # One row per line item (Invoice + StockCode combination)
    order_items = df[["Invoice", "StockCode", "Quantity", "Price"]].copy()
    order_items = order_items.rename(columns={
        "Invoice": "invoice_no",
        "StockCode": "stock_code",
        "Quantity": "quantity",
        "Price": "unit_price",
    })
    logger.info(f"Prepared {len(order_items):,} order line items")

    return {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": order_items,
    }


# ── Stage 5: Load ────────────────────────────────────────────────────────────

def load_customers(df: pd.DataFrame) -> int:
    """
    Upsert customers into PostgreSQL.

    UPSERT means: INSERT the row; if a row with this primary key already exists,
    do nothing (don't error, don't overwrite).

    This makes the pipeline IDEMPOTENT: running it 10 times produces the same
    result as running it once.

    Returns:
        Number of rows processed
    """
    sql = text("""
        INSERT INTO customers (customer_id, country, first_purchase)
        VALUES (:customer_id, :country, :first_purchase)
        ON CONFLICT (customer_id) DO NOTHING
    """)

    rows_loaded = 0
    with get_db_session() as session:
        records = df.to_dict(orient="records")
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i : i + BATCH_SIZE]
            session.execute(sql, batch)
            rows_loaded += len(batch)

    logger.info(f"Loaded {rows_loaded:,} customer records")
    return rows_loaded


def load_products(df: pd.DataFrame) -> int:
    """Upsert products into PostgreSQL."""
    sql = text("""
        INSERT INTO products (stock_code, description, unit_price)
        VALUES (:stock_code, :description, :unit_price)
        ON CONFLICT (stock_code) DO NOTHING
    """)

    rows_loaded = 0
    with get_db_session() as session:
        records = df.to_dict(orient="records")
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i : i + BATCH_SIZE]
            session.execute(sql, batch)
            rows_loaded += len(batch)

    logger.info(f"Loaded {rows_loaded:,} product records")
    return rows_loaded

def load_orders(df: pd.DataFrame) -> dict[str, int]:
    """
    Insert orders and return a mapping of invoice_no → order_id.
    """

    insert_sql = text("""
        INSERT INTO orders (invoice_no, customer_id, invoice_date, country)
        VALUES (:invoice_no, :customer_id, :invoice_date, :country)
        ON CONFLICT (invoice_no) DO NOTHING
    """)

    with get_db_session() as session:
        records = df.to_dict(orient="records")

        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            session.execute(insert_sql, batch)

        result = session.execute(text("""
            SELECT invoice_no, order_id
            FROM orders
        """))

        invoice_to_order_id = {
            row.invoice_no: row.order_id
            for row in result
        }

    logger.info(f"Loaded {len(invoice_to_order_id):,} orders")
    return invoice_to_order_id


def load_order_items(df: pd.DataFrame, invoice_to_order_id: dict[str, int]) -> int:
    """
    Insert order line items.

    Maps invoice_no → order_id using the dictionary returned by load_orders().
    """
    # Replace invoice_no with the actual order_id foreign key
    df = df.copy()
    df["order_id"] = df["invoice_no"].map(invoice_to_order_id)

    # Drop rows where we couldn't find the order_id
    # (should not happen, but defensive programming)
    missing_orders = df["order_id"].isnull().sum()
    if missing_orders > 0:
        logger.warning(f"Dropping {missing_orders} items with no matching order_id")
        df = df.dropna(subset=["order_id"])

    df["order_id"] = df["order_id"].astype(int)
    df = df.drop(columns=["invoice_no"])

    sql = text("""
        INSERT INTO order_items (order_id, stock_code, quantity, unit_price)
        VALUES (:order_id, :stock_code, :quantity, :unit_price)
    """)

    rows_loaded = 0
    with get_db_session() as session:
        records = df.to_dict(orient="records")
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i : i + BATCH_SIZE]
            session.execute(sql, batch)
            rows_loaded += len(batch)

    logger.info(f"Loaded {rows_loaded:,} order item records")
    return rows_loaded


# ── Main Orchestrator ────────────────────────────────────────────────────────

def run_ingestion(filepath: Path = RAW_DATA_PATH) -> None:
    """
    Run the complete ETL pipeline end to end.

    This is the function you call from the command line or a Makefile.
    It orchestrates all five stages in order and logs summary statistics.

    Args:
        filepath: Path to the raw Excel file
    """
    logger.info("=" * 60)
    logger.info("SmartRetail 360 — ETL Pipeline Starting")
    logger.info("=" * 60)

    # Stage 1: Extract
    raw_df = extract(filepath)

    # Stage 2: Validate
    validated_df = validate(raw_df)

    # Stage 3: Clean
    clean_df = clean(validated_df)

    # Stage 4: Transform
    tables = transform(clean_df)

    # Stage 5: Load (order matters — respect foreign key dependencies)
    logger.info("Loading data into PostgreSQL...")

    load_customers(tables["customers"])
    load_products(tables["products"])
    invoice_map = load_orders(tables["orders"])
    load_order_items(tables["order_items"], invoice_map)

    logger.info("=" * 60)
    logger.info("ETL Pipeline Complete")
    logger.info("=" * 60)


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_ingestion()