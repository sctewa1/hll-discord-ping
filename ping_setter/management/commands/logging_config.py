import os
import json5  # supports JSON with comments (JSONC)
import logging
from logging.handlers import TimedRotatingFileHandler
from pytz import timezone
from datetime import datetime

# Absolute path to config.jsonc inside the container
CONFIG_PATH = "/opt/ping_setter_hll/config.jsonc"

# Function to load the config
def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json5.load(f)
    except FileNotFoundError:
        print("[logging_config] Config file not found. Using default settings.")
        return {
            "LOG_DIR": "/logs",
            "TIMEZONE": "UTC"
        }

# Load config
config = load_config()

# Fallback values if not present in config
LOG_DIR = config.get("LOG_DIR", "/logs")
TIMEZONE = config.get("TIMEZONE", "UTC")

def setup_logging():
    # Ensure the log directory exists
    os.makedirs(LOG_DIR, exist_ok=True)

    log_filename = os.path.join(LOG_DIR, "discord_bot.log")
    print(f"[logging_config] Writing logs to: {log_filename}")

    # Rotate logs at midnight
    handler = TimedRotatingFileHandler(
        log_filename,
        when="midnight",
        interval=1,
        backupCount=50,
        utc=False
    )

    # Set up timezone-aware log formatting
    local_tz = timezone(TIMEZONE)

    class TZFormatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            dt = datetime.fromtimestamp(record.created, tz=local_tz)
            return dt.strftime(datefmt) if datefmt else dt.isoformat()

    fmt = '%(asctime)s - %(levelname)s - %(message)s'
    formatter = TZFormatter(fmt, datefmt='%Y-%m-%d %H:%M.%f')
    formatter.default_msec_format = '%s.%03d'
    handler.setFormatter(formatter)

    logger = logging.getLogger("discord_bot")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False

    return logger
