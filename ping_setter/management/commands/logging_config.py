import os
import logging
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv
from pytz import timezone
from datetime import datetime, time as dt_time

load_dotenv()

# Read LOG_DIR from .env, defaulting to /logs if not set
LOG_DIR = os.getenv("LOG_DIR", "/logs")
TIMEZONE = os.getenv("TIMEZONE", "UTC")

def setup_logging():
    # ensure the directory exists
    os.makedirs(LOG_DIR, exist_ok=True)

    log_filename = os.path.join(LOG_DIR, "discord_bot.log")
    # debug print to confirm path on container startup
    print(f"[logging_config] Writing logs to: {log_filename}")

    # handler rotates daily at midnight, backupCount=50
    handler = TimedRotatingFileHandler(
        log_filename,
        when="midnight",
        interval=1,
        backupCount=50,
        utc=False
    )
    # custom timestamp in your timezone with ms precision
    local_tz = timezone(TIMEZONE)
    class TZFormatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            dt = datetime.fromtimestamp(record.created, tz=local_tz)
            if datefmt:
                return dt.strftime(datefmt)
            return dt.isoformat()
    fmt = '%(asctime)s - %(levelname)s - %(message)s'
    formatter = TZFormatter(fmt, datefmt='%Y-%m-%d %H:%M.%f')
    formatter.default_msec_format = '%s.%03d'
    handler.setFormatter(formatter)

    logger = logging.getLogger("discord_bot")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
