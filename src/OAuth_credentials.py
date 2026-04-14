import os
import logging
import subprocess
import json
import time
import requests
import keyring

from .config import PLATFORM_URL, OAUTH_CLIENT_ID

def read_claude_code_creds() -> dict:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Claude Code-credentials not found in keychain. Is Claude Code installed and logged in?")
    return json.loads(result.stdout.strip())


def write_claude_code_creds(creds: dict) -> None:
    blob = json.dumps(creds)
    account = os.environ.get("USER", "")
    # Delete the old entry first so keyring.set_password creates a clean update.
    # The delete passes only the service name, never the secret.
    subprocess.run(["security", "delete-generic-password", "-s", "Claude Code-credentials"],
                   capture_output=True)
    # Use the keyring library (Security framework) instead of the security CLI so
    # the credentials blob is never exposed as a command-line argument.
    keyring.set_password("Claude Code-credentials", account, blob)


def get_valid_token() -> str:
    """Return a valid access token, refreshing via OAuth if expired."""
    creds  = read_claude_code_creds()
    oauth  = creds["claudeAiOauth"]

    expires_at_ms = oauth.get("expiresAt", 0)
    now_ms        = time.time() * 1000

    if now_ms < expires_at_ms - 60_000:
        return oauth["accessToken"]

    logging.info("OAuth token expired - refreshing")
    r = requests.post(
        f"{PLATFORM_URL}/v1/oauth/token",
        json={
            "grant_type":    "refresh_token",
            "refresh_token": oauth["refreshToken"],
            "client_id":     OAUTH_CLIENT_ID,
        },
        timeout=15,
    )
    if not r.ok:
        raise RuntimeError(f"Token refresh failed: HTTP {r.status_code} {r.text[:200]}")

    new_token = r.json()
    oauth["accessToken"] = new_token.get("access_token", oauth["accessToken"])
    if "refresh_token" in new_token:
        oauth["refreshToken"] = new_token["refresh_token"]
    if "expires_in" in new_token:
        oauth["expiresAt"] = int((time.time() + new_token["expires_in"]) * 1000)
    creds["claudeAiOauth"] = oauth
    write_claude_code_creds(creds)
    logging.info("Keychain updated with refreshed token")
    return oauth["accessToken"]



def is_logged_in() -> bool:
    try:
        read_claude_code_creds()
        return True
    except Exception:
        return False
