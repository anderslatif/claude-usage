#!/usr/bin/env python3
"""
Claude Usage Menu Bar App
Displays Anthropic API usage and costs in the macOS menu bar.

API key is stored in the macOS Keychain. Set it via Settings → Set API Key…
Falls back to ANTHROPIC_ADMIN_KEY / ANTHROPIC_API_KEY env vars if no keychain entry.
"""

import json
import logging
import os
import threading
import traceback
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

LOG_FILE = Path.home() / "Library" / "Logs" / "claude_usage.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)

import keyring
import requests
import rumps
from dotenv import load_dotenv

load_dotenv()

KEYCHAIN_SERVICE = "claude_usage_mac_menu"
KEYCHAIN_USER    = "anthropic_admin_key"

SETTINGS_FILE = Path.home() / ".config" / "claude_usage" / "settings.json"
USAGE_URL = "https://api.anthropic.com/v1/organizations/usage_report/messages"
COST_URL  = "https://api.anthropic.com/v1/organizations/cost_report"
KEYS_URL  = "https://api.anthropic.com/v1/organizations/api_keys"
POLL_INTERVAL = 60  # seconds

DEFAULT_SETTINGS = {
    "session_budget": 0.0,    # daily budget (0 = disabled)
    "weekly_budget":  0.0,    # weekly budget (0 = disabled)
    "show_token_count": False,
    "reset_date": None,       # ISO date string for manual weekly reset
}


# ── Settings persistence ──────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE) as f:
                merged = DEFAULT_SETTINGS.copy()
                merged.update(json.load(f))
                return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


# ── API helpers ───────────────────────────────────────────────────────────────

def _get_api_key() -> str | None:
    return (
        keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
        or os.environ.get("ANTHROPIC_ADMIN_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def _get_headers() -> dict:
    key = _get_api_key()
    if not key:
        raise ValueError("No API key — open Settings → Set API Key…")
    return {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _fetch(url: str, starting_at: str, ending_at: str, limit: int = 31) -> dict:
    """Fetch from a report endpoint using date-only strings (YYYY-MM-DD)."""
    params: dict = {
        "starting_at": starting_at,
        "ending_at":   ending_at,
        "bucket_width": "1d",
        "limit": limit,
    }
    logging.debug("GET %s params=%s", url, params)
    r = requests.get(url, headers=_get_headers(), params=params, timeout=15)
    logging.debug("Response %s: %s", r.status_code, r.text[:500])
    if not r.ok:
        try:
            detail = r.json().get("error", {}).get("message", r.text)
        except Exception:
            detail = r.text
        raise ValueError(f"HTTP {r.status_code}: {detail}")
    return r.json()


def _fetch_usage(starting_at: str, ending_at: str) -> dict:
    """Fetch token usage (accepts date strings or RFC3339)."""
    params: dict = {
        "starting_at": starting_at,
        "ending_at":   ending_at,
        "bucket_width": "1d",
        "limit": 31,
    }
    logging.debug("GET %s params=%s", USAGE_URL, params)
    r = requests.get(USAGE_URL, headers=_get_headers(), params=params, timeout=15)
    logging.debug("Response %s: %s", r.status_code, r.text[:500])
    if not r.ok:
        try:
            detail = r.json().get("error", {}).get("message", r.text)
        except Exception:
            detail = r.text
        raise ValueError(f"HTTP {r.status_code}: {detail}")
    return r.json()


def _fetch_keys() -> list[dict]:
    """Fetch API key list. Returns [] on any error (endpoint may not be available)."""
    try:
        r = requests.get(KEYS_URL, headers=_get_headers(), params={"limit": 100}, timeout=10)
        if r.ok:
            return r.json().get("data", [])
    except Exception:
        pass
    return []


def _sum_cost(data: dict) -> float:
    """Return total cost in dollars from a cost-report response."""
    cents = sum(
        float(result.get("amount", 0))
        for bucket in data.get("data", [])
        for result in bucket.get("results", [])
    )
    return cents / 100.0


def _sum_tokens(data: dict) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from a usage-report response."""
    inp = out = 0
    for bucket in data.get("data", []):
        for r in bucket.get("results", []):
            inp += r.get("uncached_input_tokens", 0)
            inp += r.get("cache_read_input_tokens", 0)
            cache = r.get("cache_creation", {})
            inp += cache.get("ephemeral_1h_input_tokens", 0)
            inp += cache.get("ephemeral_5m_input_tokens", 0)
            out += r.get("output_tokens", 0)
    return inp, out


# ── Date helpers ──────────────────────────────────────────────────────────────

def _safe_cost_fetch(start: date, end: date) -> float:
    """
    Fetch cost between two dates. The cost API requires starting_at < today (UTC),
    so if start >= today we return 0 (data not yet available).
    end is used as the exclusive upper bound.
    """
    today = datetime.now(timezone.utc).date()
    if start >= today:
        return 0.0
    # Cap end at today (API doesn't need future ending_at but some ranges may fail)
    end = min(end, today + timedelta(days=1))
    try:
        data = _fetch(COST_URL, str(start), str(end))
        return _sum_cost(data)
    except Exception as e:
        logging.warning("Cost fetch failed (%s → %s): %s", start, end, e)
        return 0.0


def _progress_bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, int(fraction * width)))
    return "▓" * filled + "░" * (width - filled)


def _fmt_reset(delta: timedelta) -> str:
    """Format a timedelta as 'Xd Yh' or 'Xh Ym'."""
    total = int(delta.total_seconds())
    if total <= 0:
        return "now"
    days  = total // 86400
    hours = (total % 86400) // 3600
    mins  = (total % 3600) // 60
    if days >= 1:
        return f"{days}d {hours}h"
    return f"{hours}h {mins}m"


def _set_tooltip(item: rumps.MenuItem, text: str) -> None:
    try:
        item._menuitem.setToolTip_(text)
    except Exception:
        pass


# ── Main app ──────────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        self.settings = load_settings()

        # ── Menu items
        self.item_in_tok     = rumps.MenuItem("Input tokens today:  —")
        self.item_out_tok    = rumps.MenuItem("Output tokens today: —")

        self.item_session_hdr = rumps.MenuItem("Session (daily)")
        self.item_session_bar = rumps.MenuItem("  —")
        self.item_session_rst = rumps.MenuItem("  Resets in —")

        self.item_weekly_hdr  = rumps.MenuItem("Weekly")
        self.item_weekly_bar  = rumps.MenuItem("  —")
        self.item_weekly_rst  = rumps.MenuItem("  Resets in —")

        self.item_keys        = rumps.MenuItem("API Keys: —")
        self.item_updated     = rumps.MenuItem("Last updated: —")

        # ── Settings submenu
        self.item_set_key         = rumps.MenuItem("Set API Key…",           callback=self.on_set_api_key)
        self.item_set_session_bud = rumps.MenuItem("Set Session Budget…",    callback=self.on_set_session_budget)
        self.item_set_weekly_bud  = rumps.MenuItem("Set Weekly Budget…",     callback=self.on_set_weekly_budget)
        self.item_tog_tokens      = rumps.MenuItem("Show Token Count",       callback=self.on_toggle_tokens)
        self.item_reset_week      = rumps.MenuItem("Reset Weekly Progress",  callback=self.on_reset_weekly)
        self.item_debug_key       = rumps.MenuItem("Show Key Info",          callback=self.on_debug_key)

        settings_menu = rumps.MenuItem("Settings")
        settings_menu.add(self.item_set_key)
        settings_menu.add(self.item_debug_key)
        settings_menu.add(rumps.separator)
        settings_menu.add(self.item_set_session_bud)
        settings_menu.add(self.item_set_weekly_bud)
        settings_menu.add(rumps.separator)
        settings_menu.add(self.item_tog_tokens)
        settings_menu.add(rumps.separator)
        settings_menu.add(self.item_reset_week)

        self.menu = [
            self.item_in_tok,
            self.item_out_tok,
            rumps.separator,
            self.item_session_hdr,
            self.item_session_bar,
            self.item_session_rst,
            rumps.separator,
            self.item_weekly_hdr,
            self.item_weekly_bar,
            self.item_weekly_rst,
            rumps.separator,
            self.item_keys,
            rumps.separator,
            self.item_updated,
            rumps.separator,
            rumps.MenuItem("Refresh", callback=self.on_refresh),
            settings_menu,
            rumps.separator,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ]

        self._lock = threading.Lock()
        self._refresh_checkmarks()

        if not _get_api_key():
            self.title = "⚠️ Key needed"
            self.item_updated.title = "Open Settings → Set API Key… to get started"
        else:
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

    # ── Refresh ───────────────────────────────────────────────────────────────

    @rumps.timer(POLL_INTERVAL)
    def _auto_refresh(self, _):
        if _get_api_key():
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def on_refresh(self, _):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        try:
            now   = datetime.now(timezone.utc)
            today = now.date()
            yesterday = today - timedelta(days=1)
            tomorrow  = today + timedelta(days=1)
            monday    = today - timedelta(days=today.weekday())

            # Respect manual weekly reset date
            reset_date = self.settings.get("reset_date")
            if reset_date:
                rd = datetime.fromisoformat(reset_date).replace(tzinfo=timezone.utc).date()
                monday = max(monday, rd)

            # Token usage — usage API accepts today's date
            usage_data = _fetch_usage(str(today), str(tomorrow))
            in_tok, out_tok = _sum_tokens(usage_data)

            # Cost — API only returns completed days (starting_at must be < today)
            cost_session = _safe_cost_fetch(yesterday, today)   # yesterday's full day
            cost_weekly  = _safe_cost_fetch(monday,    today)   # Mon through yesterday
            # Note: "today" as ending_at gives us data through yesterday (exclusive)

            # API keys
            keys = _fetch_keys()

            self._render(now, in_tok, out_tok, cost_session, cost_weekly, keys)

        except Exception as exc:
            logging.error("Fetch failed:\n%s", traceback.format_exc())
            self._render_error(str(exc))

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, now, in_tok, out_tok, cost_session, cost_weekly, keys):
        def fmt(n):
            if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:     return f"{n / 1_000:.1f}k"
            return str(n)

        today = now.date()

        # Token counts
        self.item_in_tok.title  = f"Input tokens today:  {fmt(in_tok)}"
        self.item_out_tok.title = f"Output tokens today: {fmt(out_tok)}"

        # ── Session (daily) ──────────────────────────────────────────────────
        session_budget = self.settings["session_budget"]
        midnight_utc   = datetime(today.year, today.month, today.day,
                                  tzinfo=timezone.utc) + timedelta(days=1)
        session_reset  = _fmt_reset(midnight_utc - now)

        self.item_session_hdr.title = "Session (daily)"
        self.item_session_rst.title = f"  Resets in {session_reset}"

        if session_budget > 0:
            frac = min(cost_session / session_budget, 1.0)
            pct  = int(frac * 100)
            bar  = _progress_bar(frac)
            self.item_session_bar.title = f"  [{bar}] ${cost_session:.2f} / ${session_budget:.2f}"
            _set_tooltip(self.item_session_bar, f"{pct}%")
        else:
            self.item_session_bar.title = f"  ${cost_session:.4f} (no budget set)"
            _set_tooltip(self.item_session_bar, "Set a session budget in Settings")

        # ── Weekly ────────────────────────────────────────────────────────────
        weekly_budget = self.settings["weekly_budget"]
        days_to_monday = (7 - today.weekday()) % 7 or 7
        next_monday    = datetime(today.year, today.month, today.day,
                                  tzinfo=timezone.utc) + timedelta(days=days_to_monday)
        weekly_reset   = _fmt_reset(next_monday - now)

        self.item_weekly_hdr.title  = "Weekly"
        self.item_weekly_rst.title  = f"  Resets in {weekly_reset}"

        if weekly_budget > 0:
            frac = min(cost_weekly / weekly_budget, 1.0)
            pct  = int(frac * 100)
            bar  = _progress_bar(frac)
            self.item_weekly_bar.title = f"  [{bar}] ${cost_weekly:.2f} / ${weekly_budget:.2f}"
            _set_tooltip(self.item_weekly_bar, f"{pct}%")
        else:
            self.item_weekly_bar.title = f"  ${cost_weekly:.4f} (no budget set)"
            _set_tooltip(self.item_weekly_bar, "Set a weekly budget in Settings")

        # ── API keys ─────────────────────────────────────────────────────────
        if keys:
            lines = []
            for k in keys:
                name   = k.get("name", "unnamed")
                status = k.get("status", "?")
                hint   = k.get("partial_key_hint", "")
                lines.append(f"{name}  [{status}]  {hint}")
            self.item_keys.title = "API Keys: " + " | ".join(lines) if len(lines) == 1 else f"API Keys: {len(keys)} keys"
            _set_tooltip(self.item_keys, "\n".join(lines))
        else:
            self.item_keys.title = "API Keys: —"

        self.item_updated.title = f"Last updated: {now.astimezone().strftime('%I:%M:%S %p')} (cost data: yesterday)"

        # ── Menu bar title ────────────────────────────────────────────────────
        title = f"${cost_session:.2f}"
        if self.settings["show_token_count"]:
            title += f" {fmt(in_tok + out_tok)}"
        self.title = title

    def _render_error(self, msg: str):
        if "No API key" in msg:
            self.title = "⚠️ Key needed"
            self.item_updated.title = "Open Settings → Set API Key…"
        else:
            self.title = "⚠️"
            self.item_updated.title = f"Error: {msg[:120]}"

    # ── Settings callbacks ────────────────────────────────────────────────────

    def on_set_api_key(self, _):
        existing = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
        hint = f"Current: {existing[:16]}…" if existing else "No key stored yet."
        resp = rumps.Window(
            message=f"Paste your Anthropic API key (sk-ant-…)\n{hint}\n\nFind it at: console.anthropic.com → Settings → API Keys",
            title="Set API Key",
            default_text="",
            ok="Save",
            cancel="Cancel",
            dimensions=(340, 80),
        ).run()
        if resp.clicked:
            key = "".join(resp.text.split())
            if not key:
                return
            if not key.startswith("sk-ant-"):
                rumps.alert("Invalid Key", "The key should start with sk-ant-admin… or sk-ant-api…")
                return
            keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_USER, key)
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def on_debug_key(self, _):
        key = _get_api_key()
        if not key:
            rumps.alert("Key Info", "No key stored.")
            return
        rumps.alert(
            "Key Info",
            f"Length: {len(key)}\n"
            f"First 8: {key[:8]}\n"
            f"Last 4:  {key[-4:]}\n"
            f"Source: {'keychain' if keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USER) else 'env var'}"
        )

    def _on_set_budget(self, title: str, key: str):
        current = str(self.settings[key]) if self.settings[key] else ""
        resp = rumps.Window(
            message="Budget amount in USD (leave blank or 0 to disable):",
            title=title,
            default_text=current,
            ok="Set",
            cancel="Cancel",
            dimensions=(220, 22),
        ).run()
        if resp.clicked:
            text = resp.text.strip().lstrip("$")
            try:
                amount = float(text) if text else 0.0
                self.settings[key] = max(0.0, amount)
                save_settings(self.settings)
                threading.Thread(target=self._fetch_and_update, daemon=True).start()
            except ValueError:
                rumps.alert("Invalid input", "Enter a number, e.g. 50 or 50.00")

    def on_set_session_budget(self, _):
        self._on_set_budget("Set Session (Daily) Budget", "session_budget")

    def on_set_weekly_budget(self, _):
        self._on_set_budget("Set Weekly Budget", "weekly_budget")

    def on_toggle_tokens(self, _):
        self.settings["show_token_count"] = not self.settings["show_token_count"]
        save_settings(self.settings)
        self._refresh_checkmarks()
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def on_reset_weekly(self, _):
        self.settings["reset_date"] = datetime.now(timezone.utc).isoformat()
        save_settings(self.settings)
        threading.Thread(target=self._fetch_and_update, daemon=True).start()
        rumps.alert("Weekly Reset", "Weekly progress now counts from this moment.")

    def _refresh_checkmarks(self):
        self.item_tog_tokens.state = self.settings["show_token_count"]


if __name__ == "__main__":
    ClaudeUsageApp().run()
