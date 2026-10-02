"""scripts/ci_status.py (the "is CI green?" check): its HTTP calls retry empty, non-JSON,
rate-limited and failing responses instead of giving up, and honour GitHub's reset time."""

import importlib.util
import io
import json
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci_status.py"


@pytest.fixture
def ci() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_status", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_status"] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _http_error(code: int, headers: dict[str, str]) -> urllib.error.HTTPError:
    message = Message()
    for name, value in headers.items():
        message[name] = value
    return urllib.error.HTTPError("https://api.github.com/x", code, "error", message, None)


def _serve(monkeypatch: pytest.MonkeyPatch, ci: ModuleType, replies: list[Any]) -> list[float]:
    """urlopen answers with `replies` in turn (bytes = a body, exceptions = raised); returns the
    sleeps the script asked for."""
    sleeps: list[float] = []

    def urlopen(*_: object, **__: object) -> FakeResponse:
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return FakeResponse(reply)

    monkeypatch.setattr(ci.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ci.time, "sleep", sleeps.append)
    return sleeps


def test_retries_empty_non_json_server_errors_and_network_failures(
    monkeypatch: pytest.MonkeyPatch, ci: ModuleType
) -> None:
    sleeps = _serve(
        monkeypatch,
        ci,
        [
            b"",
            b"<html>Bad gateway</html>",
            _http_error(502, {}),
            urllib.error.URLError("connection reset"),
            json.dumps({"workflow_runs": []}).encode(),
        ],
    )
    assert ci.get_json("https://api.github.com/x") == {"workflow_runs": []}
    assert sleeps == [2.0, 4.0, 8.0, 16.0]  # exponential backoff


def test_rate_limit_waits_for_the_reset(monkeypatch: pytest.MonkeyPatch, ci: ModuleType) -> None:
    sleeps = _serve(
        monkeypatch,
        ci,
        [_http_error(403, {"Retry-After": "42"}), b'{"ok": true}'],
    )
    assert ci.get_json("https://api.github.com/x") == {"ok": True}
    assert sleeps == [42.0]


def test_gives_up_after_the_last_attempt(monkeypatch: pytest.MonkeyPatch, ci: ModuleType) -> None:
    _serve(monkeypatch, ci, [b""] * 3)
    with pytest.raises(ci.TransientError, match="empty response"):
        ci.get_json("https://api.github.com/x", attempts=3)


def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch, ci: ModuleType) -> None:
    _serve(monkeypatch, ci, [_http_error(404, {})])
    with pytest.raises(urllib.error.HTTPError):
        ci.get_json("https://api.github.com/x")
