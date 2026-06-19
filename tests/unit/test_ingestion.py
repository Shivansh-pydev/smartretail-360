"""
Unit tests for the ETL cleaning pipeline.

Strategy: We create small DataFrames with known problems,
run them through each function, and assert the problems
are handled exactly as documented.

We do NOT test against the real database here — that would
make tests slow and require a running PostgreSQL instance.
Database integration is tested in tests/integration/.
"""
import pandas as pd
import pytest

from src.ingestion.load_data import clean, validate, transform


# ── Fixtures ─────────────────────────────────────────────────────────────────
# A pytest fixture is a reusable piece of test data.
# Instead of building the same DataFrame in 10 different tests,
# we define it once here and inject it where needed.

@pytest.fixture
def raw_dataframe():
    """
    A small DataFrame that mimics the raw Excel source.
    Contains one valid row and one of each problem type.
    """
    return pd.DataFrame({
        "Invoice":     ["536365", "C536379", "536366", "536367", "536368", "536365"],
        "StockCode":   ["85123A",  "85123A",  "85124A", "POST",   "85125A", "85123A"],
        "Description": ["CREAM HANGING HEART T-LIGHT HOLDER"] * 6,
        "Quantity":    [6,          6,         6,        1,        -1,        6],
        "InvoiceDate": pd.to_datetime(["2010-12-01"] * 6),
        "Price":       [2.55,       2.55,      0.0,      4.50,     2.55,     2.55],
        "Customer ID": ["17850",    "17850",   "17851",  "17852",  "17853",  "17850"],
        "Country":     ["UK"] * 6,
    })


# ── Validation Tests ──────────────────────────────────────────────────────────

def test_validate_passes_with_correct_schema(raw_dataframe):
    """validate() should return the DataFrame unchanged when schema is correct."""
    result = validate(raw_dataframe)
    assert len(result) == len(raw_dataframe)


def test_validate_raises_on_missing_column(raw_dataframe):
    """validate() should raise ValueError if an expected column is missing."""
    broken = raw_dataframe.drop(columns=["Customer ID"])
    with pytest.raises(ValueError, match="missing expected columns"):
        validate(broken)


# ── Cleaning Tests ────────────────────────────────────────────────────────────

def test_clean_removes_cancellations(raw_dataframe):
    """
    Rows where Invoice starts with 'C' should be removed.
    Our fixture has one cancellation: Invoice = 'C536379'
    """
    result = clean(raw_dataframe)
    assert not result["Invoice"].str.startswith("C").any(), \
        "Cancellation invoices should be removed"


def test_clean_removes_non_product_codes(raw_dataframe):
    """
    Rows with non-product StockCodes (POST, D, M, etc.) should be removed.
    Our fixture has one: StockCode = 'POST'
    """
    result = clean(raw_dataframe)
    assert "POST" not in result["StockCode"].values


def test_clean_removes_negative_quantity(raw_dataframe):
    """Rows with Quantity <= 0 should be removed."""
    result = clean(raw_dataframe)
    assert (result["Quantity"] > 0).all()


def test_clean_removes_zero_price(raw_dataframe):
    """Rows with Price <= 0 should be removed."""
    result = clean(raw_dataframe)
    assert (result["Price"] > 0).all()


def test_clean_removes_duplicates(raw_dataframe):
    """
    Duplicate rows (same Invoice, StockCode, Qty, Price, Date) should be removed.
    Our fixture has two identical rows for Invoice 536365, StockCode 85123A.
    """
    result = clean(raw_dataframe)
    dedup_check = result.duplicated(
        subset=["Invoice", "StockCode", "Quantity", "Price", "InvoiceDate"]
    )
    assert not dedup_check.any(), "No duplicate rows should remain"


def test_clean_retains_valid_rows(raw_dataframe):
    """After cleaning, at least one valid row should remain."""
    result = clean(raw_dataframe)
    assert len(result) > 0, "All rows were removed — cleaning is too aggressive"


# ── Transform Tests ───────────────────────────────────────────────────────────

@pytest.fixture
def clean_dataframe(raw_dataframe):
    """Cleaned version of raw_dataframe — used as input to transform()."""
    return clean(raw_dataframe)


def test_transform_returns_all_tables(clean_dataframe):
    """transform() should return a dict with all four expected table keys."""
    result = transform(clean_dataframe)
    assert set(result.keys()) == {"customers", "products", "orders", "order_items"}


def test_transform_customers_are_unique(clean_dataframe):
    """Each customer should appear exactly once in the customers table."""
    result = transform(clean_dataframe)
    customers = result["customers"]
    assert customers["customer_id"].nunique() == len(customers), \
        "customer_id must be unique — duplicates found"


def test_transform_products_are_unique(clean_dataframe):
    """Each product should appear exactly once in the products table."""
    result = transform(clean_dataframe)
    products = result["products"]
    assert products["stock_code"].nunique() == len(products)


def test_transform_order_items_link_to_orders(clean_dataframe):
    """Every invoice_no in order_items must also appear in orders."""
    result = transform(clean_dataframe)
    order_invoices = set(result["orders"]["invoice_no"])
    item_invoices = set(result["order_items"]["invoice_no"])
    orphaned = item_invoices - order_invoices
    assert len(orphaned) == 0, \
        f"Order items reference invoices not in orders table: {orphaned}"