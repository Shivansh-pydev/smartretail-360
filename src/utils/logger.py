"""
Structured logging setup.

We use Python's built-in logging module configured to output
JSON-like structured logs. This is important because:
- In production, logs are collected by monitoring tools (Datadog, CloudWatch)
- Structured logs can be queried ("show me all ERROR logs from the API")
- Plain print() statements cannot be turned off or filtered

Every module in the project gets its own logger via:
    logger = get_logger(__name__)
"""
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """
    Create a logger for a module.
    
    Args:
        name: Usually pass __name__ — this gives the logger the module's name,
              which appears in every log line and helps identify where the
              log came from.
    
    Returns:
        A configured Logger instance.
    
    Example:
        logger = get_logger(__name__)
        logger.info("ETL started", extra={"rows": 541909})
        logger.error("Database connection failed", exc_info=True)
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    
    return logger