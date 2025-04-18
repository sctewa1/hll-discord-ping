import os
import logging
import requests
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv, set_key
from django.core.management.base import BaseCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone
from datetime import datetime
from .logging_config import setup_logging

# Load environment variables
load_dotenv()

# Discord bot setup
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)
tree = client.tree

# Scheduler and headers
scheduler = AsyncIOScheduler()
HEADERS = {}

# Globals initialized later
logger = None
BOT_TOKEN = None
CHANNEL_ID = None
API_BASE_URL = None
API_BEARER_TOKEN = None
tz_name = None

# API helper functions
def get_max_ping_autokick() -> int | None:
    try:
        resp = requests.get(f"{API_BASE_URL}/api/get_server_settings", headers=HEADERS)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("max_ping_autokick")
    except Exception as e:
        logger.error(f"Failed to fetch max ping: {e}")
        return None

def set_max_ping_autokick(ping: int) -> bool:
    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/set_max_ping_autokick",
            headers=HEADERS,
            json={"max_ms": ping}
        )
        return resp.ok
    except Exception as e:
        logger.error(f"Failed to set max ping: {e}")
        return False

def get_recent_bans(limit=5):
    try:
        resp = requests.get(f"{API_BASE_URL}/api/get_bans", headers=HEADERS)
        resp.raise_for_status()
        bans = resp.json().get("result", [])
        filtered = [b for b in bans if b.get("type") == "temp" and b.get("player_id")]
        return list(reversed(filtered[-limit:]))
    except Exception as e:
        logger.error(f"Failed to fetch bans: {e}")
        return []

def get_player_name(player_id: str) -> str:
    try:
        resp = requests.get(
            f"{API_BASE_URL}/api/get_player_profile",
            headers=HEADERS,
            params={"player_id": player_id}
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
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
        resp = requests.post(f"{API_BASE_URL}/api/unban", headers=HEADERS, json={"player_id": player_id})
        return resp.ok
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
    except Exception:
        pass

    scheduler.add_job(job, trigger=trigger, id=job_id)

@tree.command(name="setscheduledtime", description="Set the scheduled job times and ping values")
@app_commands.describe(job="Job number (1 or 2)", time="New job time (hh:mm)", ping="New ping value in ms")
async def set_scheduled_time(interaction: discord.Interaction, job: int, time: str, ping: int):
    username = interaction.user.name

    try:
        hour, minute = map(int, time.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            await interaction.response.send_message("⚠️ Invalid time format. Please use hh:mm (24hr) format.")
            return
    except ValueError:
        await interaction.response.send_message("⚠️ Invalid time format. Please use hh:mm (24hr) format.")
        return

    try:
        if job == 1:
            set_key(".env", "SCHEDULED_JOB_1_TIME", time)
            set_key(".env", "SCHEDULED_JOB_1_PING", str(ping))
            reschedule_job("set_ping_job_1", time, ping)
            logger.info(f"User `{username}` updated Job 1: Time set to `{time}` and Ping set to `{ping}` ms.")
            await interaction.response.send_message(f"✅ Job 1 rescheduled to `{time}` with ping `{ping}` ms.")
        elif job == 2:
            set_key(".env", "SCHEDULED_JOB_2_TIME", time)
            set_key(".env", "SCHEDULED_JOB_2_PING", str(ping))
            reschedule_job("set_ping_job_2", time, ping)
            logger.info(f"User `{username}` updated Job 2: Time set to `{time}` and Ping set to `{ping}` ms.")
            await interaction.response.send_message(f"✅ Job 2 rescheduled to `{time}` with ping `{ping}` ms.")
        else:
            await interaction.response.send_message("⚠️ Invalid job number. Please choose 1 or 2.")
    except Exception as e:
        logger.error(f"Error updating schedule for Job {job}: {e}")
        await interaction.response.send_message(f"❌ Error updating schedule: {e}")

class Command(BaseCommand):
    help = 'Starts the Discord ping setter bot'

    def handle(self, *args, **kwargs):
        global logger, BOT_TOKEN, CHANNEL_ID, API_BASE_URL, API_BEARER_TOKEN, tz_name, HEADERS

        logger = setup_logging()
        logger.info('Starting bot setup')

        BOT_TOKEN = os.getenv('DISCORD_TOKEN')
        CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))
        API_BASE_URL = os.getenv('API_BASE_URL')
        API_BEARER_TOKEN = os.getenv('API_BEARER_TOKEN')
        tz_name = os.getenv('TIMEZONE', 'Australia/Sydney')

        HEADERS = {"Authorization": f"Bearer {API_BEARER_TOKEN}", "Content-Type": "application/json"}

        job_1_time = os.getenv('SCHEDULED_JOB_1_TIME', '00:01')
        job_2_time = os.getenv('SCHEDULED_JOB_2_TIME', '15:00')
        ping_1 = int(os.getenv('SCHEDULED_JOB_1_PING', 500))
        ping_2 = int(os.getenv('SCHEDULED_JOB_2_PING', 320))

        scheduler.configure(timezone=timezone(tz_name))
        scheduler.start()

        async def start_bot():
            @client.event
            async def on_ready():
                await tree.sync()
                logger.info(f"Bot is ready. Logged in as {client.user}")
                reschedule_job("set_ping_job_1", job_1_time, ping_1)
                reschedule_job("set_ping_job_2", job_2_time, ping_2)

            await client.start(BOT_TOKEN)

        import asyncio
        asyncio.run(start_bot())
