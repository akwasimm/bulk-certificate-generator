"""Per-recipient value validation."""
import re

from ..schemas import RecipientInput

MAX_NAME_LENGTH = 120

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_recipient(recipient: RecipientInput) -> tuple[str | None, str | None]:
    """Return (name, None) when valid, else (None, reason)."""
    name = " ".join((recipient.full_name or "").split())
    if not name:
        return None, "Rejected: recipient name is missing or blank."
    if len(name) > MAX_NAME_LENGTH:
        return None, f"Rejected: recipient name exceeds the maximum of {MAX_NAME_LENGTH} characters."

    email = (recipient.email or "").strip()
    if email and not _EMAIL_RE.match(email):
        return None, "Rejected: recipient email address is not valid."

    return name, None
