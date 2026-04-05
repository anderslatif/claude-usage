import logging
from pathlib import Path

LOG_FILE = Path.home() / "Library" / "Logs" / "claude_usage.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
    ],
)
