import logging
import threading
import traceback
from datetime import datetime, timezone

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
from Foundation import NSOperationQueue

from .config import (
    POLL_INTERVAL_OPTIONS,
    load_poll_interval, save_poll_interval,
    load_show_indicators, save_show_indicators,
)
from .usage_fetch import fetch_utilization
from .OAuth_credentials import is_logged_in
from .format_util import format_reset_window
from .menu_bar import set_bar_text, set_bar_orange_text, set_bar_batteries


class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self._poll_interval = load_poll_interval()
        self._poll_event    = threading.Event()

        self._show_session, self._show_weekly = load_show_indicators()
        self._last_render: dict | None = None

        self.item_updated      = rumps.MenuItem("Last updated: -")
        self.item_session_util = rumps.MenuItem("5h session: -")
        self.item_weekly_util  = rumps.MenuItem("7d weekly: -")

        self._interval_items = {}
        interval_submenu = rumps.MenuItem("Poll interval")
        for seconds, label in POLL_INTERVAL_OPTIONS:
            item = rumps.MenuItem(label, callback=self._make_interval_callback(seconds))
            item.state = 1 if seconds == self._poll_interval else 0
            self._interval_items[seconds] = item
            interval_submenu.add(item)

        self.item_show_session = rumps.MenuItem("Session", callback=self._toggle_session)
        self.item_show_session.state = int(self._show_session)
        self.item_show_weekly  = rumps.MenuItem("Weekly",  callback=self._toggle_weekly)
        self.item_show_weekly.state  = int(self._show_weekly)
        show_submenu = rumps.MenuItem("Show in menu bar")
        show_submenu.add(self.item_show_session)
        show_submenu.add(self.item_show_weekly)

        self.menu = [
            self.item_session_util,
            rumps.separator,
            self.item_weekly_util,
            rumps.separator,
            self.item_updated,
            rumps.MenuItem("Refresh ↻", callback=self.on_refresh),
            interval_submenu,
            show_submenu,
            rumps.separator,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ]

        if not is_logged_in():
            self._set_error("Claude Code not logged in - run `claude` in terminal first")
        else:
            threading.Thread(target=self.fetch_and_update, daemon=True).start()
            threading.Thread(target=self._poll_loop, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def _nsstatusitem(self):
        return self._nsapp.nsstatusitem

    def _set_error(self, message: str) -> None:
        # _nsapp may not exist yet during __init__; guard accordingly
        if hasattr(self, "_nsapp"):
            set_bar_text(self._nsstatusitem, "⚠️")
        self.item_updated.title = message

    # ── Visibility toggles ────────────────────────────────────────────────────

    def _toggle_session(self, _):
        self._show_session = not self._show_session
        self.item_show_session.state = int(self._show_session)
        save_show_indicators(self._show_session, self._show_weekly)
        self._rerender()

    def _toggle_weekly(self, _):
        self._show_weekly = not self._show_weekly
        self.item_show_weekly.state = int(self._show_weekly)
        save_show_indicators(self._show_session, self._show_weekly)
        self._rerender()

    def _rerender(self):
        if self._last_render is not None:
            self._render(self._last_render)
        elif not self._show_session and not self._show_weekly:
            set_bar_orange_text(self._nsstatusitem, "claude-usage")

    # ── Interval ──────────────────────────────────────────────────────────────

    def _make_interval_callback(self, seconds: int):
        def callback(_):
            for s, item in self._interval_items.items():
                item.state = 1 if s == seconds else 0
            self._poll_interval = seconds
            save_poll_interval(seconds)
            self._poll_event.set()  # wake the poll loop so it restarts immediately
        return callback

    def _poll_loop(self):
        while True:
            self._poll_event.wait(timeout=self._poll_interval)
            self._poll_event.clear()
            if is_logged_in():
                threading.Thread(target=self.fetch_and_update, daemon=True).start()

    # ── Refresh ───────────────────────────────────────────────────────────────

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
        self._last_render = data
        now = datetime.now(timezone.utc)

        s = data["five_hour"]
        w = data["seven_day"]

        s_util    = s["utilization"]
        w_util    = w["utilization"]
        s_reset   = format_reset_window(s["reset_at"])
        w_reset   = format_reset_window(w["reset_at"])
        s_tooltip = f"{s_util*100:.0f}% used  ·  resets in {s_reset}"
        w_tooltip = f"{w_util*100:.0f}% used  ·  resets in {w_reset}"

        if self._show_session or self._show_weekly:
            set_bar_batteries(
                self._nsstatusitem,
                s_util, s_reset, s_tooltip,
                w_util, w_reset, w_tooltip,
                show_session=self._show_session,
                show_weekly=self._show_weekly,
            )
        else:
            set_bar_orange_text(self._nsstatusitem, "claude-usage")

        local_now = now.astimezone()
        self.item_updated.title      = f"Last updated: {local_now.strftime('%I:%M:%S %p')}"
        self.item_session_util.title = f"5h session:  {s_util*100:.0f}% used  ·  resets in {s_reset}"
        self.item_weekly_util.title  = f"7d weekly:   {w_util*100:.0f}% used  ·  resets in {w_reset}"
