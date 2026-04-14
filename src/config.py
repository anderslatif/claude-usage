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
_VALID_INTERVALS     = {s for s, _ in POLL_INTERVAL_OPTIONS}
_defaults            = NSUserDefaults.standardUserDefaults()


def load_poll_interval() -> int:
    saved = _defaults.integerForKey_(_POLL_INTERVAL_KEY)
    return saved if saved in _VALID_INTERVALS else POLL_INTERVAL


def save_poll_interval(seconds: int) -> None:
    _defaults.setInteger_forKey_(seconds, _POLL_INTERVAL_KEY)

