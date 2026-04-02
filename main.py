#!/usr/bin/env python3
"""
Claude Usage Menu Bar App
Displays claude.ai 5-hour session and 7-day weekly utilization in the macOS menu bar.

Reads the OAuth token that Claude Code already stores in the macOS Keychain under
"Claude Code-credentials" — no separate API key required.
"""

import argparse
import json
import logging
import os
import subprocess
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from AppKit import (
    NSImage, NSColor, NSBezierPath, NSFont,
    NSMutableAttributedString, NSAttributedString,
    NSTextAttachment, NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
)
from Foundation import NSMakeRect, NSOperationQueue
from CoreText import (
    CTLineCreateWithAttributedString,
    CTLineDraw,
    CTLineGetTypographicBounds,
)
from Quartz.CoreGraphics import (
    CGContextSetTextMatrix,
    CGContextSetTextPosition,
    CGAffineTransformIdentity,
)

LOG_FILE = Path.home() / "Library" / "Logs" / "claude_usage.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
    ],
)

import requests
import rumps
from dotenv import load_dotenv

load_dotenv()

MESSAGES_URL    = "https://api.anthropic.com/v1/messages"
OAUTH_CLIENT_ID = "9d1c250a-e61b-48ad-a7e6-0b5d4eb16f10"
PLATFORM_URL    = "https://platform.claude.com"
POLL_INTERVAL   = 60  # seconds

_BAT_W        = 62
_BAT_H        = 18
_BAT_BASELINE = -3  # vertical nudge to align battery with menu bar text baseline


# ── Claude Code OAuth credentials ─────────────────────────────────────────────

def _read_claude_code_creds() -> dict:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Claude Code-credentials not found in keychain. Is Claude Code installed and logged in?")
    return json.loads(result.stdout.strip())


def _write_claude_code_creds(creds: dict) -> None:
    blob = json.dumps(creds)
    subprocess.run(["security", "delete-generic-password", "-s", "Claude Code-credentials"],
                   capture_output=True)
    subprocess.run(
        ["security", "add-generic-password", "-s", "Claude Code-credentials",
         "-a", os.environ.get("USER", ""), "-w", blob],
        capture_output=True, check=True,
    )


def _get_valid_token() -> str:
    """Return a valid access token, refreshing via OAuth if expired."""
    creds  = _read_claude_code_creds()
    oauth  = creds["claudeAiOauth"]

    expires_at_ms = oauth.get("expiresAt", 0)
    now_ms        = time.time() * 1000

    if now_ms < expires_at_ms - 60_000:
        return oauth["accessToken"]

    logging.info("OAuth token expired — refreshing")
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
    _write_claude_code_creds(creds)
    logging.info("Keychain updated with refreshed token")
    return oauth["accessToken"]



def _is_logged_in() -> bool:
    try:
        _read_claude_code_creds()
        return True
    except Exception:
        return False


# ── Usage fetch ───────────────────────────────────────────────────────────────

def _fetch_utilization() -> dict:
    """
    Send a minimal 1-token message to api.anthropic.com and read the rate-limit
    utilization headers that claude.ai subscribers receive.

    Returns:
        {
            "five_hour":  {"utilization": 0.19, "reset_at": <datetime>},
            "seven_day":  {"utilization": 0.94, "reset_at": <datetime>},
        }
    """
    token = _get_valid_token()
    r = requests.post(
        MESSAGES_URL,
        headers={
            "x-api-key":            token,
            "anthropic-version":    "2023-06-01",
            "content-type":         "application/json",
        },
        json={
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages":   [{"role": "user", "content": "hi"}],
        },
        timeout=20,
    )
    logging.debug("Messages API %s headers=%s", r.status_code, dict(r.headers))
    if not r.ok:
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
            "utilization": float(h.get("anthropic-ratelimit-unified-5h-utilization", 0)),
            "reset_at":    _reset_dt(h.get("anthropic-ratelimit-unified-5h-reset")),
        },
        "seven_day": {
            "utilization": float(h.get("anthropic-ratelimit-unified-7d-utilization", 0)),
            "reset_at":    _reset_dt(h.get("anthropic-ratelimit-unified-7d-reset")),
        },
    }


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_reset(reset_at: datetime | None) -> str:
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


# ── Battery icon drawing ──────────────────────────────────────────────────────

def _battery_image(fraction: float | None, label: str) -> NSImage:
    """
    fraction — 0.0–1.0 fill level (amount used); None = unknown (grey outline only)
    label    — time-remaining string drawn centred inside the battery body
    """
    image = NSImage.alloc().initWithSize_((_BAT_W, _BAT_H))
    image.lockFocus()

    pad       = 1
    bump_w    = 4
    bump_h    = int(_BAT_H * 0.4)
    body_w    = _BAT_W - bump_w - pad * 2
    body_h    = _BAT_H - pad * 2
    bx, by    = float(pad), float(pad)
    r         = 3.0
    inner_pad = 2

    NSColor.clearColor().set()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, _BAT_W, _BAT_H))

    fill_frac = fraction if fraction is not None else 0.0
    fill_w = max(0.0, (body_w - inner_pad * 2) * min(fill_frac, 1.0))
    if fill_w > 0:
        NSColor.whiteColor().colorWithAlphaComponent_(0.6).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(bx + inner_pad, by + inner_pad, fill_w, body_h - inner_pad * 2),
            max(1.0, r - 1), max(1.0, r - 1),
        ).fill()

    outline_color = NSColor.systemOrangeColor()

    outline_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(bx, by, float(body_w), float(body_h)), r, r
    )
    outline_path.setLineWidth_(1.5)
    outline_color.setStroke()
    outline_path.stroke()

    outline_color.setFill()
    NSBezierPath.fillRect_(NSMakeRect(
        bx + body_w, by + (body_h - bump_h) / 2, float(bump_w), float(bump_h),
    ))

    if label:
        font_size = 8.5
        font = NSFont.systemFontOfSize_(font_size)
        text_color = NSColor.systemOrangeColor()
        attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: text_color}
        attr_str = NSAttributedString.alloc().initWithString_attributes_(label, attrs)

        ct_line = CTLineCreateWithAttributedString(attr_str)
        adv_w = CTLineGetTypographicBounds(ct_line, None, None, None)
        if not isinstance(adv_w, (int, float)):
            adv_w = adv_w[0]

        cg_ctx = NSGraphicsContext.currentContext().CGContext()
        CGContextSetTextMatrix(cg_ctx, CGAffineTransformIdentity)

        ascender  = font.ascender()
        descender = font.descender()
        cap_h     = ascender - descender
        text_x = bx + inner_pad + (body_w - inner_pad * 2 - adv_w) / 2
        text_y = by + (body_h - cap_h) / 2 - descender
        CGContextSetTextPosition(cg_ctx, text_x, text_y)
        CTLineDraw(ct_line, cg_ctx)

    image.unlockFocus()
    image.setSize_((_BAT_W, _BAT_H))
    return image


def _battery_attachment(fraction: float | None, label: str) -> NSAttributedString:
    attachment = NSTextAttachment.alloc().init()
    attachment.setImage_(_battery_image(fraction, label))
    attachment.setBounds_(NSMakeRect(0, _BAT_BASELINE, _BAT_W, _BAT_H))
    return NSAttributedString.attributedStringWithAttachment_(attachment)


# ── Main app ──────────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)

        self.item_updated      = rumps.MenuItem("Last updated: —")
        self.item_session_util = rumps.MenuItem("5h session: —")
        self.item_weekly_util  = rumps.MenuItem("7d weekly: —")

        self.menu = [
            self.item_updated,
            rumps.separator,
            self.item_session_util,
            rumps.separator,
            self.item_weekly_util,
            rumps.separator,
            rumps.MenuItem("Refresh", callback=self.on_refresh),
            rumps.separator,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ]

        if not _is_logged_in():
            self._set_bar("⚠️", "Claude Code not logged in — run `claude` in terminal first")
        else:
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

    # ── Menu bar title ────────────────────────────────────────────────────────

    def _set_bar(self, plain_title: str, updated_msg: str | None = None) -> None:
        if hasattr(self, '_nsapp'):
            self._nsapp.nsstatusitem.button().setTitle_(plain_title)
        if updated_msg:
            self.item_updated.title = updated_msg

    def _set_bar_batteries(
        self,
        session_frac: float | None, session_reset: str, session_tooltip: str,
        weekly_frac:  float | None, weekly_reset:  str, weekly_tooltip:  str,
    ) -> None:
        label_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(11),
            NSForegroundColorAttributeName: NSColor.systemOrangeColor(),
        }

        def _as(text):
            return NSMutableAttributedString.alloc().initWithString_attributes_(text, label_attrs)

        bar = NSMutableAttributedString.alloc().initWithString_attributes_("", {})
        bar.appendAttributedString_(_as("Session "))
        bar.appendAttributedString_(_battery_attachment(session_frac, session_reset))
        bar.appendAttributedString_(_as("  Weekly "))
        bar.appendAttributedString_(_battery_attachment(weekly_frac, weekly_reset))
        si = self._nsapp.nsstatusitem
        si.button().setAttributedTitle_(bar)
        si.button().setToolTip_(
            f"Session: {session_tooltip}\nWeekly: {weekly_tooltip}"
        )

    # ── Refresh ───────────────────────────────────────────────────────────────

    @rumps.timer(POLL_INTERVAL)
    def _auto_refresh(self, _):
        if _is_logged_in():
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def on_refresh(self, _):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        try:
            data = _fetch_utilization()
            NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: self._render(data))
        except Exception:
            logging.error("Fetch failed:\n%s", traceback.format_exc())
            msg = traceback.format_exc().splitlines()[-1]
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: self._set_bar("⚠️", f"Error: {msg[:120]}")
            )

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, data: dict):
        now = datetime.now(timezone.utc)

        s = data["five_hour"]
        w = data["seven_day"]

        s_util     = s["utilization"]          # 0.0–1.0
        w_util     = w["utilization"]
        s_reset    = _fmt_reset(s["reset_at"])
        w_reset    = _fmt_reset(w["reset_at"])
        s_tooltip  = f"{s_util*100:.0f}% used  ·  resets in {s_reset}"
        w_tooltip  = f"{w_util*100:.0f}% used  ·  resets in {w_reset}"

        self._set_bar_batteries(s_util, s_reset, s_tooltip, w_util, w_reset, w_tooltip)

        local_now = now.astimezone()
        self.item_updated.title      = f"Last updated: {local_now.strftime('%I:%M:%S %p')}"
        self.item_session_util.title = f"5h session:  {s_util*100:.0f}% used  ·  resets in {s_reset}"
        self.item_weekly_util.title  = f"7d weekly:   {w_util*100:.0f}% used  ·  resets in {w_reset}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Usage menu bar app")
    parser.add_argument("--debug", action="store_true",
                        help="Run in foreground with console logging")
    args = parser.parse_args()

    if args.debug:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(console)
        logging.info("Debug mode — running in foreground (logs: %s)", LOG_FILE)

    ClaudeUsageApp().run()
