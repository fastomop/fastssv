"""Demo script showing FastSSV logging capabilities.

This script demonstrates different logging configurations and use cases.
"""

from fastssv import validate_sql_structured
from fastssv.core.logging import setup_logging

# Example SQL query
SQL_QUERY = """
SELECT
    person_id,
    condition_concept_id,
    condition_start_date
FROM condition_occurrence
WHERE condition_concept_id = 201826
"""


def demo_basic_logging():
    """Demo 1: Basic logging setup."""
    print("=" * 60)
    print("Demo 1: Basic INFO logging")
    print("=" * 60)

    logger = setup_logging(level="INFO", log_format="detailed")

    logger.info("Starting SQL validation demo")
    violations = validate_sql_structured(SQL_QUERY, dialect="postgres")
    logger.info(f"Validation complete: {len(violations)} violations found")

    print()


def demo_debug_logging():
    """Demo 2: Debug logging with detailed output."""
    print("=" * 60)
    print("Demo 2: DEBUG logging (shows all rules)")
    print("=" * 60)

    logger = setup_logging(level="DEBUG", log_format="simple")

    violations = validate_sql_structured(SQL_QUERY, dialect="postgres")
    logger.info(f"Found {len(violations)} violations")

    print()


def demo_json_logging():
    """Demo 3: JSON structured logging."""
    print("=" * 60)
    print("Demo 3: JSON structured logging")
    print("=" * 60)

    logger = setup_logging(level="INFO", log_format="json")

    logger.info("Validating SQL with JSON logging")
    violations = validate_sql_structured(SQL_QUERY, dialect="postgres")
    logger.info(
        "Validation complete",
        extra={"violation_count": len(violations)}
    )

    print()


def demo_file_logging():
    """Demo 5: Logging to file."""
    print("=" * 60)
    print("Demo 5: Logging to file")
    print("=" * 60)

    log_file = "/tmp/fastssv_demo.log"
    logger = setup_logging(
        level="INFO",
        log_file=log_file,
        log_format="detailed"
    )

    logger.info("This message goes to both console and file")
    violations = validate_sql_structured(SQL_QUERY, dialect="postgres")
    logger.info(f"Validation complete: {len(violations)} violations")

    print(f"\nLog file created at: {log_file}")
    print("Contents:")
    with open(log_file, "r") as f:
        print(f.read())

    print()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("FastSSV Logging System Demo")
    print("=" * 60 + "\n")

    # Run demos
    demo_basic_logging()
    demo_debug_logging()
    demo_json_logging()
    demo_file_logging()

    print("=" * 60)
    print("Demo complete! See docs/LOGGING.md for more details.")
    print("=" * 60)
