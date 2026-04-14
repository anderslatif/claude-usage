import os
import sys
import logging
import argparse
import subprocess

from dotenv import load_dotenv

from .logging import LOG_FILE
from .app import ClaudeUsageApp

load_dotenv()

_DAEMON_ENV_VAR = "_CLAUDE_USAGE_DAEMON"


def _relaunch_in_background() -> None:
    """Spawn a fresh background process and exit the foreground parent.

    os.fork() is not safe with AppKit, so we re-exec via subprocess instead.
    The child process detects the env var and skips this step.
    """
    env = os.environ.copy()
    env[_DAEMON_ENV_VAR] = "1"
    subprocess.Popen(
        [sys.executable] + sys.argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description="Claude Usage menu bar app")
    parser.add_argument("--debug", action="store_true",
                        help="Run in foreground with console logging")
    args = parser.parse_args()

    if args.debug:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(console)
        logging.info("Debug mode - running in foreground (logs: %s)", LOG_FILE)
    elif not os.environ.get(_DAEMON_ENV_VAR):
        _relaunch_in_background()

    ClaudeUsageApp().run()
