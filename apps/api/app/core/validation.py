"""Process-wide validation settings.

email-validator rejects special-use domains (RFC 6761). Outside production we allow the two our dev
and test data use (`*.local` in the dev Keycloak realm, `*.test` in automated tests); production
keeps the strict defaults.
"""

import email_validator

from app.core.config import Settings

_DEV_ONLY_DOMAINS = ("local", "test")


def configure_email_validation(settings: Settings) -> None:
    if settings.environment == "production":
        return
    for domain in _DEV_ONLY_DOMAINS:
        if domain in email_validator.SPECIAL_USE_DOMAIN_NAMES:
            email_validator.SPECIAL_USE_DOMAIN_NAMES.remove(domain)
