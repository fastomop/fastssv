"""Centralized logging configuration for FastSSV.

This module provides structured logging with configurable levels, formats,
and output destinations. Supports both console and file logging with
production-ready defaults.

Environment Variables:
    FASTSSV_LOG_LEVEL: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    FASTSSV_LOG_FILE: Path to log file (optional, defaults to console only)
    FASTSSV_LOG_FORMAT: Format preset ('simple', 'detailed', 'json')
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Default configuration
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "detailed"

# Log format presets
FORMATS = {
    "simple": "%(levelname)s: %(message)s",
    "detailed": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "json": None,  # Special handling for JSON formatting
}


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms
        if hasattr(record, "rule_id"):
            log_data["rule_id"] = record.rule_id
        if hasattr(record, "violation_count"):
            log_data["violation_count"] = record.violation_count

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    log_format: Optional[str] = None,
    logger_name: str = "fastssv",
) -> logging.Logger:
    """Configure and return a logger instance.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file (optional)
        log_format: Format preset ('simple', 'detailed', 'json')
        logger_name: Name of the logger

    Returns:
        Configured logger instance

    Example:
        logger = setup_logging(level="DEBUG", log_format="json")
        logger.info("Validation started")
    """
    # Get configuration from environment or defaults
    level = level or os.getenv("FASTSSV_LOG_LEVEL", DEFAULT_LOG_LEVEL)
    log_file = log_file or os.getenv("FASTSSV_LOG_FILE")
    log_format = log_format or os.getenv("FASTSSV_LOG_FORMAT", DEFAULT_LOG_FORMAT)

    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Get or create logger
    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create formatter
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        format_string = FORMATS.get(log_format, FORMATS["detailed"])
        formatter = logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "fastssv") -> logging.Logger:
    """Get a logger instance.

    If logging is not yet configured, returns a basic logger.
    Use setup_logging() first for full configuration.

    Args:
        name: Logger name (typically module name)

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)

    # If no handlers configured, add a basic null handler
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    return logger


# Convenience functions for common log messages
def log_validation_start(logger: logging.Logger, sql_length: int, dialect: str) -> None:
    """Log validation start message.

    Args:
        logger: Logger instance
        sql_length: Length of SQL query in characters
        dialect: SQL dialect being used
    """
    logger.info(f"Starting validation: {sql_length} characters, dialect={dialect}")


def log_validation_complete(
    logger: logging.Logger,
    total_rules: int,
    error_count: int,
    warning_count: int,
    duration_ms: Optional[float] = None,
) -> None:
    """Log validation completion message.

    Args:
        logger: Logger instance
        total_rules: Total number of rules executed
        error_count: Number of errors found
        warning_count: Number of warnings found
        duration_ms: Optional duration in milliseconds
    """
    extra: Dict[str, Any] = {
        "violation_count": error_count + warning_count,
    }
    if duration_ms is not None:
        extra["duration_ms"] = duration_ms

    logger.info(
        f"Validation complete: {total_rules} rules, {error_count} errors, {warning_count} warnings",
        extra=extra,
    )


def log_rule_execution(
    logger: logging.Logger,
    rule_id: str,
    violation_count: int,
    duration_ms: Optional[float] = None,
) -> None:
    """Log rule execution details.

    Args:
        logger: Logger instance
        rule_id: Rule identifier
        violation_count: Number of violations found
        duration_ms: Optional duration in milliseconds
    """
    extra: Dict[str, Any] = {
        "rule_id": rule_id,
        "violation_count": violation_count,
    }
    if duration_ms is not None:
        extra["duration_ms"] = duration_ms

    level = logging.DEBUG if violation_count == 0 else logging.INFO
    logger.log(
        level,
        f"Rule {rule_id}: {violation_count} violation(s)",
        extra=extra,
    )


__all__ = [
    "setup_logging",
    "get_logger",
    "JSONFormatter",
    "log_validation_start",
    "log_validation_complete",
    "log_rule_execution",
]
