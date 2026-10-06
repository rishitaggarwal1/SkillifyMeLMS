"""The deployed worker import must register the expiry sweeper with configured timing."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("interval", [5.0, 17.5])
def test_quiz_expiry_sweeper_registered_at_configured_interval(interval: float) -> None:
    # A fresh interpreter exercises settings/env loading and the actual worker
    # module, avoiding get_settings cache or Celery registry leakage between tests.
    script = """
import json
from app.worker import celery_app
celery_app.loader.import_default_modules()
entries = [e for e in celery_app.conf.beat_schedule.values()
           if e['task'] == 'assessments.sweep_expired']
print(json.dumps({'entries': entries, 'registered':
                 'assessments.sweep_expired' in celery_app.tasks}))
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository module
        [sys.executable, "-c", script],
        env={**os.environ, "QUIZ_EXPIRY_SWEEP_INTERVAL_SECONDS": str(interval)},
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data == {
        "entries": [{"task": "assessments.sweep_expired", "schedule": interval}],
        "registered": True,
    }
