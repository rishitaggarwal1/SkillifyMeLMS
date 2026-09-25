"""Write the OpenAPI spec to a file without starting a server or touching any database.

python -m app.cli.export_openapi <output.json>
"""

import json
import sys
from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app


def main() -> None:
    if len(sys.argv) != 2:  # noqa: PLR2004
        sys.exit("usage: python -m app.cli.export_openapi <output.json>")
    # Placeholder connection settings: the spec does not depend on them and nothing connects.
    placeholder = SecretStr("postgresql+asyncpg://unused@localhost/unused")
    settings = Settings(
        environment="local",
        database_url=placeholder,
        migration_database_url=placeholder,
        redis_url=SecretStr("redis://localhost:6379/0"),
        _env_file=None,
    )
    spec = create_app(settings).openapi()
    Path(sys.argv[1]).write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
