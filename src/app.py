import logging
import threading
import traceback
from datetime import datetime, timezone

import rumps
from Foundation import NSOperationQueue

from .config import POLL_INTERVAL
from .usage_fetch import fetch_utilization
from .OAuth_credentials import is_logged_in
from .format_util import format_reset_window
from .menu_bar import set_bar_text, set_bar_batteries


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
            self._set_error("Claude Code not logged in - run `claude` in terminal first")
        else:
            threading.Thread(target=self.fetch_and_update, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def _nsstatusitem(self):
        return self._nsapp.nsstatusitem

    def _set_error(self, message: str) -> None:
        # _nsapp may not exist yet during __init__; guard accordingly
        if hasattr(self, "_nsapp"):
            set_bar_text(self._nsstatusitem, "⚠️")
        self.item_updated.title = message

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
                lambda: self._set_error(f"Error: {msg[:120]}")
            )

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, data: dict) -> None:
        now = datetime.now(timezone.utc)

        s = data["five_hour"]
        w = data["seven_day"]

        s_util    = s["utilization"]
        w_util    = w["utilization"]
        s_reset   = format_reset_window(s["reset_at"])
        w_reset   = format_reset_window(w["reset_at"])
        s_tooltip = f"{s_util*100:.0f}% used  ·  resets in {s_reset}"
        w_tooltip = f"{w_util*100:.0f}% used  ·  resets in {w_reset}"

        set_bar_batteries(
            self._nsstatusitem,
            s_util, s_reset, s_tooltip,
            w_util, w_reset, w_tooltip,
        )

        local_now = now.astimezone()
        self.item_updated.title      = f"Last updated: {local_now.strftime('%I:%M:%S %p')}"
        self.item_session_util.title = f"5h session:  {s_util*100:.0f}% used  ·  resets in {s_reset}"
        self.item_weekly_util.title  = f"7d weekly:   {w_util*100:.0f}% used  ·  resets in {w_reset}"
