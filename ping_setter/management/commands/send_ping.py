import os
import logging
import requests
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from django.core.management.base import BaseCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from pytz import timezone
from datetime import datetime
from dotenv import set_key
from .logging_config import setup_logging

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))
API_BASE_URL = os.getenv('API_BASE_URL')
API_BEARER_TOKEN = os.getenv('API_BEARER_TOKEN')

# Retrieve timezone from .env file
tz_name = os.getenv('TIMEZONE', 'Australia/Sydney')

# Initialize scheduler with the specified timezone
scheduler = AsyncIOScheduler(timezone=timezone(tz_name))

# Retrieve job times and ping values from .env file
job_1_time = os.getenv('SCHEDULED_JOB_1_TIME', '00:01')
job_2_time = os.getenv('SCHEDULED_JOB_2_TIME', '15:00')
ping_1 = int(os.getenv('SCHEDULED_JOB_1_PING', 500))
ping_2 = int(os.getenv('SCHEDULED_JOB_2_PING', 320))

# Parse job times
job_1_hour, job_1_minute = map(int, job_1_time.strip('"').split(":"))
job_2_hour, job_2_minute = map(int, job_2_time.strip('"').split(":"))

# Logging
logger = setup_logging()
logger.info('Bot has started')

# Discord bot setup
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)
tree = client.tree

HEADERS = {
    "Authorization": f"Bearer {API_BEARER_TOKEN}",
    "Content-Type": "application/json"
}

# API helper functions
def get_max_ping_autokick() -> int | None:
    try:
        response = requests.get(f"{API_BASE_URL}/api/get_server_settings", headers=HEADERS)
        response.raise_for_status()
        return response.json().get("result", {}).get("max_ping_autokick")
    except Exception as e:
        logger.error(f"Failed to fetch max ping: {e}")
        return None

def set_max_ping_autokick(ping: int) -> bool:
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/set_max_ping_autokick",
            headers=HEADERS,
            json={"max_ms": ping}
        )
        return response.ok
    except Exception as e:
        logger.error(f"Failed to set max ping: {e}")
        return False

def get_recent_bans(limit=5):
    try:
        response = requests.get(f"{API_BASE_URL}/api/get_bans", headers=HEADERS)
        response.raise_for_status()
        bans = response.json().get("result", [])
        filtered = [b for b in bans if b.get("type") == "temp" and b.get("player_id")]
        return list(reversed(filtered[-limit:]))
    except Exception as e:
        logger.error(f"Failed to fetch bans: {e}")
        return []

def get_player_name(player_id: str) -> str:
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/get_player_profile",
            headers=HEADERS,
            params={"player_id": player_id},
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        names = result.get("names", [])

        if names:
            names.sort(key=lambda n: n.get("last_seen", ""), reverse=True)
            return names[0].get("name", "Unknown")
        return "Unknown"
    except Exception as e:
        logger.error(f"Failed to fetch player profile: {e}")
        return "Unknown"

def unban_player(player_id: str) -> bool:
    try:
        response = requests.post(f"{API_BASE_URL}/api/unban", headers=HEADERS, json={"player_id": player_id})
        return response.ok
    except Exception as e:
        logger.error(f"Failed to unban player: {e}")
        return False

def reschedule_job(job_id: str, time_str: str, ping: int):
    hour, minute = map(int, time_str.strip('"').split(":"))
    trigger = CronTrigger(hour=hour, minute=minute, timezone=timezone(tz_name))

    async def job():
        if set_max_ping_autokick(ping):
            logger.info(f"Rescheduled job `{job_id}`: Set max ping to {ping}ms ⏰ ({time_str})")
            channel = client.get_channel(CHANNEL_ID)
            if channel:
                await channel.send(f"🔄 Max ping autokick set to `{ping}` ms (Updated schedule: {time_str})")
        else:
            logger.warning(f"Rescheduled job `{job_id}`: Failed to set max ping to {ping}ms")

    try:
        scheduler.remove_job(job_id)
    except Exception as e:
        logger.warning(f"Could not remove job `{job_id}` before rescheduling: {e}")

    scheduler.add_job(job, trigger=trigger, id=job_id)

async def set_ping_job_1():
    if set_max_ping_autokick(ping_1):
        logger.info(f"Scheduled: Set max ping to {ping_1}ms 🕐 ({job_1_time})")
        channel = client.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(f"🕐 Max ping autokick set to `{ping_1}` ms (Scheduled {job_1_time})")
    else:
        logger.warning(f"Scheduled: Failed to set max ping to {ping_1}ms")

async def set_ping_job_2():
    if set_max_ping_autokick(ping_2):
        logger.info(f"Scheduled: Set max ping to {ping_2}ms 🕒 ({job_2_time})")
        channel = client.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(f"🕒 Max ping autokick set to `{ping_2}` ms (Scheduled {job_2_time})")
    else:
        logger.warning(f"Scheduled: Failed to set max ping to {ping_2}ms")

@client.event
async def on_ready():
    await tree.sync()
    logger.info("🔔 Bot has started and is now online!")
    print("🔔 Bot has started and is now online!")

    if not scheduler.running:
        scheduler.start()

    try:
        scheduler.add_job(set_ping_job_1, CronTrigger(hour=job_1_hour, minute=job_1_minute, timezone=timezone(tz_name)), id="set_ping_job_1")
    except Exception as e:
        logger.warning(f"Could not schedule job 1: {e}")

    try:
        scheduler.add_job(set_ping_job_2, CronTrigger(hour=job_2_hour, minute=job_2_minute, timezone=timezone(tz_name)), id="set_ping_job_2")
    except Exception as e:
        logger.warning(f"Could not schedule job 2: {e}")

    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🟢 Bot is now online and ready to go!")

# The rest of your slash command definitions go here... (unchanged)

class Command(BaseCommand):
    help = "Starts the Discord bot"

    def handle(self, *args, **options):
        client.run(BOT_TOKEN)
