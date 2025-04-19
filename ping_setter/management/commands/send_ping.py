import json5
import os
import logging
import requests
import discord
import asyncio
from discord.ext import commands
from discord import app_commands
from django.core.management.base import BaseCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone
from .logging_config import setup_logging

# Absolute path to config.jsonc inside the container
CONFIG_PATH = "/opt/ping_setter_hll/config.jsonc"

# Logging setup
logger = setup_logging()

# Load config from config.jsonc
def load_config():
    """
    Try loading config from several known locations, falling back to defaults.
    """
    paths = [
        CONFIG_PATH,
        os.path.join(os.getcwd(), "config.jsonc"),
    ]
    for p in paths:
        try:
            with open(p, "r") as f:
                logger.info(f"Loaded config from: {p}")
                return json5.load(f)
        except FileNotFoundError:
            logger.debug(f"Config not found at: {p}")
    logger.error("Config file not found in any of the expected paths; using default settings.")
    return {
        "DISCORD_TOKEN": "",
        "CHANNEL_ID": None,
        "API_BASE_URL": "",
        "API_BEARER_TOKEN": "",
        "TIMEZONE": "Australia/Sydney",
        "SCHEDULED_JOB_1_TIME": "0009",
        "SCHEDULED_JOB_1_PING": 500,
        "SCHEDULED_JOB_2_TIME": "1500",
        "SCHEDULED_JOB_2_PING": 320,
        "LOG_DIR": "/opt/ping_setter_hll/logs"
    }
# Load configuration
config = load_config()

# Required settings
DISCORD_TOKEN    = config.get("DISCORD_TOKEN")
CHANNEL_ID       = config.get("CHANNEL_ID")
API_BASE_URL     = config.get("API_BASE_URL")
API_BEARER_TOKEN = config.get("API_BEARER_TOKEN")

if not DISCORD_TOKEN or CHANNEL_ID is None or not API_BASE_URL or not API_BEARER_TOKEN:
    raise ValueError("Essential configuration missing in config.jsonc")

# Prepare headers for API calls
HEADERS = {
    "Authorization": f"Bearer {API_BEARER_TOKEN}",
    "Content-Type": "application/json"
}

# Scheduler timezone
tz_name = config.get("TIMEZONE", "Australia/Sydney")
try:
    tz = timezone(tz_name)
except Exception:
    logger.warning(f"Invalid timezone '{tz_name}', defaulting to Australia/Sydney")
    tz = timezone("Australia/Sydney")
scheduler = AsyncIOScheduler(timezone=tz)

# Discord bot setup (only slash commands)
intents = discord.Intents.default()
intents.message_content = False
client = commands.Bot(command_prefix="!", intents=intents)
tree = client.tree

# --- API helper functions ---
def get_max_ping_autokick() -> int | None:
    try:
        r = requests.get(f"{API_BASE_URL}/api/get_server_settings", headers=HEADERS)
        r.raise_for_status()
        return r.json().get("result", {}).get("max_ping_autokick")
    except Exception as e:
        logger.error(f"Failed to fetch max ping: {e}")
        return None

def set_max_ping_autokick(ping: int) -> bool:
    try:
        r = requests.post(
            f"{API_BASE_URL}/api/set_max_ping_autokick",
            headers=HEADERS,
            json={"max_ms": ping}
        )
        return r.ok
    except Exception as e:
        logger.error(f"Failed to set max ping: {e}")
        return False

def get_recent_bans(limit=5):
    try:
        r = requests.get(f"{API_BASE_URL}/api/get_bans", headers=HEADERS)
        r.raise_for_status()
        bans = r.json().get("result", [])
        return list(reversed([b for b in bans if b.get("type") == "temp" and b.get("player_id")])[-limit:])
    except Exception as e:
        logger.error(f"Failed to fetch bans: {e}")
        return []

def get_player_name(player_id: str) -> str:
    try:
        r = requests.get(
            f"{API_BASE_URL}/api/get_player_profile",
            headers=HEADERS,
            params={"player_id": player_id}
        )
        r.raise_for_status()
        names = r.json().get("result", {}).get("names", [])
        if names:
            names.sort(key=lambda n: n.get("last_seen", ""), reverse=True)
            return names[0].get("name", "Unknown")
        return "Unknown"
    except Exception as e:
        logger.error(f"Failed to fetch player profile: {e}")
        return "Unknown"

def unban_player(player_id: str) -> bool:
    try:
        r = requests.post(f"{API_BASE_URL}/api/unban", headers=HEADERS, json={"player_id": player_id})
        return r.ok
    except Exception as e:
        logger.error(f"Failed to unban player: {e}")
        return False

# --- Scheduled job function ---
async def scheduled_ping_job(job_id: str, time_str: str, ping: int):
    h, m = time_str[:2], time_str[2:]
    if set_max_ping_autokick(ping):
        logger.info(f"[{job_id}] Set max ping to {ping}ms at {h}:{m}")
        ch = client.get_channel(CHANNEL_ID)
        if ch:
            await ch.send(f"🔄 [{job_id}] Max ping autokick set to `{ping}` ms (Scheduled {h}:{m})")
    else:
        logger.warning(f"[{job_id}] Failed to set max ping {ping}")

# --- Reschedule helper ---
def reschedule_job(job_id: str, time_str: str, ping: int):
    try:
        hour, minute = int(time_str[:2]), int(time_str[2:])
        scheduler.add_job(
            scheduled_ping_job,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=job_id,
            args=[job_id, time_str, ping],
            replace_existing=True
        )

        job_config_map = {
            "set_ping_job_1": ("SCHEDULED_JOB_1_TIME", "SCHEDULED_JOB_1_PING"),
            "set_ping_job_2": ("SCHEDULED_JOB_2_TIME", "SCHEDULED_JOB_2_PING")
        }
        job_time_key, job_ping_key = job_config_map.get(job_id, (None, None))
        if job_time_key and job_ping_key:
            config[job_time_key] = time_str
            config[job_ping_key] = ping
            with open(CONFIG_PATH, "w") as f:
                json5.dump(config, f, indent=4)
            logger.info(f"[{job_id}] Rescheduled to {time_str} with ping {ping}")
        else:
            logger.warning(f"Job ID '{job_id}' is not recognized for config update")

    except Exception as e:
        logger.error(f"Failed to reschedule {job_id}: {e}")

# --- Slash commands ---
@tree.command(name="curping", description="Show current max ping autokick")
async def curping(interaction: discord.Interaction):
    logger.info(f"[/curping] Requested by {interaction.user} (ID: {interaction.user.id})")
    ping = get_max_ping_autokick()
    if ping is not None:
        await interaction.response.send_message(f"📡 Current max ping autokick is `{ping}` ms.")
    else:
        await interaction.response.send_message("⚠️ Could not fetch current ping.")

@tree.command(name="setping", description="Set max ping autokick")
@app_commands.describe(ping="Ping in ms")
async def setping(interaction: discord.Interaction, ping: int):
    logger.info(f"[/setping] Requested by {interaction.user} (ID: {interaction.user.id}) with ping {ping}")
    if ping <= 0 or ping > 10000:
        return await interaction.response.send_message("⚠️ Ping must be between 1 and 10000 ms.")
    if set_max_ping_autokick(ping):
        await interaction.response.send_message(f"✅ Set max ping autokick to `{ping}` ms.")
    else:
        await interaction.response.send_message("❌ Failed to set ping.")

@tree.command(name="curscheduledtime", description="Show scheduled jobs and pings")
async def curscheduledtime(interaction: discord.Interaction):
    logger.info(f"[/curscheduledtime] Requested by {interaction.user} (ID: {interaction.user.id})")
    t1, p1 = config.get("SCHEDULED_JOB_1_TIME"), config.get("SCHEDULED_JOB_1_PING")
    t2, p2 = config.get("SCHEDULED_JOB_2_TIME"), config.get("SCHEDULED_JOB_2_PING")
    msg = (f"🕒 Job 1: {t1[:2]}:{t1[2:]} @ {p1}ms\n"
           f"🕒 Job 2: {t2[:2]}:{t2[2:]} @ {p2}ms")
    await interaction.response.send_message(msg)

@tree.command(name="setscheduledtime", description="Set scheduled job time and ping")
@app_commands.describe(job="Job number (1 or 2)", time="Time HHMM", ping="Ping in ms")
async def setscheduledtime(interaction: discord.Interaction, job: int, time: str, ping: int):
    logger.info(f"[/setscheduledtime] Requested by {interaction.user} (ID: {interaction.user.id}) - Job {job}, Time {time}, Ping {ping}")
    if not time.isdigit() or len(time) != 4:
        return await interaction.response.send_message("⚠️ Invalid time format. Use HHMM.")
    hour, minute = int(time[:2]), int(time[2:])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return await interaction.response.send_message("⚠️ Invalid time value.")
    if job == 1:
        reschedule_job("set_ping_job_1", time, ping)
        await interaction.response.send_message(f"✅ Job 1 rescheduled to `{hour:02d}:{minute:02d}` @ {ping}ms.")
    elif job == 2:
        reschedule_job("set_ping_job_2", time, ping)
        await interaction.response.send_message(f"✅ Job 2 rescheduled to `{hour:02d}:{minute:02d}` @ {ping}ms.")
    else:
        await interaction.response.send_message("⚠️ Invalid job number (1 or 2).")

@tree.command(name="bans", description="Show last 5 temp bans")
async def bans(interaction: discord.Interaction):
    logger.info(f"[/bans] Requested by {interaction.user} (ID: {interaction.user.id})")
    data = get_recent_bans()
    if not data:
        return await interaction.response.send_message("⚠️ No bans found.")
    lines = [f"`{i+1}` - {get_player_name(b['player_id'])} (ID: `{b['player_id']}`)" for i,b in enumerate(data)]
    await interaction.response.send_message("**Last 5 Bans:**\n" + "\n".join(lines))

@tree.command(name="unban", description="Unban player by ban index")
@app_commands.describe(index="1-5")
async def unban(interaction: discord.Interaction, index: int):
    logger.info(f"[/unban] Requested by {interaction.user} (ID: {interaction.user.id}) - Index {index}")
    data = get_recent_bans()
    if not (1 <= index <= len(data)):
        return await interaction.response.send_message("⚠️ Invalid ban index.")
    pid = data[index-1]["player_id"]
    if unban_player(pid):
        await interaction.response.send_message(f"✅ Unbanned player ID `{pid}`.")
    else:
        await interaction.response.send_message("❌ Unban failed.")

@tree.command(name="online", description="Check if bot and API are running")
async def online(interaction: discord.Interaction):
    logger.info(f"[/online] Requested by {interaction.user} (ID: {interaction.user.id})")
    ping = get_max_ping_autokick()
    if ping is not None:
        await interaction.response.send_message(f"🟢 Bot and API are online! Current max ping autokick: `{ping}` ms.")
    else:
        await interaction.response.send_message("🟢 Bot is online, but failed to reach API.")

@tree.command(name="help", description="Show this help message")
async def help_command(interaction: discord.Interaction):
    logger.info(f"[/help] Requested by {interaction.user} (ID: {interaction.user.id})")
    msg = (
        "📘 **Getting Started:**\n"
        "Welcome to the HLL command tool!\n\n"
        "📜 **List of Commands:**\n"
        "/bans - Show recent bans\n"
        "/unban - Unban a player from recent bans\n"
        "/curping - Show current max ping autokick value\n"
        "/setping - Set max ping autokick value (in ms)\n"
        "/curscheduledtime - Show current scheduled job times and ping values\n"
        "/setscheduledtime <job> <time> <ping> - Set scheduled job time and ping\n"
        "/online - Check if bot and API are running\n"
        "/help - Show this help message"
    )
    await interaction.response.send_message(msg)

# --- Bot startup ---
@client.event
async def on_ready():
    for jid in ("set_ping_job_1", "set_ping_job_2"):
        t = config.get(f"{jid.upper()}_TIME")
        p = config.get(f"{jid.upper()}_PING")
        if t and p is not None:
            reschedule_job(jid, t, p)
    scheduler.start()
    ch = client.get_channel(CHANNEL_ID)
    if ch:
        await ch.send("🟢 Bot is online!")

# Django management command
class Command(BaseCommand):
    help = "Starts the Discord Ping Bot"

    def handle(self, *args, **options):
        @client.event
        async def on_ready():
            try:
                await tree.sync()
                logger.info(f"Synced slash commands as {client.user} (ID: {client.user.id})")

                # Schedule the jobs on bot startup
                reschedule_job("set_ping_job_1", config.get("SCHEDULED_JOB_1_TIME"), config.get("SCHEDULED_JOB_1_PING"))
                reschedule_job("set_ping_job_2", config.get("SCHEDULED_JOB_2_TIME"), config.get("SCHEDULED_JOB_2_PING"))

                # Start the scheduler
                scheduler.start()
                logger.info("Scheduler started and jobs scheduled.")
            except Exception as e:
                logger.error(f"Failed during on_ready: {e}")

        client.run(DISCORD_TOKEN)
