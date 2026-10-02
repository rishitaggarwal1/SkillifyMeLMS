"""CSV cells that are safe to open in a spreadsheet (OWASP CSV injection)."""

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> str:
    """The value as text; cells a spreadsheet would run as a formula get a leading quote."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text
