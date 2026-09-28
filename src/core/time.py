from datetime import datetime, timezone


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
