"""Emitted event payloads must match the JSON Schemas documented in docs/events.md."""

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.identity.tests.conftest import OrgSetup
from tests.factories import Factory

EVENTS_DOC = Path(__file__).resolve().parents[6] / "docs" / "events.md"


def _documented_schemas() -> dict[str, dict[str, Any]]:
    doc = EVENTS_DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"### `(\w+)` \(version \d+\)\s+```json\n(.*?)```", doc, re.S)
    return {name: json.loads(body) for name, body in blocks}


SCHEMAS = _documented_schemas()


def test_both_batch_events_are_documented() -> None:
    assert set(SCHEMAS) >= {"batch_member_added", "batch_member_removed"}


@pytest.mark.parametrize("event_type", ["batch_member_added", "batch_member_removed"])
async def test_emitted_payloads_match_documented_schema(
    client: AsyncClient,
    org_setup: OrgSetup,
    factory: Factory,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    event_type: str,
) -> None:
    newcomer = await factory.member(org_setup.org, "student")
    h = org_setup.h(org_setup.admin)
    await client.post(
        f"/api/v1/batches/{org_setup.batch.id}/members",
        headers=h,
        json={"user_ids": [str(newcomer.id)]},
    )
    await client.delete(f"/api/v1/batches/{org_setup.batch.id}/members/{newcomer.id}", headers=h)
    async with owner_sessionmaker() as s:
        payload = await s.scalar(
            text(
                "SELECT payload FROM outbox_events WHERE event_type = :t "
                "AND payload->>'user_id' = :u"
            ),
            {"t": event_type, "u": str(newcomer.id)},
        )
    assert payload is not None
    jsonschema.validate(payload, SCHEMAS[event_type])
