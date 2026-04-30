"""Docker-based smoke test for the deploy artifacts.

Builds the image via `deploy/docker-compose.yml`, brings the container up,
waits for the `/v1/health` endpoint to respond 200, hits a few representative
API surfaces, and tears the stack down. Validates that the deploy bundle
(Dockerfile + compose) actually produces a working app — not just that the
files parse.

Skipped automatically when:
  - `docker` is not on PATH, OR
  - The Docker daemon isn't running, OR
  - The env var `FASTSSV_SKIP_DOCKER_TESTS=1` is set.

Run explicitly:
  pytest tests/api/test_docker_smoke.py -v

Run only this test against an already-running stack (no build/teardown):
  FASTSSV_SMOKE_BASE_URL=http://localhost:8000 pytest tests/api/test_docker_smoke.py -v
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker-compose.yml"
PROJECT_NAME = "fastssv-smoke"
BUILD_TIMEOUT_S = 600
HEALTH_TIMEOUT_S = 90


def _docker_available() -> bool:
    if os.environ.get("FASTSSV_SKIP_DOCKER_TESTS") == "1":
        return False
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/v1/health", timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError) as e:
            last_err = e
        time.sleep(1)
    raise AssertionError(
        f"/v1/health did not return 200 within {timeout_s}s. Last error: {last_err!r}"
    )


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="docker not available (set FASTSSV_SKIP_DOCKER_TESTS=1 to skip explicitly)",
)


@pytest.fixture(scope="module")
def base_url() -> str:
    """Spin up the compose stack, yield its base URL, tear it down on exit.

    If `FASTSSV_SMOKE_BASE_URL` is set we skip stack management and just point
    the tests at the existing URL — useful for running the same tests against
    a staging deployment.
    """
    override = os.environ.get("FASTSSV_SMOKE_BASE_URL")
    if override:
        _wait_for_health(override.rstrip("/"), HEALTH_TIMEOUT_S)
        yield override.rstrip("/")
        return

    port = _free_port()
    env = {**os.environ, "FASTSSV_PORT": str(port)}

    base = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", PROJECT_NAME]

    try:
        subprocess.run(
            [*base, "up", "-d", "--build", "--wait"],
            check=True,
            timeout=BUILD_TIMEOUT_S,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        logs = subprocess.run(
            [*base, "logs", "--no-color"],
            capture_output=True,
            text=True,
            env=env,
        )
        pytest.fail(
            f"`docker compose up` failed (rc={e.returncode}). "
            f"Logs:\n{logs.stdout}\n{logs.stderr}"
        )

    url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(url, HEALTH_TIMEOUT_S)
        yield url
    finally:
        subprocess.run(
            [*base, "down", "-v", "--remove-orphans"],
            timeout=60,
            env=env,
        )


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read()


def _post_json(url: str, body: dict) -> tuple[int, bytes]:
    import json

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read()


def test_health_returns_200(base_url: str) -> None:
    status, _ = _get(f"{base_url}/v1/health")
    assert status == 200


def test_rules_listing_returns_registry(base_url: str) -> None:
    import json

    status, body = _get(f"{base_url}/v1/rules")
    assert status == 200
    payload = json.loads(body)
    assert "rules" in payload
    assert isinstance(payload["rules"], list)
    assert len(payload["rules"]) > 50


def test_validate_clean_sql_returns_valid(base_url: str) -> None:
    import json

    status, body = _post_json(
        f"{base_url}/v1/validate",
        {"sql": "SELECT person_id FROM person WHERE year_of_birth > 1980", "dialect": "postgres"},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["is_valid"] is True


def test_validate_prose_input_surfaces_not_sql_rule(base_url: str) -> None:
    """Verifies the parse.not_sql_input fix is wired through the deployed stack."""
    import json

    status, body = _post_json(
        f"{base_url}/v1/validate",
        {
            "sql": "It appears that the schema is unavailable, making the query infeasible.",
            "dialect": "postgres",
        },
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["is_valid"] is False
    rule_ids = {e["rule_id"] for e in payload.get("errors", [])}
    assert "parse.not_sql_input" in rule_ids
