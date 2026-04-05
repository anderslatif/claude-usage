from datetime import datetime, timezone

def format_reset_window(reset_at: datetime | None) -> str:
    if reset_at is None:
        return "?"
    delta = reset_at - datetime.now(timezone.utc)
    total = int(delta.total_seconds())
    if total <= 0:
        return "now"
    days  = total // 86400
    hours = (total % 86400) // 3600
    mins  = (total % 3600) // 60
    if days >= 1:
        return f"{days}d"
    return f"{hours}h{mins:02d}m"