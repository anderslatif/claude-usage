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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta, date
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
COST_URL  = "https://api.anthropic.com/v1/organizations/cost_report"
POLL_INTERVAL = 60  # seconds

_BAT_W        = 62
_BAT_H        = 18
_BAT_BASELINE = -3  # vertical nudge to align battery with menu bar text baseline

DEFAULT_SETTINGS = {
    "session_budget": 0.0,
    "weekly_budget":  0.0,
    "reset_date": None,
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


def _sum_cost(data: dict) -> float:
    cents = sum(
        float(result.get("amount", 0))
        for bucket in data.get("data", [])
        for result in bucket.get("results", [])
    )
    return cents / 100.0


# ── Date helpers ──────────────────────────────────────────────────────────────

def _safe_cost_fetch(start: date, end: date, today: date) -> float:
    """
    Fetch total cost between two dates.
    The cost API requires starting_at strictly before today (UTC) — completed days only.
    """
    if start >= today:
        return 0.0
    try:
        data = _fetch(COST_URL, str(start), str(min(end, today)))
        return _sum_cost(data)
    except Exception as e:
        logging.warning("Cost fetch failed (%s → %s): %s", start, end, e)
        return 0.0


def _fmt_reset(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total <= 0:
        return "now"
    days  = total // 86400
    hours = (total % 86400) // 3600
    mins  = (total % 3600) // 60
    if days >= 1:
        return f"{days}d"
    return f"{hours}h{mins:02d}m"


def _compute_battery_state(budget: float, spent: float) -> tuple[float | None, str]:
    """Return (fill_fraction, tooltip). fraction=None when no budget is configured."""
    if budget > 0:
        frac = min(1.0, spent / budget)
        remaining = max(0.0, budget - spent)
        return frac, f"{int(frac * 100)}% used  (${remaining:.2f} remaining of ${budget:.2f})"
    return None, f"${spent:.4f} spent  —  set a budget in Settings"


# ── Battery icon drawing ──────────────────────────────────────────────────────

def _battery_image(fraction: float | None, label: str) -> NSImage:
    """
    Draw and return an NSImage of a battery (_BAT_W × _BAT_H) with label text inside.

    fraction — 0.0–1.0 fill level (amount used); None = no budget (grey outline only)
    label    — time-remaining string drawn centred inside the battery body via CoreText
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

    if fraction is not None and fraction > 0:
        fill_w = max(float(inner_pad), (body_w - inner_pad * 2) * min(fraction, 1.0))
        NSColor.systemOrangeColor().setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(bx + inner_pad, by + inner_pad, fill_w, body_h - inner_pad * 2),
            max(1.0, r - 1), max(1.0, r - 1),
        ).fill()

    outline_color = NSColor.labelColor() if fraction is not None else NSColor.tertiaryLabelColor()

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

    # Draw label via CoreText — avoids NSString glyph-tree crash in off-screen bitmaps
    if label:
        font_size = 8.5
        font = NSFont.systemFontOfSize_(font_size)
        # White text on visible orange fill; outline colour otherwise
        text_color = NSColor.whiteColor() if (fraction is not None and fraction > 0.25) else outline_color
        attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: text_color}
        attr_str = NSAttributedString.alloc().initWithString_attributes_(label, attrs)

        ct_line = CTLineCreateWithAttributedString(attr_str)
        adv_w = CTLineGetTypographicBounds(ct_line, None, None, None)
        if not isinstance(adv_w, (int, float)):
            adv_w = adv_w[0]  # PyObjC may return a tuple

        cg_ctx = NSGraphicsContext.currentContext().CGContext()
        CGContextSetTextMatrix(cg_ctx, CGAffineTransformIdentity)

        # Baseline: vertically centre cap-height inside body
        ascender  = font.ascender()
        descender = font.descender()   # negative
        cap_h     = ascender - descender
        text_x = bx + inner_pad + (body_w - inner_pad * 2 - adv_w) / 2
        text_y = by + (body_h - cap_h) / 2 - descender
        CGContextSetTextPosition(cg_ctx, text_x, text_y)
        CTLineDraw(ct_line, cg_ctx)

    image.unlockFocus()
    image.setSize_((_BAT_W, _BAT_H))  # lockFocus on Retina can double the logical size
    return image


def _battery_attachment(fraction: float | None, label: str) -> NSAttributedString:
    """Return a battery image (with label inside) as an inline NSAttributedString attachment."""
    attachment = NSTextAttachment.alloc().init()
    attachment.setImage_(_battery_image(fraction, label))
    attachment.setBounds_(NSMakeRect(0, _BAT_BASELINE, _BAT_W, _BAT_H))
    return NSAttributedString.attributedStringWithAttachment_(attachment)



# ── Main app ──────────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        self.settings = load_settings()

        # ── Menu items
        self.item_updated = rumps.MenuItem("Last updated: —")

        # ── Settings submenu (API key only)
        self.item_set_key = rumps.MenuItem("Set API Key…", callback=self.on_set_api_key)

        settings_menu = rumps.MenuItem("Settings")
        settings_menu.add(self.item_set_key)

        self.menu = [
            self.item_updated,
            rumps.separator,
            rumps.MenuItem("Refresh", callback=self.on_refresh),
            settings_menu,
            rumps.separator,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ]

        if not _get_api_key():
            self._set_bar("⚠️", "Open Settings → Set API Key… to get started")
        else:
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

    # ── Menu bar title ────────────────────────────────────────────────────────

    def _set_bar(self, plain_title: str, updated_msg: str | None = None) -> None:
        """Set the menu bar to a plain text title (used for errors / loading)."""
        if hasattr(self, '_nsapp'):
            self._nsapp.nsstatusitem.button().setTitle_(plain_title)
        if updated_msg:
            self.item_updated.title = updated_msg

    def _set_bar_batteries(
        self,
        session_frac: float | None, session_reset: str, session_tooltip: str,
        weekly_frac:  float | None, weekly_reset:  str, weekly_tooltip:  str,
    ) -> None:
        """Render both battery icons with reset labels drawn inside them."""
        label_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(11),
            NSForegroundColorAttributeName: NSColor.labelColor(),
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
        if _get_api_key():
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def on_refresh(self, _):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        try:
            now       = datetime.now(timezone.utc)
            today     = now.date()
            yesterday = today - timedelta(days=1)
            monday    = today - timedelta(days=today.weekday())

            reset_date = self.settings.get("reset_date")
            if reset_date:
                rd     = datetime.fromisoformat(reset_date).replace(tzinfo=timezone.utc).date()
                monday = max(monday, rd)

            with ThreadPoolExecutor(max_workers=2) as pool:
                f_session = pool.submit(_safe_cost_fetch, yesterday, today, today)
                f_weekly  = pool.submit(_safe_cost_fetch, monday,    today, today)

            cost_session = f_session.result()
            cost_weekly  = f_weekly.result()

            args = (now, cost_session, cost_weekly)
            NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: self._render(*args))

        except Exception:
            logging.error("Fetch failed:\n%s", traceback.format_exc())
            msg = traceback.format_exc().splitlines()[-1]
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: self._set_bar("⚠️", f"Error: {msg[:120]}")
            )

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, now, cost_session, cost_weekly):
        today = now.date()

        midnight_utc   = datetime(today.year, today.month, today.day,
                                  tzinfo=timezone.utc) + timedelta(days=1)
        days_to_monday = (7 - today.weekday()) % 7 or 7
        next_monday    = datetime(today.year, today.month, today.day,
                                  tzinfo=timezone.utc) + timedelta(days=days_to_monday)

        s_frac, s_tip = _compute_battery_state(self.settings["session_budget"], cost_session)
        w_frac, w_tip = _compute_battery_state(self.settings["weekly_budget"],  cost_weekly)

        self._set_bar_batteries(
            s_frac, _fmt_reset(midnight_utc - now), s_tip,
            w_frac, _fmt_reset(next_monday   - now), w_tip,
        )

        self.item_updated.title = (
            f"Last updated: {now.astimezone().strftime('%I:%M:%S %p')}"
            f"  (cost data reflects yesterday)"
        )

    # ── Settings callbacks ────────────────────────────────────────────────────

    def on_set_api_key(self, _):
        existing = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
        hint = f"Current: {existing[:16]}…" if existing else "No key stored yet."
        resp = rumps.Window(
            message=f"Paste your Anthropic API key (sk-ant-…)\n{hint}",
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


if __name__ == "__main__":
    ClaudeUsageApp().run()
