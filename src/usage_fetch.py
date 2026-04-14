import requests
import logging
from datetime import datetime, timezone

from .config import MESSAGES_URL, OAUTH_CLIENT_ID, PLATFORM_URL
from .OAuth_credentials import get_valid_token, read_claude_code_creds, write_claude_code_creds

def fetch_utilization() -> dict:
    """
    Send a minimal 1-token message to api.anthropic.com and read the rate-limit
    utilization headers that claude.ai subscribers receive.

    Returns:
        {
            "five_hour":  {"utilization": 0.19, "reset_at": <datetime>},
            "seven_day":  {"utilization": 0.94, "reset_at": <datetime>},
        }
    """
    token = get_valid_token()
    r = requests.post(
        MESSAGES_URL,
        headers={
            "Authorization":        f"Bearer {token}",
            "anthropic-version":    "2023-06-01",
            "anthropic-beta":       "oauth-2025-04-20",
            "content-type":         "application/json",
        },
        json={
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages":   [{"role": "user", "content": "hi"}],
        },
        timeout=20,
    )
    logging.debug("Messages API status=%s", r.status_code)
    if not r.ok:
        if r.status_code != 429:
            raise RuntimeError(f"Messages API HTTP {r.status_code}: {r.text[:200]}")

    def _reset_dt(unix_str: str | None) -> datetime | None:
        if not unix_str:
            return None
        try:
            return datetime.fromtimestamp(int(unix_str), tz=timezone.utc)
        except Exception:
            return None

    h = r.headers
    return {
        "five_hour": {
            "utilization": float(h.get("anthropic-ratelimit-unified-5h-utilization", 1.0 if not r.ok else 0)),
            "reset_at":    _reset_dt(h.get("anthropic-ratelimit-unified-5h-reset")),
        },
        "seven_day": {
            "utilization": float(h.get("anthropic-ratelimit-unified-7d-utilization", 1.0 if not r.ok else 0)),
            "reset_at":    _reset_dt(h.get("anthropic-ratelimit-unified-7d-reset")),
        },
    }