"""Guard: service ports are configuration, never literals. The Keycloak port in particular must flow
from KEYCLOAK_PORT into compose, the realm import, the API settings and the web app."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Files where a literal Keycloak port would silently break a non-default KEYCLOAK_PORT.
CHECKED = [
    "docker-compose.yml",
    "Makefile",
    *(str(p.relative_to(REPO)) for p in (REPO / "infra/local/keycloak").rglob("*") if p.is_file()),
    *(str(p.relative_to(REPO)) for p in (REPO / "apps/api/app").rglob("*.py")),
    *(
        str(p.relative_to(REPO))
        for p in (REPO / "apps/web/src").rglob("*")
        if p.is_file() and p.suffix in {".ts", ".tsx"}
    ),
    *(str(p.relative_to(REPO)) for p in (REPO / "apps/web").glob("*.ts")),
    *(str(p.relative_to(REPO)) for p in (REPO / "apps/web").glob("*.mjs")),
]


def test_no_hardcoded_keycloak_port() -> None:
    # One test for every file (the count doesn't grow with the codebase); the failure lists every
    # offender with its line numbers.
    offenders = [
        f"{path}:{number}"
        for path in CHECKED
        for number, line in enumerate((REPO / path).read_text(encoding="utf-8").splitlines(), 1)
        if "8080" in line
    ]
    listing = "\n".join(offenders)
    assert offenders == [], f"hardcoded 8080; derive it from KEYCLOAK_PORT:\n{listing}"


def test_env_example_declares_the_default_once() -> None:
    lines = [
        line
        for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
        if "8080" in line and not line.lstrip().startswith("#")
    ]
    assert lines == ["KEYCLOAK_PORT=8080"]
