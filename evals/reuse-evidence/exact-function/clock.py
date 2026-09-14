"""Elapsed-time helpers used by the local UI."""


def format_duration_ms(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def now_label() -> str:
    return "live"
