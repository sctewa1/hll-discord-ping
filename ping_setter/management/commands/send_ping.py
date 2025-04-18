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

# Logging
logger = setup_logging()
logger.info('Bot has started')

# Discord bot setup
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()
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
            json={"max_ms": ping}  # 'ping' here is an integer
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
    cleaned_time_str = time_str.strip("'")  # Remove potential single quotes
    hour = int(cleaned_time_str[:2])
    minute = int(cleaned_time_str[2:])
    trigger = CronTrigger(hour=hour, minute=minute, timezone=timezone(tz_name))

    async def scheduled_job(current_time_str, current_ping):
        hour_display = current_time_str[:2]
        minute_display = current_time_str[2:]
        if set_max_ping_autokick(current_ping):
            logger.info(f"Rescheduled job `{job_id}`: Set max ping to {current_ping}ms ⏰ ({hour_display}:{minute_display})")
            channel = client.get_channel(CHANNEL_ID)
            if channel:
                await channel.send(f"🔄 Max ping autokick set to `{current_ping}` ms (Updated schedule: {hour_display}:{minute_display})")
        else:
            logger.warning(f"Rescheduled job `{job_id}`: Failed to set max ping to {current_ping}ms")

    try:
        scheduler.remove_job(job_id)
    except Exception as e:
        logger.warning(f"Attempted to remove job `{job_id}` but it did not exist. Exception: {e}")

    scheduler.add_job(scheduled_job, trigger=trigger, id=job_id, args=[time_str, ping])

# Discord bot events and commands
@client.event
async def on_ready():
    GUILD_ID = 1318878021335388255  # your server ID
    guild = discord.Object(id=GUILD_ID)

    # Sync and capture the list of commands registered
    registered = await tree.sync(guild=guild)
    logger.info(f"🔁 Synced {len(registered)} command(s) to guild {GUILD_ID}")
    print(f"🔁 Synced {len(registered)} command(s) to guild {GUILD_ID}")

    registered_global = await tree.sync()  # no guild argument
    logger.info(f"🔁 Synced {len(registered_global)} global command(s)")
    print(f"🔁 Synced {len(registered_global)} global command(s)")

    logger.info("🔔 Bot has started and is now online!")
    print("🔔 Bot has started and is now online!")
    # Start the scheduler here
    scheduler.start()

    # Send a message to the designated channel to announce the bot is online
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🟢 Bot is now online and ready to go!")

@tree.command(name="curping", description="Show current max ping kick setting")
async def curping(interaction: discord.Interaction):
    ping = get_max_ping_autokick()
    if ping is not None:
        await interaction.response.send_message(f"📡 Current max ping autokick is set to: `{ping}` ms.")
    else:
        await interaction.response.send_message("⚠️ Could not fetch the current max ping value.")

@tree.command(name="setping", description="Set new max ping kick value")
@app_commands.describe(ping="The new max ping value (in ms)")
async def setping(interaction: discord.Interaction, ping: int):
    username = interaction.user.name  # Get the Discord username of the user who triggered the command

    # Log the attempt to set the ping
    logger.info(f"User `{username}` is attempting to set max ping autokick to `{ping}` ms.")

    # Check if the ping value is within a reasonable range (you can adjust this range as needed)
    if ping <= 0 or ping > 10000:
        await interaction.response.send_message("⚠️ Invalid ping value. Please provide a value between 1 and 10,000 ms.")
        logger.warning(f"User `{username}` provided an invalid ping value: `{ping}` ms (must be between 1 and 10,000 ms).")
        return

    # Attempt to set the max ping
    if set_max_ping_autokick(ping):
        await interaction.response.send_message(f"✅ Max ping autokick set to `{ping}` ms.")
        logger.info(f"User `{username}` successfully set max ping autokick to `{ping}` ms.")
    else:
        await interaction.response.send_message("⚠️ Failed to set max ping autokick.")
        logger.error(f"User `{username}` failed to set max ping autokick to `{ping}` ms.")


@tree.command(name="curscheduledtime", description="Show the current scheduled job times and ping values")
async def cur_scheduled_time(interaction: discord.Interaction):
    # Retrieve current job times and ping values from the environment
    job_1_time = os.getenv('SCHEDULED_JOB_1_TIME', '0001')
    job_2_time = os.getenv('SCHEDULED_JOB_2_TIME', '1500')
    ping_1 = os.getenv('SCHEDULED_JOB_1_PING', 500)
    ping_2 = os.getenv('SCHEDULED_JOB_2_PING', 320)

    # Create the message to send
    msg = (
        f"🕒 **Current Scheduled Job Times and Pings:**\n"
        f"1️⃣ Job 1: Time = `{job_1_time}`, Ping = `{ping_1}` ms\n"
        f"2️⃣ Job 2: Time = `{job_2_time}`, Ping = `{ping_2}` ms"
    )

    # Send the message
    await interaction.response.send_message(msg)

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
        if job == 1:
            set_key(".env", "SCHEDULED_JOB_1_TIME", time)  # Write time as string
            set_key(".env", "SCHEDULED_JOB_1_PING", str(ping))  # Write ping as string
            logger.info(f"CALLING RESCHEDULE_JOB FOR JOB 1 - time: '{time}', ping: {ping}")
            reschedule_job("set_ping_job_1", time, ping)
            logger.info(f"User `{username}` updated Job 1: Time set to `{time[:2]}:{time[2:]}` and Ping set to `{ping}` ms.")
            await interaction.response.send_message(f"✅ Job 1 rescheduled to `{time[:2]}:{time[2:]}` with ping `{ping}` ms.")

        elif job == 2:
            set_key(".env", "SCHEDULED_JOB_2_TIME", time)  # Write time as string
            set_key(".env", "SCHEDULED_JOB_2_PING", str(ping))  # Write ping as string
            reschedule_job("set_ping_job_2", time, ping)
            logger.info(f"User `{username}` updated Job 2: Time set to `{time[:2]}:{time[2:]}` and Ping set to `{ping}` ms.")
            await interaction.response.send_message(f"✅ Job 2 rescheduled to `{time[:2]}:{time[2:]}` with ping `{ping}` ms.")

        else:
            await interaction.response.send_message("⚠️ Invalid job number. Please choose 1 or 2.")

    except Exception as e:
        logger.error(f"Error updating schedule for Job {job}: {e}")
        await interaction.response.send_message(f"❌ Error updating schedule: {e}")


@tree.command(name="bans", description="List last 5 bans")
async def bans(interaction: discord.Interaction):
    data = get_recent_bans()
    if not data:
        await interaction.response.send_message("⚠️ No bans found.")
        return

    msg = "**Last 5 Bans:**\n"
    for idx, ban in enumerate(data, 1):
        name = get_player_name(ban.get("player_id"))
        msg += f"`{idx}` - {name} (ID: `{ban.get('player_id')}`)\n"

    await interaction.response.send_message(msg)

@tree.command(name="unban", description="Unban player by ban number from the last /bans list")
@app_commands.describe(index="Ban number from the /bans list (1-5)")
async def unban(interaction: discord.Interaction, index: int):
    data = get_recent_bans()
    if not data:
        await interaction.response.send_message("⚠️ No bans to unban.")
        logger.info(f"User `{interaction.user.name}` attempted to unban a player, but no bans were found.")
        return

    if 1 <= index <= len(data):
        player_id = data[index - 1]["player_id"]
        name = get_player_name(player_id)
        success = unban_player(player_id)

        if success:
            await interaction.response.send_message(f"✅ Unbanned `{name}` (ID: `{player_id}`)")
            logger.info(f"User `{interaction.user.name}` successfully unbanned player `{name}` (ID: `{player_id}`)")
        else:
            await interaction.response.send_message("❌ Failed to unban player.")
            logger.error(f"User `{interaction.user.name}` failed to unban player `{name}` (ID: `{player_id}`).")
    else:
        await interaction.response.send_message("⚠️ Invalid ban index.")
        logger.warning(f"User `{interaction.user.name}` provided an invalid index `{index}` when attempting to unban a player.")

@tree.command(name="help", description="Show help message")
async def help_command(interaction: discord.Interaction):
    msg = (
        "📘 **Getting Started:**\n"
        "Welcome to the HLL command tool!\n\n"
        "📜 **List of Commands:**\n"
        "/bans - Show recent bans\n"
        "/unban - Unban a player from recent bans\n"
        "/curping - Show current max ping kick\n"
        "/setping - Set max ping kick\n"
        "/curscheduledtime - there are 2 jobs to set the ping, see the times\n"
        "/setscheduledtime job=1 time=0001 ping=500 - use this to set the time and ping (HHMM format)\n"
        "/online - Check if bot and API are running\n"
        "/help - Show this help message"
    )
    await interaction.response.send_message(msg)

# Django command entry point
class Command(BaseCommand):
    help = "Starts the Discord bot"

    def handle(self, *args, **options):
        job_1_time_initial = os.getenv('SCHEDULED_JOB_1_TIME', '0001')
        job_2_time_initial = os.getenv('SCHEDULED_JOB_2_TIME', '1500')
        ping_1_initial = int(os.getenv('SCHEDULED_JOB_1_PING', 500))
        ping_2_initial = int(os.getenv('SCHEDULED_JOB_2_PING', 320))

        job_1_hour = int(job_1_time_initial[:2])
        job_1_minute = int(job_1_time_initial[2:])
        job_2_hour = int(job_2_time_initial[:2])
        job_2_minute = int(job_2_time_initial[2:])

        async def set_ping_job_1(current_time_str, current_ping):
            hour = int(current_time_str[:2])
            minute = int(current_time_str[2:])
            if set_max_ping_autokick(current_ping):
                logger.info(f"Scheduled: Set max ping to {current_ping}ms 🕐 ({hour:02d}:{minute:02d})")
                channel = client.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send(f"🕐 Max ping autokick set to `{current_ping}` ms (Scheduled {hour:02d}:{minute:02d})")
            else:
                logger.warning(f"Scheduled: Failed to set max ping to {current_ping}ms")

        async def set_ping_job_2(current_time_str, current_ping):
            hour = int(current_time_str[:2])
            minute = int(current_time_str[2:])
            if set_max_ping_autokick(current_ping):
                logger.info(f"Scheduled: Set max ping to {current_ping}ms 🕒 ({hour:02d}:{minute:02d})")
                channel = client.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send(f"🕒 Max ping autokick set to `{current_ping}` ms (Scheduled {hour:02d}:{minute:02d})")
            else:
                logger.warning(f"Scheduled: Failed to set max ping to {current_ping}ms")

        # Register async jobs properly using scheduler.add_job
        scheduler.add_job(set_ping_job_1, CronTrigger(hour=job_1_hour, minute=job_1_minute, timezone=timezone(tz_name)), id="set_ping_job_1", args=[job_1_time_initial, ping_1_initial])
        scheduler.add_job(set_ping_job_2, CronTrigger(hour=job_2_hour, minute=job_2_minute, timezone=timezone(tz_name)), id="set_ping_job_2", args=[job_2_time_initial, ping_2_initial])

        client.run(BOT_TOKEN)
