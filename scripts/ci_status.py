"""Wait for the GitHub Actions run of a commit and report every job (stdlib only).

    python scripts/ci_status.py            # HEAD
    python scripts/ci_status.py <sha>      # a specific commit

A green run is part of "done" (CLAUDE.md), so this must not give up on a flaky response: every
API call is retried with backoff on network errors, HTTP 5xx / 403 rate limits, empty bodies and
non-JSON bodies. The repository is public, so no token is needed; if GITHUB_TOKEN is set it is
sent (for a higher rate limit) and never printed.

Exit codes: 0 = run completed and every job succeeded (skipped jobs allowed); 1 = the run or a
job failed or was cancelled; 2 = gave up (no run found, or still running at --timeout).
"""

# ruff: noqa: T201 - a command-line tool: printing is its output

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from typing import Any

REPO = "rishitaggarwal1/SkillifyMeLMS"
API = f"https://api.github.com/repos/{REPO}"
OK_CONCLUSIONS = {"success", "skipped", "neutral"}
RATE_LIMITED = {HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS}  # GitHub uses both


class TransientError(Exception):
    """A response worth retrying (network error, 5xx, rate limit, empty or non-JSON body)."""

    def __init__(self, reason: str, *, wait: float | None = None) -> None:
        super().__init__(reason)
        self.wait = wait  # seconds the server asked us to wait, if it said


def _rate_limit_wait(exc: urllib.error.HTTPError) -> float | None:
    """Seconds until GitHub lifts a rate limit (Retry-After or X-RateLimit-Reset), capped."""
    headers = exc.headers
    if retry_after := headers.get("Retry-After"):
        return min(float(retry_after), 900.0)
    if headers.get("X-RateLimit-Remaining") == "0" and (reset := headers.get("X-RateLimit-Reset")):
        return min(max(float(reset) - time.time(), 1.0) + 1.0, 900.0)
    return None


def _fetch(url: str) -> Any:
    request = urllib.request.Request(  # noqa: S310 - fixed https://api.github.com URLs
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ci-status"}
    )
    if token := os.environ.get("GITHUB_TOKEN"):
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code >= HTTPStatus.INTERNAL_SERVER_ERROR or exc.code in RATE_LIMITED:
            raise TransientError(f"HTTP {exc.code}", wait=_rate_limit_wait(exc)) from exc
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TransientError(str(exc)) from exc
    if not body.strip():
        raise TransientError("empty response")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise TransientError(f"non-JSON response: {body[:80]!r}") from exc


def get_json(url: str, *, attempts: int = 8) -> Any:
    """GET with retries and exponential backoff (2, 4, 8 ... capped at 60 s)."""
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return _fetch(url)
        except TransientError as exc:
            if attempt == attempts:
                raise
            pause = exc.wait if exc.wait is not None else delay
            print(f"  {exc}; retrying in {pause:.0f}s ({attempt}/{attempts})", file=sys.stderr)
            time.sleep(pause)
            delay = min(delay * 2, 60.0)
    raise AssertionError("unreachable")


def full_sha(rev: str) -> str:
    """The full 40-character sha (the API matches head_sha exactly; short shas find nothing)."""
    return subprocess.run(  # noqa: S603 - git with a revision the user typed
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],  # noqa: S607
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sha", nargs="?", default="HEAD", help="commit or ref (default: HEAD)")
    parser.add_argument("--timeout", type=int, default=45 * 60, help="seconds to wait")
    # 60 s keeps a 45-minute wait within GitHub's 60 unauthenticated requests per hour.
    parser.add_argument("--interval", type=int, default=60, help="seconds between polls")
    args = parser.parse_args()
    sha = full_sha(args.sha)
    deadline = time.monotonic() + args.timeout

    run: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        runs = get_json(f"{API}/actions/runs?head_sha={sha}").get("workflow_runs") or []
        run = runs[0] if runs else None
        if run is not None and run.get("status") == "completed":
            break
        state = run.get("status") if run else "not started yet"
        print(f"{sha[:7]}: {state}", file=sys.stderr)
        time.sleep(args.interval)
    else:
        print(f"Gave up after {args.timeout}s: {run['html_url'] if run else 'no run found'}")
        return 2

    assert run is not None  # noqa: S101 - the loop only breaks with a completed run
    jobs = get_json(f"{API}/actions/runs/{run['id']}/jobs?per_page=100").get("jobs") or []
    print(f"Run {run['id']}: {run['conclusion']}  {run['html_url']}")
    for job in jobs:
        print(f"  {job['conclusion'] or job['status']:<10} {job['name']}")
    failed = [j for j in jobs if j.get("conclusion") not in OK_CONCLUSIONS]
    return 0 if run.get("conclusion") == "success" and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
