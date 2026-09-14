"""Display helpers."""

from datetime import UTC, datetime


def format_duration(epoch_seconds: int) -> str:
    """Format a Unix timestamp as an ISO-8601 UTC wall-clock string."""
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
