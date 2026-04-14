import logging
from pathlib import Path

LOG_FILE = Path.home() / "Library" / "Logs" / "claude_usage.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
    ],
)

# Suppress verbose debug output from HTTP libraries that would log
# request headers (including Authorization tokens) at DEBUG level.
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
