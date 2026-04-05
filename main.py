#!/usr/bin/env python3
"""
Claude Usage Menu Bar App
Displays claude.ai 5-hour session and 7-day weekly utilization in the macOS menu bar.

Reads the OAuth token that Claude Code already stores in the macOS Keychain under
"Claude Code-credentials" - no separate API key required.
"""

import logging
import argparse
import threading
import traceback
from datetime import datetime, timezone


from AppKit import (
    NSImage, NSColor, NSBezierPath, NSFont,
    NSMutableAttributedString, NSAttributedString,
    NSTextAttachment, NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
)
from Foundation import NSMakeRect, NSOperationQueue


from src.config import POLL_INTERVAL
from src.logging import LOG_FILE
from src.usage_fetch import fetch_utilization
from src.OAuth_credentials import is_logged_in
from src.format_util import format_reset_window
from src.draw_icon import battery_attachment


import rumps
from dotenv import load_dotenv

load_dotenv()






# ── Main app ──────────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)

        self.item_updated      = rumps.MenuItem("Last updated: -")
        self.item_session_util = rumps.MenuItem("5h session: -")
        self.item_weekly_util  = rumps.MenuItem("7d weekly: -")

        self.menu = [
            self.item_updated,
            rumps.separator,
            self.item_session_util,
            rumps.separator,
            self.item_weekly_util,
            rumps.separator,
            rumps.MenuItem("Refresh ↻", callback=self.on_refresh),
            rumps.separator,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ]

        if not is_logged_in():
            self.set_bar("⚠️", "Claude Code not logged in - run `claude` in terminal first")
        else:
            threading.Thread(target=self.fetch_and_update, daemon=True).start()

    # ── Menu bar title ────────────────────────────────────────────────────────

    def set_bar(self, plain_title: str, updated_msg: str | None = None) -> None:
        if hasattr(self, '_nsapp'):
            self._nsapp.nsstatusitem.button().setTitle_(plain_title)
        if updated_msg:
            self.item_updated.title = updated_msg

    def set_bar_batteries(
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
        bar.appendAttributedString_(battery_attachment(session_frac, session_reset))
        bar.appendAttributedString_(_as("  Weekly "))
        bar.appendAttributedString_(battery_attachment(weekly_frac, weekly_reset))
        si = self._nsapp.nsstatusitem
        si.button().setAttributedTitle_(bar)
        si.button().setToolTip_(
            f"Session: {session_tooltip}\nWeekly: {weekly_tooltip}"
        )

    # ── Refresh ───────────────────────────────────────────────────────────────

    @rumps.timer(POLL_INTERVAL)
    def _auto_refresh(self, _):
        if is_logged_in():
            threading.Thread(target=self.fetch_and_update, daemon=True).start()

    def on_refresh(self, _):
        threading.Thread(target=self.fetch_and_update, daemon=True).start()

    def fetch_and_update(self):
        try:
            data = fetch_utilization()
            NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: self._render(data))
        except Exception:
            logging.error("Fetch failed:\n%s", traceback.format_exc())
            msg = traceback.format_exc().splitlines()[-1]
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: self.set_bar("⚠️", f"Error: {msg[:120]}")
            )

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, data: dict):
        now = datetime.now(timezone.utc)

        s = data["five_hour"]
        w = data["seven_day"]

        s_util     = s["utilization"]          # 0.0–1.0
        w_util     = w["utilization"]
        s_reset    = format_reset_window(s["reset_at"])
        w_reset    = format_reset_window(w["reset_at"])
        s_tooltip  = f"{s_util*100:.0f}% used  ·  resets in {s_reset}"
        w_tooltip  = f"{w_util*100:.0f}% used  ·  resets in {w_reset}"

        self.set_bar_batteries(s_util, s_reset, s_tooltip, w_util, w_reset, w_tooltip)

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
        logging.info("Debug mode - running in foreground (logs: %s)", LOG_FILE)

    ClaudeUsageApp().run()
