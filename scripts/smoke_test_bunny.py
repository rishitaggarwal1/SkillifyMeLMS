"""Manual smoke test of our BunnyStreamProvider against a REAL Bunny Stream library.

Never run in CI and not wired into any Makefile target: it creates (and then deletes) a real
video, which needs real credentials and may be billed. Run it by hand once the BUNNY_* settings
are in the repo-root .env:

    uv run --project apps/api python scripts/smoke_test_bunny.py [--api-url URL] [--timeout SECONDS]

It refuses to start unless every BUNNY_* setting is present and non-empty, prints the exact
checks it will perform, then performs them in order and prints a pass/fail summary. The exit
status is non-zero if any check fails. The webhook check needs the local API running (`make dev`)
with the same BUNNY_* settings; it inserts one temporary video_assets row (as the database owner)
and removes it again.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
from uuid import UUID, uuid4

REPO = Path(__file__).resolve().parents[1]
API_DIR = REPO / "apps" / "api"
FIXTURE = API_DIR / "tests" / "fixtures" / "video.mp4"

# Settings read the repo-root .env relative to apps/api (see app/core/config.py).
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.modules.media.providers import BunnyStreamProvider, UploadTicket  # noqa: E402

OK, FORBIDDEN, CREATED, NO_CONTENT = 200, 403, 201, 204

REQUIRED = {
    "BUNNY_LIBRARY_ID": "bunny_library_id",
    "BUNNY_API_KEY": "bunny_api_key",
    "BUNNY_CDN_HOSTNAME": "bunny_cdn_hostname",
    "BUNNY_TOKEN_KEY": "bunny_token_key",
    "BUNNY_WEBHOOK_SECRET": "bunny_webhook_secret",
    "BUNNY_API_URL": "bunny_api_url",
}


def missing_settings(settings: Settings) -> list[str]:
    """BUNNY_* settings that are unset or empty."""
    missing = []
    for env_name, field in REQUIRED.items():
        value = getattr(settings, field)
        raw = value.get_secret_value() if hasattr(value, "get_secret_value") else value
        if not raw or not str(raw).strip():
            missing.append(env_name)
    return missing


class CheckFailedError(Exception):
    pass


def ensure(condition: object, message: str) -> None:
    """Fail a check. (Not `assert`: `python -O` would strip it and let every check pass.)"""
    if not condition:
        raise CheckFailedError(message)


@dataclass
class Context:
    settings: Settings
    http: httpx.AsyncClient
    provider: BunnyStreamProvider
    api_url: str
    timeout: int
    video_id: str | None = None
    ticket: UploadTicket | None = None
    asset_id: UUID | None = None


def plan(ctx: Context) -> list[tuple[str, Callable[[Context], Awaitable[str]]]]:
    library = ctx.settings.bunny_library_id
    api = ctx.settings.bunny_api_url.rstrip("/")
    return [
        (
            f"create_upload: BunnyStreamProvider creates a video in library {library} "
            f"(POST {api}/library/{library}/videos) and returns signed TUS headers "
            "(AuthorizationSignature, AuthorizationExpire, VideoId, LibraryId)",
            check_create_upload,
        ),
        (
            f"tus_upload: upload {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size} bytes) "
            f"over TUS (POST {api}/tusupload, then PATCH) with exactly those headers",
            check_tus_upload,
        ),
        (
            f"processing: poll refresh_status every 5 s until the video is ready "
            f"(timeout {ctx.timeout} s)",
            check_processing,
        ),
        (
            "webhook: insert a temporary video_assets row (provider bunny, status processing), "
            f"POST a Bunny-shaped payload {{VideoGuid, Status: 3}} to "
            f"{ctx.api_url}/api/v1/webhooks/video/bunny/<BUNNY_WEBHOOK_SECRET>, and check the row "
            "becomes ready through the webhook path (which re-fetches the status from Bunny)",
            check_webhook,
        ),
        (
            f"playback: GET the signed HLS URL from playback() on {ctx.settings.bunny_cdn_hostname}"
            " and expect HTTP 200 with an #EXTM3U manifest",
            check_playback,
        ),
        (
            "tampered token: the same URL with one token character changed must be refused (403)",
            check_tampered,
        ),
        (
            "expired token: a URL signed to expire in 1 s, fetched after 3 s,"
            " must be refused (403)",
            check_expired,
        ),
    ]


# ---------------------------------------------------------------------------------------- checks


async def check_create_upload(ctx: Context) -> str:
    created = await ctx.provider.create_upload(uuid4(), f"smoke test {int(time.time())}")
    ctx.video_id = created.provider_video_id
    ctx.ticket = created.ticket
    headers = created.ticket.headers
    expected = {"AuthorizationSignature", "AuthorizationExpire", "VideoId", "LibraryId"}
    ensure(created.ticket.protocol == "tus", created.ticket.protocol)
    ensure(expected <= set(headers), f"missing headers: {expected - set(headers)}")
    ensure(headers["VideoId"] == ctx.video_id, 'check failed: headers["VideoId"] == ctx.video_id')
    return f"video {ctx.video_id}"


async def check_tus_upload(ctx: Context) -> str:
    ensure(ctx.video_id and ctx.ticket, "no video created")
    data = FIXTURE.read_bytes()
    signed = ctx.ticket.headers  # exactly what the browser would get from POST /videos
    meta = ",".join(
        f"{key} {base64.b64encode(value.encode()).decode()}"
        for key, value in {"filetype": "video/mp4", "title": "smoke test"}.items()
    )
    start = await ctx.http.post(
        ctx.ticket.url,
        headers={**signed, "Tus-Resumable": "1.0.0", "Upload-Length": str(len(data)),
                 "Upload-Metadata": meta},
    )  # fmt: skip
    ensure(
        start.status_code == CREATED, f"TUS create returned {start.status_code}: {start.text[:200]}"
    )
    location = urljoin(ctx.ticket.url, start.headers["Location"])
    patch = await ctx.http.patch(
        location,
        content=data,
        headers={**signed, "Tus-Resumable": "1.0.0", "Upload-Offset": "0",
                 "Content-Type": "application/offset+octet-stream"},
    )  # fmt: skip
    ensure(
        patch.status_code == NO_CONTENT,
        f"TUS upload returned {patch.status_code}: {patch.text[:200]}",
    )
    return f"{len(data)} bytes accepted"


async def check_processing(ctx: Context) -> str:
    ensure(ctx.video_id, "check failed: ctx.video_id")
    deadline = time.monotonic() + ctx.timeout
    while True:
        status = await ctx.provider.refresh_status(ctx.video_id)
        if status.status == "ready":
            return f"ready, {status.duration_seconds} s long"
        ensure(status.status != "failed", f"Bunny failed to process the video: {status.error}")
        ensure(time.monotonic() < deadline, f"still {status.status} after {ctx.timeout} s")
        await asyncio.sleep(5)


async def check_webhook(ctx: Context) -> str:
    ensure(ctx.video_id, "check failed: ctx.video_id")
    owner = create_async_engine(ctx.settings.migration_database_url.get_secret_value())
    try:
        async with owner.begin() as conn:
            org = await conn.scalar(
                text("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
            )
            ensure(org, "no organization in the local database (run `make seed`)")
            ctx.asset_id = await conn.scalar(
                text(
                    "INSERT INTO video_assets (id, organization_id, provider, provider_video_id,"
                    " title, status) VALUES (:id, :org, 'bunny', :vid, 'smoke test', 'processing')"
                    " RETURNING id"
                ),
                {"id": uuid4(), "org": org, "vid": ctx.video_id},
            )
        secret = ctx.settings.bunny_webhook_secret.get_secret_value()  # type: ignore[union-attr]
        response = await ctx.http.post(
            f"{ctx.api_url}/api/v1/webhooks/video/bunny/{secret}",
            json={"VideoLibraryId": int(str(ctx.settings.bunny_library_id)),
                  "VideoGuid": ctx.video_id, "Status": 3},
        )  # fmt: skip
        ensure(
            response.status_code == OK,
            f"webhook returned {response.status_code} (is the API running with these BUNNY_* "
            "settings?)",
        )
        async with owner.connect() as conn:
            status = await conn.scalar(
                text("SELECT status FROM video_assets WHERE id = :id"), {"id": ctx.asset_id}
            )
        ensure(status == "ready", f"asset is {status!r} after the webhook")
        return "asset marked ready by the webhook"
    finally:
        await owner.dispose()


async def _playback_url(ctx: Context, ttl: int) -> str:
    ensure(ctx.video_id, "check failed: ctx.video_id")
    return (await ctx.provider.playback(ctx.video_id, ttl)).url


async def check_playback(ctx: Context) -> str:
    url = await _playback_url(ctx, 300)
    response = await ctx.http.get(url)
    ensure(response.status_code == OK, f"manifest returned {response.status_code}")
    ensure(response.text.lstrip().startswith("#EXTM3U"), "not an HLS manifest")
    return "HLS manifest served"


async def check_tampered(ctx: Context) -> str:
    url = await _playback_url(ctx, 300)
    marker = "bcdn_token="
    i = url.index(marker) + len(marker)
    tampered = url[:i] + ("A" if url[i] != "A" else "B") + url[i + 1 :]
    response = await ctx.http.get(tampered)
    ensure(
        response.status_code == FORBIDDEN,
        f"tampered token returned {response.status_code} "
        "(is token authentication enabled on the pull zone?)",
    )
    return "refused (403)"


async def check_expired(ctx: Context) -> str:
    url = await _playback_url(ctx, 1)
    await asyncio.sleep(3)
    response = await ctx.http.get(url)
    ensure(response.status_code == FORBIDDEN, f"expired token returned {response.status_code}")
    return "refused (403)"


async def cleanup(ctx: Context) -> list[str]:
    notes = []
    if ctx.asset_id:
        owner = create_async_engine(ctx.settings.migration_database_url.get_secret_value())
        try:
            async with owner.begin() as conn:
                await conn.execute(text("DELETE FROM video_assets WHERE id = :id"),
                                   {"id": ctx.asset_id})  # fmt: skip
            notes.append("removed the temporary video_assets row")
        finally:
            await owner.dispose()
    if ctx.video_id:
        await ctx.provider.delete(ctx.video_id)
        notes.append(f"deleted Bunny video {ctx.video_id}")
    return notes


# ---------------------------------------------------------------------------------------- runner


async def run(args: argparse.Namespace, settings: Settings) -> int:
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
        ctx = Context(
            settings=settings, http=http, provider=BunnyStreamProvider(settings, http),
            api_url=args.api_url.rstrip("/"), timeout=args.timeout,
        )  # fmt: skip
        checks = plan(ctx)
        print("Bunny Stream smoke test. It will perform these checks, in order:")
        for number, (description, _) in enumerate(checks, start=1):
            print(f"  {number}. {description}")
        print(
            "  then: delete the test video and the temporary row (always, even after a failure)\n"
        )

        results: list[tuple[str, str, str]] = []
        failed = False
        for number, (description, check) in enumerate(checks, start=1):
            name = description.split(":", 1)[0]
            if failed:
                results.append((name, "SKIP", "not run: an earlier check failed"))
                continue
            print(f"[{number}/{len(checks)}] {name} ...", flush=True)
            try:
                detail = await check(ctx)
                results.append((name, "PASS", detail))
            except Exception as exc:  # report every failure, then clean up
                failed = True
                results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))
                if args.verbose:
                    traceback.print_exc()
        try:
            for note in await cleanup(ctx):
                print(f"cleanup: {note}")
        except Exception as exc:  # cleanup must report, not crash
            failed = True
            print(f"cleanup FAILED: {type(exc).__name__}: {exc} (delete the video by hand)")

        print("\nSummary:")
        for name, outcome, detail in results:
            print(f"  {outcome}  {name}: {detail}")
        print(f"\n{'FAILED' if failed else 'PASSED'}")
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--api-url",
        default=f"http://localhost:{os.environ.get('API_PORT', '8000')}",
        help="the local API that receives the webhook (default: http://localhost:$API_PORT)",
    )
    parser.add_argument("--timeout", type=int, default=600, help="seconds to wait for processing")
    parser.add_argument("--verbose", action="store_true", help="print tracebacks for failures")
    args = parser.parse_args()

    settings = Settings()
    missing = missing_settings(settings)
    if missing:
        print(
            "Refusing to run: these settings are missing or empty: " + ", ".join(missing) + ".\n"
            "Set every BUNNY_* value in the repo-root .env (see .env.example). Nothing was sent.",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(asyncio.run(run(args, settings)))


if __name__ == "__main__":
    main()
