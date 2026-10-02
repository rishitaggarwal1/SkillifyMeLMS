"""Guard: hosts and ports that a browser or another machine would see are configuration, never
literals. Every externally visible URL comes from .env (WEB_ORIGIN, KEYCLOAK_PUBLIC_URL,
S3_PUBLIC_ENDPOINT_URL...), so the same code and compose files work on any host.

One test covers app code, compose files, Dockerfiles, the Caddyfile and the Keycloak realm
template; the failure lists every offending line. What may stay is allowed explicitly below:
Docker service-DNS addresses (`minio:9000`, resolved inside the compose network on any host),
container-side port mappings (`"${MINIO_PORT:-9000}:9000"`), and health probes that run inside
their own container."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

FORBIDDEN = re.compile(
    r"localhost|127\.0\.0\.1|:(?:3000|8000|8080|9000|9001|8025|1025)\b|infra/local/keycloak"
)

# Allowed everywhere: a compose service name with its in-network port, and the container side of
# a port mapping whose host side comes from .env.
SERVICE_DNS = re.compile(r"\b(?:api|web|minio|mailpit|keycloak|redpanda|postgres|redis):\d+\b")
PORT_MAPPING = re.compile(r'"\$\{[A-Z_]+(?::-\d+)?\}:\d+"')

# Allowed in one file only, each for a stated reason (file, exact line fragment).
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("docker-compose.yml", "exec 3<>/dev/tcp/127.0.0.1/9000 &&"): (
        "MinIO and Keycloak health probes connect to their own container"
    ),
    ("docker-compose.yml", "Host: localhost"): "the same probes' HTTP Host header",
    ("docker-compose.yml", "urlopen('http://127.0.0.1:8000/health/ready'"): (
        "API health probe inside the API container"
    ),
    ("docker-compose.yml", "wget -q -O /dev/null http://127.0.0.1:3000/"): (
        "web health probe inside the web container"
    ),
    ("docker-compose.yml", '"--console-address", ":9001"'): "MinIO console listen address",
    ("apps/api/Dockerfile", "urlopen('http://127.0.0.1:8000/health/live'"): (
        "image health check inside the API container"
    ),
    ("apps/web/Dockerfile", "wget -q -O /dev/null http://127.0.0.1:3000/"): (
        "image health check inside the web container"
    ),
}


def _files() -> list[Path]:
    web_src = [
        p
        for p in (REPO / "apps/web/src").rglob("*")
        if p.suffix in {".ts", ".tsx"} and ".test." not in p.name
    ]
    return sorted(
        {
            REPO / "docker-compose.yml",
            REPO / "docker-compose.prod.yml",
            REPO / "apps/api/Dockerfile",
            REPO / "apps/web/Dockerfile",
            REPO / "apps/web/next.config.ts",
            REPO / "infra/caddy/Caddyfile",
            *(REPO / "infra/keycloak").rglob("*.json"),
            *(REPO / "infra/keycloak").rglob("*.py"),
            *(REPO / "infra/keycloak").rglob("*.sh"),
            *(p for p in (REPO / "apps/api/app").rglob("*.py") if "tests" not in p.parts),
            *web_src,
        }
    )


def _offending(rel: str, line: str) -> bool:
    if any(rel == path and fragment in line for path, fragment in EXCEPTIONS):
        return False
    return bool(FORBIDDEN.search(PORT_MAPPING.sub("", SERVICE_DNS.sub("", line))))


def test_no_hardcoded_hosts_or_ports() -> None:
    files = _files()
    assert len(files) > 100, "the sweep should cover the app code"
    offenders = [
        f"{path.relative_to(REPO).as_posix()}:{number}: {line.strip()}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _offending(path.relative_to(REPO).as_posix(), line)
    ]
    listing = "\n".join(offenders)
    assert offenders == [], f"hardcoded host or port; read it from .env instead:\n{listing}"


def test_every_exception_is_still_needed() -> None:
    # A stale exception would quietly allow the same literal to come back elsewhere in the file.
    stale = [
        f"{path}: {fragment}"
        for path, fragment in EXCEPTIONS
        if fragment not in (REPO / path).read_text(encoding="utf-8")
    ]
    assert stale == []


def test_server_env_example_has_placeholders_only() -> None:
    text = (REPO / ".env.dev-server.example").read_text(encoding="utf-8")
    settings = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    assert not FORBIDDEN.search(text), "a server env must not point at localhost"
    secret_keys = [k for k in settings if re.search(r"PASSWORD|SECRET|_HASH", k)]
    assert secret_keys, "the example should list the secrets a server needs"
    for key in secret_keys:
        assert settings[key] in {"", "<SECRET>", "<BCRYPT_HASH>"}, f"{key} must be a placeholder"
    # The dev-only switches are off on a server.
    assert settings["SEED_DEV_USERS"] == "false"
    assert settings["KEYCLOAK_DEV_CLIENTS"] == "false"
    assert settings["KEYCLOAK_BRUTE_FORCE"] == "true"
    assert "skillifyme-test" not in settings["OIDC_ALLOWED_CLIENTS"]
