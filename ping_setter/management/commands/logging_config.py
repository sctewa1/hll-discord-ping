import os
import json5  # supports JSON with comments (JSONC)
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from pytz import timezone
from datetime import datetime

# Read from ENV, fallback to /app/config.jsonc inside the image
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.jsonc")

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json5.load(f)
    except FileNotFoundError:
        print("[logging_config] Config file not found. Using default settings.")
        return {"LOG_DIR": "/logs", "TIMEZONE": "UTC"}

config = load_config()

LOG_DIR = config.get("LOG_DIR", "/logs")
TIMEZONE = config.get("TIMEZONE", "UTC")

class TZFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None, tzname="UTC"):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.local_tz = timezone(tzname)
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=self.local_tz)
        return dt.strftime(datefmt) if datefmt else dt.isoformat()

def setup_logging():
    # Use the root logger so all modules inherit it
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers on reloads
    if logger.handlers:
        return logger

    # Ensure the log directory exists (for the file handler)
    os.makedirs(LOG_DIR, exist_ok=True)
    log_filename = os.path.join(LOG_DIR, "discord_bot.log")
    print(f"[logging_config] Writing logs to: {log_filename}")

    fmt = "[%(asctime)s] [%(levelname)-7s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Console handler -> shows up in `fly logs`
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(TZFormatter(fmt, datefmt, tzname=TIMEZONE))
    logger.addHandler(sh)

    # Rotating file handler (optional but you already use it)
    fh = TimedRotatingFileHandler(
        log_filename, when="midnight", interval=1, backupCount=50, utc=False
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(TZFormatter(fmt, datefmt, tzname=TIMEZONE))
    logger.addHandler(fh)

    # Don’t let child loggers double-print to parent if they add their own handlers
    logger.propagate = False

    return logger