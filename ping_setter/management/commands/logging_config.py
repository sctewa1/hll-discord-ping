import logging
from logging.handlers import TimedRotatingFileHandler
import os
from dotenv import load_dotenv
from pytz import timezone
from datetime import datetime, timedelta

# Load environment variables from .env file
load_dotenv()

# Get timezone from .env
TIMEZONE = os.getenv('TIMEZONE', 'UTC')  # Default to UTC if no timezone is set

def setup_logging():
    log_directory = '/logs'
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)

    log_filename = os.path.join(log_directory, 'discord_bot.log')

    # Get the timezone object
    local_tz = timezone(TIMEZONE)
    
    # Calculate 3 AM in the local timezone
    now = datetime.now(local_tz)
    next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)

    if now > next_run:
        # If it's already past 3 AM, schedule the log rotation for the next day
        next_run += timedelta(days=1)

    # Create a TimedRotatingFileHandler to rotate logs at 3 AM
    handler = TimedRotatingFileHandler(
        log_filename, when="midnight", interval=1, backupCount=50, atTime=next_run.time()
    )

    # Set the suffix for archived log files
    handler.suffix = "%Y-%m-%d"  # Format the archived log file names with the date (e.g., discord_bot-2025-04-17.log)

    # Create a formatter and set it for the handler
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    # Set up the logger
    logger = logging.getLogger('discord_bot')
    logger.setLevel(logging.INFO)  # Set the desired log level
    logger.addHandler(handler)

    return logger
