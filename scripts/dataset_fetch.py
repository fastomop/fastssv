"""
Script to fetch run_omop_query data from Langfuse.
Extracts sql_query and final_query labels from the JSON data.
"""

import os
import json
import time
from pathlib import Path
from dotenv import load_dotenv
from langfuse import Langfuse
from langfuse.api.core.api_error import ApiError
import httpx

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


def retry_with_backoff(func, max_retries=5, initial_delay=2):
    """
    Retry a function with exponential backoff on rate limits and timeouts.

    Args:
        func: Function to retry
        max_retries: Maximum number of retries
        initial_delay: Initial delay in seconds

    Returns:
        Result of the function
    """
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return func()
        except ApiError as e:
            if e.status_code == 429 and attempt < max_retries - 1:
                print(f"  Rate limited. Waiting {delay}s before retry {attempt + 1}/{max_retries}...", flush=True)
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            if attempt < max_retries - 1:
                print(f"  Timeout. Waiting {delay}s before retry {attempt + 1}/{max_retries}...", flush=True)
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise Exception(f"Max retries ({max_retries}) exceeded")


def get_langfuse_client():
    """
    Initialize and return a Langfuse client.

    Requires environment variables:
    - LANGFUSE_SECRET_KEY
    - LANGFUSE_PUBLIC_KEY
    - LANGFUSE_HOST (optional, defaults to cloud)
    """
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    host = os.getenv("LANGFUSE_HOST")

    if not secret_key or not public_key:
        raise ValueError(
            "LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY must be set in environment variables"
        )

    if host:
        client = Langfuse(
            secret_key=secret_key,
            public_key=public_key,
            host=host
        )
    else:
        client = Langfuse(
            secret_key=secret_key,
            public_key=public_key
        )

    return client


def fetch_run_omop_query_data(client, max_total=None):
    """
    Fetch run_omop_query observations directly from Langfuse using the
    observations API with name filter. Much faster than fetching each trace.

    Args:
        client: Langfuse client instance
        max_total: Maximum number of observations to fetch (None for all)

    Returns:
        List of dictionaries containing extracted data
    """
    results = []
    page = 1
    per_page = 100  # Max allowed by Langfuse API

    print(f"Fetching run_omop_query observations directly (max {per_page} per page)...", flush=True)

    while True:
        try:
            print(f"  Page {page}...", flush=True)
            resp = retry_with_backoff(
                lambda p=page: client.api.observations.get_many(
                    name="run_omop_query", limit=per_page, page=p
                )
            )

            obs_count = len(resp.data)
            print(f"  Page {page}: got {obs_count} observations", flush=True)

            if obs_count == 0:
                break

            for obs in resp.data:
                results.append({
                    "trace_id": obs.trace_id,
                    "observation_id": obs.id,
                    "observation_type": obs.type,
                    "input": obs.input,
                    "output": obs.output,
                    "metadata": obs.metadata,
                })

                if max_total and len(results) >= max_total:
                    print(f"  Reached max_total={max_total}", flush=True)
                    return results

            if obs_count < per_page:
                break

            page += 1
            time.sleep(0.5)  # gentle delay between pages

        except Exception as e:
            print(f"  Page {page} error: {e}. Retrying after delay.", flush=True)
            page += 1
            time.sleep(3)
            continue

    print(f"Total run_omop_query observations fetched: {len(results)}", flush=True)
    return results


def extract_queries(data_dict):
    """
    Extract sql_query from the observation data.

    Args:
        data_dict: Dictionary containing observation data

    Returns:
        Dictionary with sql_query field
    """
    result = {
        "trace_id": data_dict.get("trace_id"),
        "observation_id": data_dict.get("observation_id"),
        "user_query": None,
        "sql_query": None
    }

    # Extract from the output which contains step_executor_runs
    output = data_dict.get("output")
    if not isinstance(output, dict):
        return result

    # Get user query from input
    if "input" in output:
        result["user_query"] = output.get("input")

    # Look for the database agent step executor run
    step_executor_runs = output.get("step_executor_runs") or []

    for run in step_executor_runs:
        if run.get("agent_name") == "OMOP Database Agent":
            # Search through tools for SQL queries
            tools = run.get("tools", [])
            for tool in tools:
                if tool.get("tool_name") == "Select_Query":
                    # The SQL query is in tool_args
                    tool_args = tool.get("tool_args", {})
                    if "query" in tool_args and result["sql_query"] is None:
                        result["sql_query"] = tool_args["query"]
                        break

            # If not found in tools, check messages for SQL
            if result["sql_query"] is None:
                messages = run.get("messages", [])
                for msg in messages:
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        for tool_call in msg["tool_calls"]:
                            if tool_call.get("function", {}).get("name") == "Select_Query":
                                args_str = tool_call["function"].get("arguments", "{}")
                                try:
                                    args = json.loads(args_str)
                                    if "query" in args:
                                        result["sql_query"] = args["query"]
                                        break
                                except json.JSONDecodeError:
                                    pass
                        if result["sql_query"]:
                            break

    return result


def process_raw_data(raw_data):
    """
    Extract, deduplicate, and add IDs to raw observation data.

    Args:
        raw_data: List of raw observation dictionaries

    Returns:
        List of dictionaries with extracted queries (deduplicated with IDs)
    """
    processed_data = [extract_queries(data) for data in raw_data]

    # Remove duplicates based on sql_query
    seen_queries = set()
    unique_data = []

    for item in processed_data:
        sql_query = item.get("sql_query")
        if sql_query and sql_query not in seen_queries:
            seen_queries.add(sql_query)
            unique_data.append(item)

    # Add sequential IDs and reorder fields
    reordered_data = []
    for i, item in enumerate(unique_data, 1):
        reordered_data.append({
            "id": i,
            "user_query": item["user_query"],
            "sql_query": item["sql_query"],
            "trace_id": item["trace_id"],
            "observation_id": item["observation_id"]
        })

    return reordered_data


if __name__ == "__main__":
    raw_cache_file = Path(__file__).parent / "raw_observations.json"

    # Use cached raw data if available, otherwise fetch
    if Path(raw_cache_file).exists():
        print(f"Loading cached raw data from {raw_cache_file}...")
        with open(raw_cache_file, "r") as f:
            raw_data = json.load(f)
        print(f"Loaded {len(raw_data)} cached observations\n")
    else:
        print("Initializing Langfuse client...")
        client = get_langfuse_client()
        print("Langfuse client initialized successfully\n")

        print("Fetching run_omop_query data from Langfuse...")
        raw_data = fetch_run_omop_query_data(client, max_total=None)

        # Save raw data before extraction so we don't re-fetch on failure
        with open(raw_cache_file, "w") as f:
            json.dump(raw_data, f, indent=2)
        print(f"Raw data saved to {raw_cache_file} ({len(raw_data)} observations)\n")

    print("Extracting and deduplicating queries...")
    results = process_raw_data(raw_data)

    print(f"\nFound {len(results)} unique queries (after deduplication)\n")

    # Display sample results
    print("Sample results (first 3):")
    for result in results[:3]:
        print(f"\n--- Result {result['id']} ---")
        print(f"User Query: {result.get('user_query', 'N/A')}")
        print(f"SQL Query: {'Found' if result['sql_query'] else 'Not found'}")
        print(f"Trace ID: {result['trace_id']}")

    output_file = Path(__file__).parent / "run_omop_query_dataset.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_file}")
