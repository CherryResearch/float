"""Fast validation guards for calendar recurrence rules."""

from typing import Any, Dict

_ALLOWED_FREQUENCIES = {
    "MINUTELY",
    "HOURLY",
    "DAILY",
    "WEEKLY",
    "MONTHLY",
    "YEARLY",
}


def validate_rrule_text(value: Any) -> str:
    """Validate the bounded RRULE subset used by Calendar background work.

    ``dateutil`` accepts a zero interval but can then loop forever while looking
    for the previous occurrence. Validate numeric semantics before constructing
    its iterator, and require dense minute schedules to have an explicit end.
    """

    raw = str(value or "").strip()
    if raw.upper().startswith("RRULE:"):
        raw = raw[6:].strip()
    if not raw:
        raise ValueError("RRULE cannot be empty")
    if "\n" in raw or "\r" in raw:
        raise ValueError("RRULE must contain one recurrence rule")

    values: Dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            raise ValueError(f"invalid RRULE segment: {part}")
        key, item = part.split("=", 1)
        key = key.strip().upper()
        item = item.strip()
        if not key or not item or key in values:
            raise ValueError(f"invalid RRULE segment: {part}")
        values[key] = item

    frequency = values.get("FREQ", "").upper()
    if frequency not in _ALLOWED_FREQUENCIES:
        raise ValueError(
            "RRULE FREQ must be MINUTELY, HOURLY, DAILY, WEEKLY, MONTHLY, or YEARLY"
        )

    for key in ("INTERVAL", "COUNT"):
        if key not in values:
            continue
        try:
            number = int(values[key])
        except ValueError as exc:
            raise ValueError(f"RRULE {key} must be a positive integer") from exc
        if number <= 0:
            raise ValueError(f"RRULE {key} must be a positive integer")
        if key == "COUNT" and number > 100_000:
            raise ValueError("RRULE COUNT may not exceed 100000")

    if "COUNT" in values and "UNTIL" in values:
        raise ValueError("RRULE may use COUNT or UNTIL, not both")
    if frequency == "MINUTELY" and not ({"COUNT", "UNTIL"} & values.keys()):
        raise ValueError("MINUTELY recurrence requires COUNT or UNTIL")
    return raw
