"""
Converts run_omop_query_dataset.json into a single .sql file.
Each query is preceded by a comment with its ID.
"""

import json
from pathlib import Path

INPUT_FILE = Path(__file__).parent / "run_omop_query_dataset.json"
OUTPUT_FILE = Path(__file__).parent / "run_omop_query_dataset.sql"


def main():
    with open(INPUT_FILE, "r") as f:
        data = json.load(f)

    with open(OUTPUT_FILE, "w") as f:
        for i, entry in enumerate(data):
            if i > 0:
                f.write("\n\n")
            f.write(f"-- ID: {entry['id']}\n")
            f.write(entry["sql_query"].rstrip())
            if not entry["sql_query"].rstrip().endswith(";"):
                f.write(";")

    print(f"Exported {len(data)} queries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
