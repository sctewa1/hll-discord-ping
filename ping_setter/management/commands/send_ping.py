import json5
import os
import logging
import requests
import discord
from discord.ext import commands
from discord import app_commands
from django.core.management.base import BaseCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone
from .logging_config import setup_logging

# Logging setup
logger = setup_logging()

# Load config from config.jsonc
def load_config():
    try:
        with open("config.jsonc", "r") as f:
            return json5.load(f)
    except FileNotFoundError:
        logger.error("Config file not found. Using default settings.")
        return {
            "SCHEDULED_JOB_1_TIME": "0009",
            "SCHEDULED_JOB_1_PING": 500,
            "SCHEDULED_JOB_2_TIME": "1500",
            "SCHEDULED_JOB_2_PING": 320
        }

def save_config(config):
    try:
        with open("config.jsonc", "w") as f:
            json5.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")

# Load config once at top
config = load_config()

DISCORD_TOKEN = config.get("DISCORD_TOKEN")
CHANNEL_ID = config.get("CHANNEL_ID")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is not defined in config.jsonc")
if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID is not defined in config.jsonc")

# Get the timezone from config, with a fallback to "Australia/Sydney" if not found
tz_name = config.get("TIMEZONE", "Australia/Sydney")

try:
    tz = timezone(tz_name)
except Exception as e:
    logger.warning(f"Invalid timezone in config: {tz_name}. Falling back to Australia/Sydney.")
    tz = timezone("Australia/Sydney")
scheduler = AsyncIOScheduler(timezone=tz)

# Discord bot setup
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)
tree = client.tree

# API helper functions (same as in your original code)
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

# Global job function for setting max ping autokick
async def scheduled_ping_job(job_id: str, current_time_str: str, current_ping: int):
    hour_display = current_time_str[:2]
    minute_display = current_time_str[2:]
    if set_max_ping_autokick(current_ping):
        logger.info(f"[{job_id}] Set max ping to {current_ping}ms ⏰ ({hour_display}:{minute_display})")
        channel = client.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(f"🔄 [{job_id}] Max ping autokick set to `{current_ping}` ms (Updated schedule: {hour_display}:{minute_display})")
    else:
        logger.warning(f"[{job_id}] Failed to set max ping to {current_ping}ms")

# Reschedule job function
def reschedule_job(job_id: str, time_str: str, ping: int):
    try:
        hour, minute = map(int, time_str.split(":"))

        # Remove old job if exists
        try:
            scheduler.remove_job(job_id)
        except Exception as e:
            logger.warning(f"[{job_id}] Tried to remove existing job: {e}")

        # Schedule updated job
        scheduler.add_job(
            scheduled_ping_job,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone(tz_name)),
            id=job_id,
            args=[job_id, time_str, ping],
            replace_existing=True
        )

        # Update and persist config
        config[f"{job_id.upper()}_TIME"] = time_str
        config[f"{job_id.upper()}_PING"] = ping
        save_config(config)

        logger.info(f"[{job_id}] Rescheduled to {time_str} with ping {ping}")

    except Exception as e:
        logger.error(f"[{job_id}] Failed to reschedule: {e}")

# Discord bot events and commands
@client.event
async def on_ready():
          
    job_1_time_initial = config["SCHEDULED_JOB_1_TIME"]
    job_2_time_initial = config["SCHEDULED_JOB_2_TIME"]

    # Slice the time strings into hours and minutes directly (since no colon is present)
    job_1_hour, job_1_minute = int(job_1_time_initial[:2]), int(job_1_time_initial[2:])
    job_2_hour, job_2_minute = int(job_2_time_initial[:2]), int(job_2_time_initial[2:])

    ping_1_initial = config["SCHEDULED_JOB_1_PING"]
    ping_2_initial = config["SCHEDULED_JOB_2_PING"]

    # Register async jobs properly using scheduler.add_job
    scheduler.add_job(
        scheduled_ping_job, 
        CronTrigger(hour=job_1_hour, minute=job_1_minute, timezone=timezone(tz_name)),
        id="set_ping_job_1", 
        args=["set_ping_job_1", job_1_time_initial, ping_1_initial]
    )
    
    scheduler.add_job(
        scheduled_ping_job, 
        CronTrigger(hour=job_2_hour, minute=job_2_minute, timezone=timezone(tz_name)),
        id="set_ping_job_2", 
        args=["set_ping_job_2", job_2_time_initial, ping_2_initial]
    )

    # Start the scheduler here
    scheduler.start()

    # Send a message to the designated channel to announce the bot is online
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🟢 Bot is now online and ready to go!")

@tree.command(name="setscheduledtime", description="Set the scheduled job times and ping values (Time in HHMM format)")
@app_commands.describe(job="Job number (1 or 2)", time="New job time (HHMM)", ping="New ping value in ms")
async def set_scheduled_time(interaction: discord.Interaction, job: int, time: str, ping: int):
    username = interaction.user.name

    # Validate time format (HHMM)
    if not time.isdigit() or len(time) != 4:
        await interaction.response.send_message("⚠️ Invalid time format. Please use HHMM (24hr) format (e.g., 0000, 1530, 2359).")
        return

    try:
        hour = int(time[:2])
        minute = int(time[2:])
        if not (0 <= hour < 24 and 0 <= minute < 60):
            await interaction.response.send_message("⚠️ Invalid time value.")
            return
    except ValueError:
        await interaction.response.send_message("⚠️ Invalid time value.")
        return

    try:
        # Call reschedule_job directly; no need to modify config here
        if job == 1:
            reschedule_job("set_ping_job_1", time, ping)
            await interaction.response.send_message(f"✅ Job 1 rescheduled to `{time[:2]}:{time[2:]}` with ping `{ping}` ms.")

        elif job == 2:
            reschedule_job("set_ping_job_2", time, ping)
            await interaction.response.send_message(f"✅ Job 2 rescheduled to `{time[:2]}:{time[2:]}` with ping `{ping}` ms.")

        else:
            await interaction.response.send_message("⚠️ Invalid job number. Please choose 1 or 2.")

    except Exception as e:
        logger.error(f"Error updating schedule for Job {job}: {e}")
        await interaction.response.send_message(f"❌ Error updating schedule: {e}")

# Django command entry point
class Command(BaseCommand):
    help = "Starts the Discord bot"

    def handle(self, *args, **options):
        # No need to change this method since it's already reading from the config
        # and initializing jobs accordingly.
        client.run(DISCORD_TOKEN)
