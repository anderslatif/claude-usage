from Foundation import NSUserDefaults

MESSAGES_URL    = "https://api.anthropic.com/v1/messages"
# Anthropic's OAuth Client ID for Claude Code. Safe to publish.
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
PLATFORM_URL    = "https://platform.claude.com"
POLL_INTERVAL   = 60  # seconds, used as the default

POLL_INTERVAL_OPTIONS = [
    (30,  "30 seconds"),
    (60,  "1 minute"),
    (300, "5 minutes"),
    (900, "15 minutes"),
]

_POLL_INTERVAL_KEY   = "pollInterval"
_SHOW_SESSION_KEY    = "showSession"
_SHOW_WEEKLY_KEY     = "showWeekly"
_VALID_INTERVALS     = {s for s, _ in POLL_INTERVAL_OPTIONS}
_defaults            = NSUserDefaults.standardUserDefaults()


def load_poll_interval() -> int:
    saved = _defaults.integerForKey_(_POLL_INTERVAL_KEY)
    return saved if saved in _VALID_INTERVALS else POLL_INTERVAL


def save_poll_interval(seconds: int) -> None:
    _defaults.setInteger_forKey_(seconds, _POLL_INTERVAL_KEY)


def load_show_indicators() -> tuple[bool, bool]:
    """Returns (show_session, show_weekly). Both default to True on first run."""
    # objectForKey_ returns None when the key has never been set, which lets us
    # distinguish "not yet stored" (default True) from an explicit False.
    def _get(key: str) -> bool:
        val = _defaults.objectForKey_(key)
        return True if val is None else bool(_defaults.boolForKey_(key))
    return _get(_SHOW_SESSION_KEY), _get(_SHOW_WEEKLY_KEY)


def save_show_indicators(show_session: bool, show_weekly: bool) -> None:
    _defaults.setBool_forKey_(show_session, _SHOW_SESSION_KEY)
    _defaults.setBool_forKey_(show_weekly,  _SHOW_WEEKLY_KEY)

