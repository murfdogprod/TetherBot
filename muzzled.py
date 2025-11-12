import os
import sys
import re
import time
import math
import json
import ast
import base64
import random
import shutil
import string
import sqlite3
import traceback
import subprocess
import asyncio
import requests
import pronouncing
import zipfile
from datetime import datetime, timedelta, timezone
from collections import deque


import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Select
from discord import Option, Message
from discord.commands import context
from dotenv import load_dotenv
from gtts import gTTS

# Custom project-specific import
import shared
from shared import (
    with_config,
    unpack_config,
    unpack_config_for_guild,
    safe_send,
)

# Py-cord specific imports
from discord.commands import Option, SlashCommandGroup, slash_command
from discord.commands.context import AutocompleteContext




Version_Number = "2.3.1"
Version_Notes = "ping update"
# Main version / subversion / patch

ARCHIVE_DIR = "archive"
os.makedirs(ARCHIVE_DIR, exist_ok=True)
os.makedirs("cogs", exist_ok=True)

# Assuming you have this global connection already
global_conn = sqlite3.connect("db/global.db")

def create_last_sent_table():
    cur = global_conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prison_status_last_sent (
        guild_id INTEGER PRIMARY KEY,
        last_sent TEXT
    )
    """)
    global_conn.commit()


async def run_launch_and_report(bot):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "launch.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()
    output = stdout.decode() + "\n" + stderr.decode()

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        print("❌ Log channel not found!")
        return

    if proc.returncode != 0:
        # Discord message limit ~2000 chars, chunk output if too long
        chunk_size = 1900
        for i in range(0, len(output), chunk_size):
            await channel.send(f"```\n{output[i:i+chunk_size]}\n```")
    else:
        await channel.send("✅ launch.py ran successfully with no errors.")

def get_available_databases(bot):
    dbs = {"Global (global)": "db/global.db"}
    servers_path = "db/servers"
    if os.path.isdir(servers_path):
        for filename in os.listdir(servers_path):
            if filename.endswith(".db"):
                server_id = filename[:-3]
                try:
                    guild = bot.get_guild(int(server_id))
                    name = guild.name if guild else f"Unknown Server"
                    label = f"{name} ({server_id})"
                    dbs[label] = os.path.join(servers_path, filename)
                except Exception:
                    continue
    return dbs



# Autocomplete function for database names
async def database_autocomplete(ctx: discord.AutocompleteContext):
    databases = get_available_databases(ctx.interaction.client)
    return list(databases.keys())[:25]  # Discord limits choices to 25


def ensure_prefix_column():
    try:
        shared.c.execute("PRAGMA table_info(server_config)")
        existing_columns = [row[1] for row in shared.c.fetchall()]

        if "prefix" not in existing_columns:
            shared.c.execute("ALTER TABLE server_config ADD COLUMN prefix TEXT DEFAULT '!>'")
            shared.conn.commit()
            print("✅ Added 'prefix' column to server_config")
    except Exception as e:
        print(f"[ensure_prefix_column] Error: {e}")



async def load_prefixes():
    shared.prefixes = {}  # Make sure this dict exists

    shared.c.execute("SELECT guild_id, prefix FROM server_config")
    rows = shared.c.fetchall()

    for guild_id, prefix in rows:
        shared.prefixes[guild_id] = prefix or "!>"  # default prefix if null

def get_prefix(bot, message):
    if not message.guild:
        return "!>"
    return shared.prefixes.get(message.guild.id, "!>")
    
bot = commands.Bot(command_prefix=get_prefix, intents=discord.Intents.all())


class DiscordLogger:
    def __init__(self, bot, channel_id):
        self.bot = bot
        self.channel_id = channel_id
        self._buffer = ""
        self._channel = None

    def write(self, message):
        self._buffer += message
        if "\n" in self._buffer:
            asyncio.run_coroutine_threadsafe(self.send(self._buffer), self.bot.loop)
            self._buffer = ""

    def flush(self):
        pass  # Required for file-like compatibility

    async def send(self, text):
        if not self._channel:
            self._channel = self.bot.get_channel(self.channel_id)
        if self._channel and text.strip():
            chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]  # Discord limit
            for chunk in chunks:
                try:
                    await self._channel.send(f"```\n{chunk.strip()}\n```")
                except Exception:
                    pass  # Avoid feedback loops if Discord fails


YOUR_SERVER_ID = 1362071490048032935  # your user ID or bot server ID


async def log_error_to_channels(
    bot,
    error,
    context_label="unspecified",
    ctx=None,
    guild=None,
    channel=None,
    user=None,
    extra_info=None
):
    # Resolve context from ctx if provided
    if ctx:
        guild = ctx.guild
        channel = ctx.channel
        user = ctx.author

    guild_id = guild.id if guild else None
    guild_name = guild.name if guild else "DM or Unknown"
    user_str = f"{user} (`{user.id}`)" if user else "Unknown User"

    # Build basic error description
    if isinstance(error, Exception):
        error_text = f"`{type(error).__name__}: {error}`"
    else:
        error_text = f"`{error}`"

    command_name = getattr(ctx.command, "qualified_name", context_label) if ctx else context_label

    error_message = (
        f"⚠️ **Error in `{command_name}`**\n"
        f"Server: {guild_name} (`{guild_id}`)\n"
        f"User: {user_str}\n"
        f"Error: {error_text}"
    )

    if extra_info:
        error_message += f"\nℹ️ **Extra Info:** {extra_info}"

    try:
        with sqlite3.connect("db/global.db") as conn:
            c = conn.cursor()
            c.execute("SELECT guild_id, log_channel_id FROM server_config")
            all_logs = dict(c.fetchall())  # {guild_id: log_channel_id}

            # Send to this guild's log channel
            if guild_id in all_logs:
                log_channel = bot.get_channel(all_logs[guild_id])
                if log_channel:
                    await log_channel.send(error_message)

            # Send to your dev/owner server's log channel too

            if guild_id != YOUR_SERVER_ID and YOUR_SERVER_ID in all_logs:
                your_log_channel = bot.get_channel(all_logs[YOUR_SERVER_ID])
                if your_log_channel:
                    await your_log_channel.send(error_message)

    except Exception as e:
        print(f"[log_error_to_channels] Failed to log error: {e}")
        print(f"Original error: {error}")



# ------ DB Table Setup
TABLE_COLUMNS = {
    "gagged_users": ["user_id", "type", "status"],
    "double_type_users": ["user_id"],
    "prison_users": ["user_id", "channel_id", "balance"],
    "user_auth": ["user_id", "auth_level"],
    "cooldown_users": ["user_id", "cooldown"],
    "solitary_confinement": ["user_id", "thread_id", "archive_date"],
    "enforced_words": ["user_id", "word", "initial_time", "added_time"],
    "enforcement_offenses": ["user_id", "count"],
    "banned_words": ["user_id", "word", "initial_time", "added_time"],
    "ignored_users": ["user_id"],
    "user_settings": ["user_id", "enforcement_action"],
    "line_assignments": ["assignment_id", "user_id", "line", "lines_required", "penalty_lines", "last_submission", "assigned_by"],
    "active_line_writers": ["user_id", "line", "lines_required", "lines_written", "penalty_lines", "assignment_id", "assigned_by"],
    "user_wallets": ["user_id", "balance"],
    "bets": ["id", "initiator_id", "opponent_id", "amount", "game", "status", "created_at"],
    "daily_claims": ["user_id", "last_claim", "claim_count", "streak_days"],
    "timer_logs": ["id", "user_id", "timer_name", "start_time"],
    "command_logs": ["id", "user_id", "command", "timestamp", "arguments"],
    "allowed_users": ["user_id", "channel_id"],
    "locked_users": ["user_id", "locked_by"],
    "pishock_users": ["user_id", "code", "shock_min", "shock_max", "vibrate_min", "vibrate_max", "duration_min", "duration_max", "line_writing_shock_intensity", "line_writing_shock_duration", "enforcement_action_shock_intensity", "enforcement_action_shock_duration", "lightning_reaction_shock_intensity", "lightning_reaction_shock_duration"],
    "lovense_users": ["user_id", "token"],
    "solitary_pings": ["thread_id", "last_ping"],
    "bot_status": ["id", "restart_status"],
    "pot": ["id", "pot"],
    "command_bans": ["user_id"],
    "word_length_limits": ["user_id", "min_length", "max_length"]
}

async def table_autocomplete(ctx: AutocompleteContext):
    current = (ctx.value or '').lower()
    return [name for name in TABLE_COLUMNS if current in name.lower()][:25]

async def column_autocomplete(ctx: AutocompleteContext):
    table = ctx.options.get("table_name")
    current = (ctx.value or '').lower()
    if not table or table not in TABLE_COLUMNS:
        return []
    return [col for col in TABLE_COLUMNS[table] if current in col.lower()][:25]

# ------------------- Configuration -------------------
load_dotenv(dotenv_path="tetherbot.env")

# Read values from the environment
BOT_TOKEN = os.getenv('BOT_TOKEN')
PRISON_CHANNEL_ID = int(os.getenv('PRISON_CHANNEL_ID'))
DEFAULT_AUTH_MODE = os.getenv('DEFAULT_AUTH_MODE')
TEST_SERVER_ID = 1362071490048032935
'''
LINE_WRITING_ID = int(os.getenv('LINE_WRITING_ID'))
COUNTING_ID = 1366615223221485621  # Channel ID for counting
GAMBLING_ID = 1366615223221485621 # Channel ID for gambling
SOLITARY_ROLE_ID = int(os.getenv('SOLITARY_ROLE_ID'))
PRISONER_ROLE_ID = int(os.getenv('PRISONER_ROLE_ID'))
SC_ROLE_ID = int(os.getenv('SC_ROLE_ID'))
DEFAULT_AUTH_MODE = os.getenv('DEFAULT_AUTH_MODE')
BOTSTATUS_ID = 1368298892847747093  # Channel ID for bot status updates
TASK_CHANNEL_ID = 1364615676907618354
LOG_CHANNEL_ID = 1376990411742117919
'''

# Configuration (USE ENVIRONMENT VARIABLES IN PRODUCTION!)
PISHOCK_USERNAME = (os.getenv('PISHOCK_USERNAME')) # PiShock username
PISHOCK_API_KEY = (os.getenv('PISHOCK_API_KEY'))  # PiShock API key

# Lovense API stuff
Lovense_Token = (os.getenv('Lovense_Token'))
Lovense_Key = (os.getenv('Lovense_Key'))
Lovense_IV = (os.getenv('Lovense_IV'))

# Replace with your actual user ID
BOT_OWNER_ID = 1146469921270792326

def is_bot_owner():
    def predicate(ctx):
        return ctx.author.id == BOT_OWNER_ID
    return commands.check(predicate)





Mod = {1146469921270792326, 169981533187211264, 964573737477341235, 927628071706689636, 186523925264465920, 754523779887136799}
additional_lock_managers = {
    380625660277948426  # Another one
}

AUTHORIZED_LOCK_MANAGERS = Mod | additional_lock_managers

async def is_user_server_mod(bot, guild_id, user_id):
    # Bot owner always allowed
    if user_id == BOT_OWNER_ID:
        return True

    guild = bot.get_guild(guild_id)
    if guild:
        member = guild.get_member(user_id)
        if member and member.guild_permissions.administrator:
            return True

    try:
        with sqlite3.connect("db/global.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM moderators WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            return cur.fetchone() is not None
    except Exception as e:
        # Optionally log the error
        print(f"[is_user_server_mod] DB error: {e}")
        return False

def is_server_mod():
    async def predicate(ctx):
        return await is_user_server_mod(ctx.bot, ctx.guild.id, ctx.author.id)
    return commands.check(predicate)


ALLOWED_QUICK_CHAT = {
    # General
    "I got it!",
    "Need boost!",
    "Take the shot!",
    "Defending...",
    "Go for it!",
    "Centering!",
    "All yours.",
    "In position.",
    "Incoming!",
    "Faking.",
    "Bumping!",
    "Demoing!",
    
    # Compliments
    "Nice shot!",
    "Great pass!",
    "Thanks!",
    "What a save!",
    "Nice one!",
    "Well played.",
    "Great clear!",
    "Nice block!",
    "Nice demo!",
    "Great bump!",
    "Savage!",
    
    # Reactions
    "OMG!",
    "Noooo!",
    "Wow!",
    "Close one!",
    "Whew.",
    "Siiiick!",
    "Holy cow!",
    "Calculated.",
    "Whoops...",
    "Okay.",
    "Everybody dance!",
    
    # Apologies
    "Sorry!",
    "No problem.",
    "Oops!",
    "My bad.",
    
    # Post-game
    "Good game!",
    "gg",
    "That was fun!",
    "Rematch!",
    
    # Tactical
    "Pressing.",
    "Cheating up.",
    "Rotating.",
    "Heads up!",
    "To you!",
    "You got this!"
}

RESTRICTED_USER_ID = 1  # Replace with your target Discord user ID



# ------------------- Database Setup -------------------
conn = sqlite3.connect('db/global.db')
shared.conn = conn
c = conn.cursor()
shared.c = c

# Create tables


clicker_users = {824747106370584599, 169981533187211264, 380625660277948426, 1146469921270792326, 1292644804831875073, 885014775245975573}


from shared import PerGuildDict



bot_paused = False

WEBHOOK_NAME = "GagWebhook"
is_restarting = False  # Global variable to track if bot is restarting

COST_TO_START = 1
FREEDOM_DURATION = 30 * 60
MAX_FREEDOM_DURATION = 2 * 60 * 60  # 2 hours in seconds
SECONDS_PER_BUCK = 2
temporarily_freed = {}
COUNT_TO = 100
freedom_pot = 0


COMMAND_LIMIT = 5
TIME_WINDOW = 10  # seconds



# ------------------- Bot Setup -------------------
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    case_insensitive=True
)


# Save the original method
original_add_reaction = Message.add_reaction

# Monkey-patch with the silent check
async def patched_add_reaction(self, emoji):
    try:
        if not shared.silent_command_running:
            return await original_add_reaction(self, emoji)
    except Exception as e:
        print(f"Failed to add reaction: {e}")
    return None

Message.add_reaction = patched_add_reaction

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return  # Blocked or not allowed — already handled in check
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ You don't have permission to use this command!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Missing required arguments!")
    else:
        await log_error_to_channels(bot=bot, error=error, ctx=ctx)


@bot.event
async def on_application_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        # Only suppress CheckFailure from the command ban check
        if str(error) == "User is blocked from using commands.":
            return
        else:
            await ctx.respond("❌ You don't meet the requirements to run this command.", ephemeral=True)
    else:
        await log_error_to_channels(bot=bot, error=error, ctx=ctx)






URL_REGEX = re.compile(r'https?://\S+')
NITRO_EMOJI_REGEX = re.compile(r'<a?:\w+:\d+>')

LOG_CHANNEL_ID = 1376990411742117919

@bot.event
async def on_ready():


    BOTSTATUS_ID = 1368298892847747093

    print(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name="with chewtoys!"))
    status_channel = bot.get_channel(LOG_CHANNEL_ID)

    # Redirect stdout/stderr to Discord logging
    sys.stdout = DiscordLogger(bot, LOG_CHANNEL_ID)
    sys.stderr = sys.stdout

    # Check global bot_status for restart flag
    shared.c.execute("SELECT restart_status FROM bot_status WHERE id = 1")
    restart_status = shared.c.fetchone()


    ensure_prefix_column()
    # Send online status if not a restart
    if not (restart_status and restart_status[0] == "restarting"):
        new_channel_name = "✅bot-online"
        channel = bot.get_channel(BOTSTATUS_ID)

        if channel:
            embed = discord.Embed(
                title="👋 BOT IS ONLINE!",
                description=f"`Version: {Version_Number}`",
                color=0x00ffcc
            )
            embed.set_footer(text=Version_Notes)

            await channel.send(embed=embed)

            try:
                if channel.name != new_channel_name:
                    await channel.edit(name=new_channel_name)
                    print(f"Renamed channel to: {new_channel_name}")
            except Exception as e:
                print(f"Failed to rename channel: {e}")
        else:
            print("Channel not found or bot lacks access.")

    # Clear restart flag if previously set
    if restart_status and restart_status[0] == "restarting":
        shared.c.execute("UPDATE bot_status SET restart_status = NULL WHERE id = 1")
        shared.conn.commit()

    # Ensure server_config columns exist
    ensure_cog_columns_from_files()


    await load_prefixes()


    if not process_command_logs.is_running():
        process_command_logs.start()

    


    # --- Load per-guild memory state ---
    for guild in bot.guilds:
        guild_id = guild.id
        db = shared.get_server_db(guild_id)
        cur = db.cursor()

        # Gagged users
        cur.execute("SELECT user_id, type FROM gagged_users WHERE status = 'active'")
        gagged = {row[0]: row[1] for row in cur.fetchall()}
        shared.gagged_users[guild_id].update(gagged)
        

        # PiShock users (global dict)
        cur.execute("SELECT user_id, code FROM pishock_users")
        pishock_code = {row[0]: row[1] for row in cur.fetchall()}
        shared.user_pishock_codes[guild_id].update(pishock_code)

        # Prison users
        cur.execute("SELECT user_id, channel_id FROM prison_users")
        prison = {row[0]: row[1] for row in cur.fetchall()}
        shared.prison_users[guild_id].update(prison)

        # Cooldown users
        cur.execute("SELECT user_id, cooldown FROM cooldown_users")
        cooldown_rows = cur.fetchall()
        for row in cooldown_rows:
            shared.cooldown_users[guild_id][row[0]] = row[1]
            shared.last_message_times[guild_id][row[0]] = 0

        # Solitary confinement
        cur.execute("SELECT user_id, thread_id FROM solitary_confinement")
        solitary = {row[0]: row[1] for row in cur.fetchall()}
        shared.solitary_confinement[guild_id].update(solitary)

        # Enforced words
        cur.execute("SELECT user_id, word, initial_time, added_time FROM enforced_words")
        enforced_count = 0
        for row in cur.fetchall():
            user_id, word, initial_time, added_time = row
            shared.enforced_words[guild_id].setdefault(user_id, {})[word.lower()] = {
                "initial_time": initial_time,
                "added_time": added_time
            }
            enforced_count += 1

        # Enforcement offenses
        cur.execute("SELECT user_id, count FROM enforcement_offenses")
        shared.enforcement_offenses[guild_id].update({row[0]: row[1] for row in cur.fetchall()})

        # Banned words
        cur.execute("SELECT user_id, word, initial_time, added_time FROM banned_words")
        banned_count = 0
        for row in cur.fetchall():
            user_id, word, initial_time, added_time = row
            shared.banned_words[guild_id].setdefault(user_id, {})[word.lower()] = {
                "initial_time": initial_time,
                "added_time": added_time
            }
            banned_count += 1

        # Ignored users
        cur.execute("SELECT user_id FROM double_type_users")
        for row in cur.fetchall():
            shared.double_type_users[guild_id].add(row[0])

        # Ignored users
        cur.execute("SELECT user_id FROM ignored_users")
        for row in cur.fetchall():
            shared.ignored_users[guild_id].add(row[0])
        
        cur.execute("SELECT user_id, min_length, max_length FROM word_length_limits")
        for row in cur.fetchall():
            user_id, min_length, max_length = row
            shared.word_length_limits[guild_id][user_id] = {
                "min_length": min_length,
                "max_length": max_length
            }

        # Line writing sessions
        cur.execute("SELECT user_id, line, lines_required, lines_written FROM active_line_writers")
        writer_count = 0
        for row in cur.fetchall():
            uid, line, req, written = row
            shared.line_writing_sessions[guild_id][uid] = {
                "line": line,
                "lines_required": req,
                "lines_written": written
            }
            writer_count += 1

        # Locked users
        cur.execute("SELECT user_id, locked_by FROM locked_users")
        locked_count = 0
        for user_id, locked_by in cur.fetchall():
            shared.locked_users[guild_id].add(user_id)
            shared.locked_by_map[guild_id][user_id] = locked_by
            locked_count += 1

        # Assemble smart stat line
        stat_parts = []
        if gagged: stat_parts.append(f"{len(gagged)} gagged users")
        if prison: stat_parts.append(f"{len(prison)} prisoned users")
        if solitary: stat_parts.append(f"{len(solitary)} solitary records")
        if enforced_count: stat_parts.append(f"{enforced_count} enforced words")
        if banned_count: stat_parts.append(f"{banned_count} banned words")
        if locked_count: stat_parts.append(f"{locked_count} locked users")
        if writer_count: stat_parts.append(f"{writer_count} writing assignments")

        stats_str = ", ".join(stat_parts) if stat_parts else "no active data"
        print(f"✅ {guild.name} ({guild_id}) — {stats_str}")


    # --- Load all Cogs ---
    all_cogs = [f"cogs.{name}" for name in list_toggleable_cogs()]
    for cog in all_cogs:
        try:
            bot.load_extension(cog)
            print(f"✅ Loaded {cog}")
        except Exception as e:
            error_msg = f"❌ Failed to load `{cog}`: {e}"
            traceback_str = traceback.format_exc()
            print(error_msg)
            print(traceback_str)
            if status_channel:
                await status_channel.send(
                    f"{error_msg}\n```{traceback_str[-1500:]}```"
                )

    # Update server count channel name if changed
    try:
        server_count_channel = bot.get_channel(1380259276974461081)
        if server_count_channel:
            current_count = len(bot.guilds)
            expected_name = f"{current_count}-servers"
            if server_count_channel.name != expected_name:
                await server_count_channel.edit(name=expected_name)
                print(f"🔢 Updated server count channel to '{expected_name}'")
    except Exception as e:
        print(f"[Server Count] Failed to update channel name: {e}")
    # Final summary
    if not send_prison_status_periodically.is_running():
        send_prison_status_periodically.start()
    
    create_last_sent_table()
    print("✅ on_ready complete.")


@bot.event
async def on_typing(channel, user, when):
    # Replace with your actual IDs
    '''
    target_user_id = 927628071706689636

    # Check user and guild match
    if user.id == target_user_id and channel.guild and channel.guild.id == TEST_SERVER_ID:
        await channel.send(f"{user.mention} I see you typing... 👀", delete_after=5)
    '''

@bot.event
async def on_message_edit(before, after):
    # Avoid bot messages and partials
    if after.author.bot or not after.content:
        return
    if before.content == after.content:
        return

    # Reuse your enforcement logic
    await on_message_or_edit(after, edited = True)

@bot.event
async def on_message(message):
    await on_message_or_edit(message, edited = False)


async def on_message_or_edit(message, edited: bool = False):
    try:

        if not message.author.bot and message.guild:
             # ✅ Load server-specific database
            guild_id = message.guild.id
            user_id = message.author.id
            db = shared.get_server_db(guild_id)
            c = db.cursor()
            config = shared.get_server_config(message.guild.id)  # or message.guild.id
            conn = c.connection



            if not config:
                if message.author.guild_permissions.administrator:
                    try:
                        await message.author.send(
                            f"⚠️ No configuration found for **{message.guild.name}**.\n"
                            f"Please use `/setup_ids` in your server to set the required channels and roles."
                            f"Also join the support server for more information and help setting up this bot. https://discord.gg/zeESfuRMf8"
                        )
                    except discord.Forbidden:
                        # User has DMs off or blocked the bot
                        pass
                return

            # Now your variables match the original names
            (
                PRISON_CHANNEL_ID,
                LINE_WRITING_ID,
                COUNTING_ID,
                GAMBLING_ID,
                SOLITARY_ROLE_ID,
                PRISONER_ROLE_ID,
                SC_ROLE_ID,
                BOTSTATUS_ID,
                TASK_CHANNEL_ID,
                LOG_CHANNEL_ID
            ) = shared.unpack_config_from_obj(config)

            if message.channel.id == LOG_CHANNEL_ID:
                await bot.process_commands(message)
                return

        shared.returnvar = False
        global bot_paused

        if bot_paused and not message.content.startswith('!>pause'):
            return  # Ignore all messages except the unpause command
        
        if message.author.bot:
            return
        
        original_author = message.author
        original_content = message.content
        user_id = message.author.id
        guild_id = message.guild.id if message.guild else None

        # If user has a pending silent command in this guild:
        key = (message.guild.id if message.guild else None, message.author.id)

        if key in shared.pending_silent_commands:
            # Prevent recursion
            if getattr(shared, "silent_command_running", False):
                # If flag is set, we're already running a silent command, skip to normal processing
                return await bot.process_commands(message)

            shared.silent_command_running = True
            try:
                run_as, silent_cmd = shared.pending_silent_commands[key]

                if message.guild and message.guild.id == key[0]:
                    run_as = message.author
                    original_author = message.author
                    original_content = message.content

                    try:
                        message.author = run_as
                        message.content = silent_cmd

                        ctx = await bot.get_context(message)
                        original_ctx_author = ctx.author
                        ctx.author = run_as
                        del shared.pending_silent_commands[key]
                        await bot.invoke(ctx)

                    finally:
                        # Restore everything
                        message.author = original_author
                        message.content = original_content
                        ctx.author = original_ctx_author



            finally:
                shared.silent_command_running = False
        
        
        if "*click*" in message.content.lower() or "*clicks*" in message.content.lower():
            mp3_file_path = "non-suspicious sound.mp3"
            
            # Check if the mp3 file exists and send it if it does
            if os.path.exists(mp3_file_path):
                # Rename the file when sending (without changing the original filename in the directory)
                await message.channel.send(file=discord.File(mp3_file_path, filename="wruf.mp3"))
                return


        if "!>bark" in message.content.lower() or "!>bork" in message.content.lower():
            bark_file_path = "bark.png"
            if os.path.exists(bark_file_path):
                await message.channel.send(file=discord.File(bark_file_path, filename="bark.png"))
                return

        if "!>rigged" in message.content.lower() or "!>unfair" in message.content.lower():
            rigged_file_path = "rigged.png"
            if os.path.exists(rigged_file_path):
                await message.channel.send(file=discord.File(rigged_file_path, filename="rigged.png"))
                return

        if "*collar*" in message.content.lower() or "!>collar" in message.content.lower():
            collar_file_path = "collar.mp3"
            
            # Check if the mp3 file exists and send it if it does
            if os.path.exists(collar_file_path):
                # Rename the file when sending (without changing the original filename in the directory)
                await message.channel.send(file=discord.File(collar_file_path, filename="jingle.mp3"))
                return


        if (
            message.attachments and 
            message.author.id == 1146469921270792326 and 
            isinstance(message.channel, discord.DMChannel)
        ):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

            for attachment in message.attachments:
                filename = attachment.filename.lower()

                try:
                    # Handle database update
                    if filename.endswith(".db"):
                        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                        os.makedirs("archive", exist_ok=True)

                        if filename == "global.db":
                            # Backup and replace global DB
                            shutil.copy("db/global.db", f"archive/global_{timestamp}.db")
                            await attachment.save("db/global.db")
                            await message.channel.send("✅ Global database updated and archived.")

                        elif filename.isdigit() and os.path.exists(f"db/servers/{filename}"):
                            # Backup and replace a known server DB
                            shutil.copy(f"db/servers/{filename}", f"archive/{filename[:-3]}_{timestamp}.db")
                            await attachment.save(f"db/servers/{filename}")
                            await message.channel.send(f"✅ Server DB `{filename}` updated and archived.")

                        else:
                            # Save unknown/new DB files in db/
                            await attachment.save(f"db/{filename}")
                            await message.channel.send(f"📦 Unknown DB `{filename}` saved to `db/`.")

                    # Handle Python files
                    elif filename.endswith(".py"):
                        if filename == "muzzled.py" or filename == "muzzled_reset.py":
                            shutil.copy("muzzled.py", f"archive/muzzled_{timestamp}.py")
                            await attachment.save("muzzled.py")
                            await message.channel.send("✅ muzzled.py updated. Restarting...")
                            subprocess.Popen([sys.executable, "launch.py"])
                            sys.exit(0)
                        elif filename == "muzzled_safe.py":
                            await attachment.save("muzzled_safe.py")
                            await message.channel.send("✅ muzzled_safe.py updated in the main folder.")
                        elif filename == "watchdog.py":
                            await attachment.save("watchdog.py")
                            await message.channel.send("✅ watchdog.py updated in the main folder.")
                        elif filename == "launch.py":
                            await attachment.save("launch.py")
                            await message.channel.send("✅ launch.py updated in the main folder.") 
                        elif filename == "launch_safe.py":
                            await attachment.save("launch_safe.py")
                            await message.channel.send("✅ launch_safe.py updated in the main folder.")                       

                        elif filename == "shared.py":
                            await attachment.save("shared.py")
                            await message.channel.send("✅ shared.py updated in the main folder.")
                        else:
                            cog_path = os.path.join("cogs", filename)
                            await attachment.save(cog_path)
                            await message.channel.send(f"✅ Python file `{filename}` saved to `cogs/`.")

                    elif filename == "disabled_cogs.json":
                        shutil.copy("disabled_cogs.json", f"archive/disabled_cogs_{timestamp}.json")
                        await attachment.save("disabled_cogs.json")
                        await message.channel.send("✅ `disabled_cogs.json` updated and archived.")

                    # Handle .env file
                    elif filename.endswith(".env"):
                        shutil.copy("tetherbot.env", f"archive/tetherbot_{timestamp}.env")
                        await attachment.save("tetherbot.env")
                        await message.channel.send("✅ `.env` file saved as `tetherbot.env` and archived.")
                        subprocess.Popen([sys.executable, "launch.py"])
                        sys.exit(0)

                    # Default handler
                    else:
                        save_path = os.path.join(os.getcwd(), attachment.filename)
                        await attachment.save(save_path)
                        await message.channel.send(f"✅ File `{attachment.filename}` saved.")
                        subprocess.Popen([sys.executable, "launch.py"])
                        sys.exit(0)

                except Exception as e:
                    await message.channel.send(f"❌ Failed to process `{filename}`: {e}")
                    



        if bot.user in message.mentions:
            await message.add_reaction("<:goofy:1367975113835810816>")


            # Skip messages that just created threads
        if message.thread is not None:
            return  # message was used to create a thread

        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return



            
    #    if random.randint(1, 1_000_000_000_000) == 1:
    #        user = await bot.fetch_user(927628071706689636)
    #        await message.guild.ban(user)
    #        await message.channel.send(f"🔨 Against all odds, user `{user}` (`{user.id}`) has been banned.")
    #        return
        # Skip forced filter logic if message is in DMs
        if not message.guild:
            forced_filtered = False
        else:
            db = shared.get_server_db(guild_id)
            c = db.cursor()
            c.execute("SELECT 1 FROM nonbypass_users WHERE guild_id = ? AND user_id = ?", (message.guild.id, message.author.id))
            forced_filtered = c.fetchone() is not None


        alt_server_command = False

        user_id = message.author.id
        prefix = get_prefix(bot, message)  # call your prefix function properly
        is_command = message.content.startswith(prefix)
        content = message.content.lower()
        con_line = message.content.strip()
        channel_id = message.channel.id

        bypass_triggers = (
            message.content.startswith("((") or
            message.content.startswith("-# ((") or
            message.content.startswith("# ((") or
            "​" in message.content or
            message.content.startswith("Owo") or
            message.content.startswith("owo") or
            message.content.startswith("OWO")
        )



        # Check for media content
        has_media = message.attachments or message.stickers

        if message.content.startswith("!>") and prefix != "!>":
            return

        if not forced_filtered and (bypass_triggers or has_media):
            return  # Ignore message if bypassing and not force-filtered
        

        if not message.guild:
            return

        author = message.author  # guaranteed to be a Member now



        # Annoying mock feature (reactions)
        if (
            message.guild and 
            message.guild.id == TEST_SERVER_ID and 
            message.author.id == 927628071706689636 and
            False
        ):
            await message.add_reaction("⬆️")
            await message.add_reaction("<:goofy:1367975113835810816>")
            await message.add_reaction("🇧")
            await message.add_reaction("🇷")
            await message.add_reaction("🇦")
            await message.add_reaction("🇹")

        # Command delay for same user, test server only
        if (
            is_command and 
            message.guild and 
            message.guild.id == TEST_SERVER_ID and 
            message.author.id == 927628071706689636 and
            False
        ):
            await asyncio.sleep(14)


        
        if not is_command and URL_REGEX.search(content):
            return
        
        #Quick Chat
        if message.author.id == RESTRICTED_USER_ID and not is_command:
            content = message.content.strip()

            if content not in ALLOWED_QUICK_CHAT:
                try:
                    await message.delete()
                    await message.channel.send(
                        f"{message.author.mention} ❌ Only Rocket League quick chat phrases are allowed!",
                        delete_after=60
                    )
                except Exception as e:
                    await log_error_to_channels(
                        bot=bot,
                        guild=message.guild,
                        channel=message.channel,
                        user=message.author,
                        error=e,
                        context_label="allowed quick chat error"
                    )

        if "!>sluppy" in message.content.lower() or "!>frshwtr" in message.content.lower():
            await message.delete()
            silly_file_path = "dog-silly.gif"
            if os.path.exists(silly_file_path):
                await message.channel.send(file=discord.File(silly_file_path, filename="dog-silly.gif"))
                await message.channel.send(
                        f"-# Credit goes to floofy for stealing the gif from another server",
                        delete_after=1
                    )
            return



        # Ensure required variables are defined early
        user = message.author
        guild_id = message.guild.id

        # Counting game logic
        counting_game = shared.counting_game[guild_id]

        if counting_game["active"] and channel_id == COUNTING_ID and not is_command:
            expected = counting_game["count"] + 1
            result = safe_eval(message.content)

            if result != expected:
                await message.add_reaction("❌")
                await message.channel.send(f"❌ Incorrect number! Expected `{expected}`. Challenge failed.")
                counting_game["active"] = False
                shared.clear_freedom_pot(guild_id)
                shared.counting_game[guild_id] = counting_game
                return

            if user_id == counting_game["last_user_id"]:
                await message.add_reaction("❌")
                await message.channel.send("⛔ You cannot count twice in a row! Your second number did not count.")
                await message.channel.send(f"The next number is `{expected}`.")
                shared.counting_game[guild_id] = counting_game
                return

            if result == expected:
                await message.add_reaction("✅")
                shared.counting_game[guild_id] = counting_game

            # Update state directly (no need to reassign)
            counting_game["count"] += 1
            counting_game["last_user_id"] = user_id
            counting_game["participants"].add(user_id)
            shared.counting_game[guild_id] = counting_game

            if counting_game["count"] >= COUNT_TO:
                counting_game["active"] = False
                shared.counting_game[guild_id] = counting_game
                now = time.time()
                pot = shared.get_freedom_pot(guild_id)
                duration = min(pot * SECONDS_PER_BUCK, MAX_FREEDOM_DURATION)

                for pid in shared.prison_users[guild_id]:
                    shared.temporarily_freed[guild_id][pid] = now + duration

                await message.channel.send(
                    f"🎉 Challenge complete! All prisoners are freed for {pot * SECONDS_PER_BUCK:.0f} seconds."
                )
                shared.clear_freedom_pot(guild_id)

            return

        # Gambling or other passive interaction blocking
        if not is_command:
            if channel_id == GAMBLING_ID:
                guild_players = shared.players.get(guild_id, [])
                guild_queue = shared.queue.get(guild_id, [])

                if user in guild_players or user in guild_queue:
                    return
            

            c.execute(
                "SELECT 1 FROM allowed_users WHERE user_id = ? AND channel_id = ?",
                (user_id, channel_id)
            )
            if c.fetchone():
                return

            if user_id in shared.user_pishock_codes[guild_id] and not user.bot:
                if random.random() < 0.2:  # 20% chance
                    try:
                        await message.add_reaction('⚡')
                    except discord.errors.Forbidden:
                        await log_error_to_channels(
                            bot=bot,
                            guild=message.guild,
                            channel=message.channel,
                            user=message.author,
                            error=e,
                            context_label="missing permissions to add bolt reaction"
                        )


                    except Exception as e:
                        await log_error_to_channels(
                            bot=bot,
                            error=e,
                            context_label="Error adding reaction",
                            guild=message.guild,
                            user=message.author
                        )



        # Line writing penalty check
        if (
            not is_command and
            user_id in shared.line_writing_sessions[guild_id] and
            channel_id != LINE_WRITING_ID
        ):
            c.execute(
                "SELECT line, lines_required, lines_written, penalty_lines FROM active_line_writers WHERE user_id = ?",
                (user_id,)
            )
            row = c.fetchone()

            if row:
                expected_line, lines_required, lines_written, penalty = row

                # Apply penalty directly to database value
                new_required = lines_required + penalty

                c.execute(
                    "UPDATE active_line_writers SET lines_required = ? WHERE user_id = ?",
                    (new_required, user_id)
                )
                c.connection.commit()

                await message.channel.send(
                    f"⚠️ {user.mention}, you typed outside the line-writing channel. "
                    f"You've been penalized with **{penalty} more lines**!"
                )

                # PiShock penalty
                if user_id in shared.user_pishock_codes.get(guild_id, {}) and shared.pishock_command:
                    try:
                        c = shared.get_server_db(guild_id).cursor()
                        c.execute("""
                            SELECT line_writing_shock_intensity, line_writing_shock_duration
                            FROM pishock_users WHERE user_id = ?
                        """, (user_id,))
                        row = c.fetchone()

                        if not row:
                            await log_error_to_channels(
                                bot=bot,
                                error=f"No line writing settings for user {user_id}",
                                context_label="PiShock settings missing",
                                guild=guild_id,        # pass the relevant guild object here if you have it
                                user=user_id           # pass the relevant user object here if you have it
                            )

                            return

                        intensity, duration = row

                        result = shared.pishock_command(guild_id, user_id, op=0, intensity=intensity, duration=duration)
                        if result.get("Success"):
                            await message.channel.send(f"⚡ Shocked {user.display_name} for typing in wrong channel!")
                        else:
                            print(f"[PiShock] Failure: {result.get('Message')}")
                    except Exception as e:
                        print(f"[PiShock] Error triggering line writing shock: {e}")


                # Send updated line count
                writing_channel = message.guild.get_channel(LINE_WRITING_ID)
                if writing_channel:
                    remaining = new_required - lines_written
                    await writing_channel.send(f"{user.mention}, now you must write **{remaining} more lines**.")

            return


        con_line = message.content.strip()  # The line the user typed

        # Line writing progress logic
        if (
            not is_command and
            channel_id == LINE_WRITING_ID and
            user_id in shared.line_writing_sessions[guild_id]
        ):
            c.execute(
                "SELECT line, lines_required, lines_written, penalty_lines FROM active_line_writers WHERE user_id = ?",
                (user_id,)
            )
            row = c.fetchone()

            if row:
                expected_line, lines_required, lines_written, penalty = row
                expected_line = expected_line.strip()

                if con_line == expected_line:
                    if edited:
                        await message.channel.send("Do not try and cheat with edited messages!!")
                        await message.add_reaction("❌")
                        return

                    lines_written += 1
                    await message.add_reaction("✅")

                    c.execute(
                        "UPDATE active_line_writers SET lines_written = ? WHERE user_id = ?",
                        (lines_written, user_id)
                    )
                    c.connection.commit()

                    # VC clicker bonus
                    if user_id in clicker_users and random.randint(1, 10) == 1:
                        voice_state = user.voice
                        if voice_state and voice_state.channel:
                            voice_client = discord.utils.get(bot.voice_clients, guild=message.guild)
                            if voice_client and not voice_client.is_playing():
                                try:
                                    audio = discord.FFmpegPCMAudio("non-suspicious sound.mp3")
                                    voice_client.play(audio)
                                    while voice_client.is_playing():
                                        await asyncio.sleep(1)
                                except Exception as e:
                                    print(f"[Voice] Playback error: {e}")
                        else:
                            mp3_file_path = "non-suspicious sound.mp3"
                            if os.path.exists(mp3_file_path):
                                await message.channel.send(file=discord.File(mp3_file_path), delete_after = 10)

                else:
                    lines_required += penalty
                    await message.add_reaction("❌")

                    c.execute(
                        "UPDATE active_line_writers SET lines_required = ? WHERE user_id = ?",
                        (lines_required, user_id)
                    )
                    c.connection.commit()

                    if user_id in shared.user_pishock_codes.get(guild_id, {}) and shared.pishock_command:
                        try:
                            c.execute("""
                                SELECT line_writing_shock_intensity, line_writing_shock_duration
                                FROM pishock_users WHERE user_id = ?
                            """, (user_id,))
                            row = c.fetchone()

                            if not row:
                                print(f"[PiShock] No line writing settings for user {user_id}")
                                return

                            intensity, duration = row

                            result = shared.pishock_command(guild_id, user_id, op=0, intensity=intensity, duration=duration)
                            if result.get("Success"):
                                await message.channel.send(f"⚡ Shocked {user.display_name} for incorrect line!")
                            else:
                                print(f"[PiShock] Failure: {result.get('Message')}")
                        except Exception as e:
                            print(f"[PiShock] Error triggering line writing shock: {e}")


                remaining = lines_required - lines_written

                if remaining <= 0:
                    c.execute("DELETE FROM active_line_writers WHERE user_id = ?", (user_id,))
                    c.connection.commit()
                    del shared.line_writing_sessions[guild_id][user_id]
                    await message.channel.send(f"🎉 {user.mention} has completed their assigned lines!")

            return




        # Special check to allow processing commands during line writing
        if (
            user_id in shared.prison_users[guild_id] and
            channel_id == LINE_WRITING_ID and
            user_id in shared.line_writing_sessions[guild_id] and
            not is_command
        ):
            await bot.process_commands(message)
            return

        content = message.content.lower()

        # Suppose this is your set of users who must double-type
        if user_id in shared.double_type_users[guild_id] and not is_command:
            last = shared.pending_confirm[guild_id].get(user_id)

            if last == message.content:
                # Confirmed on second try — allow message
                shared.pending_confirm[guild_id].pop(user_id, None)


            else:
                # First attempt — delete and store
                shared.pending_confirm[guild_id][user_id] = message.content
                await message.delete()
                warning = await message.channel.send(
                    f"{message.author.mention} Say it again to confirm.",
                    delete_after=3
                )
                return

        # ---------- ENFORCED WORDS ----------
        if not is_command and user_id in shared.enforced_words[guild_id]:
            required_words = set(shared.enforced_words[guild_id][user_id].keys())
            found_words = {w for w in required_words if w in content}

            if found_words != required_words:
                try:
                    await message.delete()
                except:
                    pass

                missing = required_words - found_words
                total_timeout = 0

                for word in missing:
                    word_data = shared.enforced_words[guild_id][user_id].get(word, {})
                    initial = word_data.get("initial_time", 60)
                    added = word_data.get("added_time", 30)
                    new_initial = initial + added
                    total_timeout += new_initial

                    shared.enforced_words[guild_id][user_id][word]["initial_time"] = new_initial
                    c.execute(
                        "UPDATE enforced_words SET initial_time = ? WHERE user_id = ? AND word = ?",
                        (new_initial, user_id, word)
                    )

                # Track offenses
                offenses = shared.enforcement_offenses[guild_id].get(user_id, 0) + 1
                shared.enforcement_offenses[guild_id][user_id] = offenses
                c.execute(
                    "INSERT OR REPLACE INTO enforcement_offenses (user_id, count) VALUES (?, ?)",
                    (user_id, offenses)
                )

                actions = get_user_enforcement_action(user_id, c)
                warning = f"⚠️ {message.author.mention} Missing words: {', '.join(missing)}. "

                if "timeout" in actions:
                    try:
                        await message.author.timeout(
                            datetime.now(timezone.utc) + timedelta(seconds=total_timeout)
                        )
                        expiry = datetime.now(timezone.utc) + timedelta(seconds=total_timeout)
                        timestamp = int(expiry.timestamp())  # Convert to Unix timestamp
                        warning += f"Timed out for {total_timeout}s. Will return in <t:{timestamp}:R> "
                    except Exception:
                        warning += "❌ Could not timeout. "

                if "cooldown" in actions:
                    added_cd = sum(
                        shared.enforced_words[guild_id][user_id][w].get("added_time", 30)
                        for w in missing
                    )
                    shared.cooldown_users[guild_id][user_id] = shared.cooldown_users[guild_id].get(user_id, 0) + added_cd
                    shared.last_message_times[guild_id][user_id] = time.time()

                    c.execute(
                        "INSERT OR REPLACE INTO cooldown_users (user_id, cooldown) VALUES (?, ?)",
                        (user_id, shared.cooldown_users[guild_id][user_id])
                    )
                if "pishock" in actions and shared.pishock_command:
                    try:
                        c.execute("""
                            SELECT enforcement_action_shock_intensity, enforcement_action_shock_duration
                            FROM pishock_users WHERE user_id = ?
                        """, (user_id,))
                        row = c.fetchone()

                        if row:
                            intensity, duration = row
                            result = shared.pishock_command(guild_id, user_id, op=0, intensity=intensity, duration=duration)
                            if result.get("Success"):
                                warning += f"⚡ Shocked for enforcement. "
                            else:
                                warning += f"⚠️ PiShock failed: {result.get('Message')} "
                        else:
                            warning += "⚠️ PiShock not configured. "

                    except Exception as e:
                        warning += f"⚠️ PiShock error: {e} "

                if "gag" in actions and shared.enforcement_gag_send:
                    c.execute("SELECT type FROM gagged_users WHERE user_id = ?", (user_id,))
                    result = c.fetchone()
                    gag_type = result[0] if result else "loose"
                    await shared.enforcement_gag_send(message, gag_type)

                c.connection.commit()
                if "timeout" in actions:
                    await message.channel.send(warning, delete_after=total_timeout)
                else:
                    await message.channel.send(warning, delete_after=120)
                return



        # ---------- BANNED WORDS ----------
        if not is_command and user_id in shared.banned_words[guild_id]:
            triggered = {w for w in shared.banned_words[guild_id][user_id] if w in content}

            if triggered:
                try:
                    await message.delete()
                except:
                    pass

                total_timeout = 0
                for word in triggered:
                    word_data = shared.banned_words[guild_id][user_id].get(word, {})
                    initial = word_data.get("initial_time", 60)
                    added = word_data.get("added_time", 30)
                    new_initial = initial + added
                    total_timeout += new_initial
                    shared.banned_words[guild_id][user_id][word]["initial_time"] = new_initial

                    c.execute(
                        "UPDATE banned_words SET initial_time = ? WHERE user_id = ? AND word = ?",
                        (new_initial, user_id, word)
                    )

                # Offense count
                offenses = shared.enforcement_offenses[guild_id].get(user_id, 0) + 1
                shared.enforcement_offenses[guild_id][user_id] = offenses
                c.execute(
                    "INSERT OR REPLACE INTO enforcement_offenses (user_id, count) VALUES (?, ?)",
                    (user_id, offenses)
                )

                actions = get_user_enforcement_action(user_id, c)
                warning = f"⛔ {message.author.mention} Triggered banned words: {', '.join(triggered)}. "

                if "timeout" in actions:
                    try:
                        await message.author.timeout(
                            datetime.now(timezone.utc) + timedelta(seconds=total_timeout)
                        )
                        expiry = datetime.now(timezone.utc) + timedelta(seconds=total_timeout)
                        timestamp = int(expiry.timestamp())  # Convert to Unix timestamp
                        warning += f"Timed out for {total_timeout}s. Will return in <t:{timestamp}:R> "
                    except Exception:
                        warning += "❌ Could not timeout. "

                if "cooldown" in actions:
                    added_cd = sum(
                        shared.banned_words[guild_id][user_id][w].get("added_time", 30)
                        for w in triggered
                    )
                    shared.cooldown_users[guild_id][user_id] = shared.cooldown_users[guild_id].get(user_id, 0) + added_cd
                    shared.last_message_times[guild_id][user_id] = time.time()

                    c.execute(
                        "INSERT OR REPLACE INTO cooldown_users (user_id, cooldown) VALUES (?, ?)",
                        (user_id, shared.cooldown_users[guild_id][user_id])
                    )

                if "pishock" in actions and shared.pishock_command:
                    try:
                        c.execute("""
                            SELECT enforcement_action_shock_intensity, enforcement_action_shock_duration
                            FROM pishock_users WHERE user_id = ?
                        """, (user_id,))
                        row = c.fetchone()

                        if row:
                            intensity, duration = row
                            result = shared.pishock_command(guild_id, user_id, op=0, intensity=intensity, duration=duration)
                            if result.get("Success"):
                                warning += f"⚡ Shocked for enforcement. "
                            else:
                                warning += f"⚠️ PiShock failed: {result.get('Message')} "
                        else:
                            warning += "⚠️ PiShock not configured. "

                    except Exception as e:
                        warning += f"⚠️ PiShock error: {e} "

                if "gag" in actions and shared.enforcement_gag_send:
                    c.execute("SELECT type FROM gagged_users WHERE user_id = ?", (user_id,))
                    result = c.fetchone()
                    gag_type = result[0] if result else "loose"
                    await shared.enforcement_gag_send(message, gag_type)

                c.connection.commit()
                if "timeout" in actions:
                    await message.channel.send(warning, delete_after=total_timeout)
                else:
                    await message.channel.send(warning, delete_after=120)
            
                return
            
        # ---------- WORD LENGTH LIMIT CHECK ----------
        if not is_command and user_id in shared.word_length_limits[guild_id]:
            limit_data = shared.word_length_limits[guild_id][user_id]
            min_len = limit_data.get("min_length", 0)
            max_len = limit_data.get("max_length", 0)

            # Skip check if nothing enforced
            if min_len > 0 or max_len > 0:
                words = [w.strip(string.punctuation) for w in content.split()]
                for w in words:
                    lw = len(w)
                    if (min_len > 0 and lw < min_len) or (max_len > 0 and lw > max_len):
                        try:
                            await message.delete()
                        except discord.NotFound:
                            pass
                        except discord.Forbidden:
                            # Prevent log spam if missing perms
                            print(f"[WordLength] Missing permission to delete message in {message.channel}")
                        except Exception as e:
                            print(f"[WordLength] Delete failed: {e}")

                        # Format clearer message
                        limit_text = []
                        if min_len > 0:
                            limit_text.append(f"≥ {min_len}")
                        if max_len > 0:
                            limit_text.append(f"≤ {max_len}")
                        rule_text = " and ".join(limit_text)

                        await message.channel.send(
                            f"{message.author.mention} ❌ Invalid word length! "
                            f"Each word must be {rule_text} characters long.",
                            delete_after=8
                        )
                        return



        # Prison check
        if shared.enforce_prison_restrictions and not is_command:
            await shared.enforce_prison_restrictions(message)
            #if shared.returnvar == True:
                #return


        # Cooldown check (per guild)
        if not is_command:
            guild_id = message.guild.id
            now = time.time()

            if user_id in shared.cooldown_users[guild_id]:
                elapsed = now - shared.last_message_times[guild_id].get(user_id, 0)

                if elapsed < shared.cooldown_users[guild_id][user_id]:
                    try:
                        await message.delete()
                        remaining = int(shared.cooldown_users[guild_id][user_id] - elapsed)
                        await message.channel.send(
                            f"⏳ {message.author.mention} Wait {remaining}s before messaging again",
                            delete_after=30
                        )
                    except:
                        pass
                    return

                shared.last_message_times[guild_id][user_id] = now

                
        # Gag check (per guild)

        if not is_command:
            guild_id = message.guild.id
            if user_id in shared.gagged_users[guild_id]:                
                try:
                    if shared.handle_gagged_message:
                        await shared.handle_gagged_message(message)
                    else:
                        print("⚠️ Gag handler not set!")
                except Exception as e:
                    await log_error_to_channels(
                        bot=bot,
                        error=e,
                        context_label=f"Gag handling failed for user {user_id}",
                        guild=message.guild,
                        user=message.author
                    )
                if shared.returnvar:
                    return




        await bot.process_commands(message)
    except Exception as e:
        tb = traceback.format_exc()

        error_text = f"[on_message_or_edit ERROR] {e}\n{tb}"

        # Use your log_error_to_channels function, adapted for message context
        try:
            await log_error_to_channels(
                bot=bot,
                error=e,
                context_label="[on_message_or_edit]",
                guild=message.guild,
                user=message.author,
                extra_info=tb
            )
        except Exception as log_exc:
            print(f"Failed to log error: {log_exc}")

        # Notify in channel with a brief message
        await message.channel.send(
            f"❌ An error occurred: `{type(e).__name__}: {e}`",
            delete_after=15
        )


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    message = reaction.message
    guild = message.guild
    if not guild:
        return

    guild_id = guild.id

    if user.id in shared.ignored_users[guild_id]:
        return

    # Optional: pause per guild
    if shared.bot_paused[guild_id]:
        return

    msg_id = message.id

    # 🔇 Gag handler
    if shared.handle_gag_reaction:
        await shared.handle_gag_reaction(reaction, user)

    # 🧨 Delete reaction by mods
    if str(reaction.emoji) == "💣" and message.author == bot.user:
        if message.guild:
            if await is_user_server_mod(bot, message.guild.id, user.id):
                try:
                    await message.delete()
                except discord.Forbidden as e:
                    await log_error_to_channels(bot, error=e, context_label="Missing permissions to delete message", guild=message.guild, user=user)
                except discord.HTTPException as e:
                    await log_error_to_channels(bot, error=e, context_label="Failed to delete message", guild=message.guild, user=user)
        return


    # 🎭 Gagged message, find real author
    target_user = message.author
    msg_dict = shared.gagged_messages.get(guild_id, {})
    if target_user.bot and msg_id in msg_dict:
        author_id, original_content, webhook_id, webhook_token = msg_dict.get(msg_id, (None, None, None, None))
        real_author = guild.get_member(author_id)
        if real_author:
            target_user = real_author

    # ⚡ PiShock reaction
    if str(reaction.emoji) == "⚡" and shared.pishock_command:
        try:
            # Fetch intensity and duration from DB
            c = shared.get_server_db(guild_id).cursor()
            c.execute("""
                SELECT lightning_reaction_shock_intensity, lightning_reaction_shock_duration
                FROM pishock_users WHERE user_id = ?
            """, (target_user.id,))
            row = c.fetchone()

            if not row:
                await message.add_reaction('❌')
                await log_error_to_channels(
                    bot,
                    error=f"No PiShock settings found for user {target_user} (ID: {target_user.id})",
                    context_label="PiShock reaction failure",
                    guild=message.guild,
                    user=message.author
                )
                return

            intensity, duration = row

            # Send the PiShock command
            result = shared.pishock_command(guild_id, target_user.id, op=0, intensity=intensity, duration=duration)
            if result.get("Success"):
                await message.add_reaction('✅')
            else:
                await message.add_reaction('❌')
                await log_error_to_channels(
                    bot,
                    error=result.get('Message'),
                    context_label="PiShock shock failed",
                    guild=message.guild,
                    user=target_user
                )

        except Exception as e:
            await log_error_to_channels(
                bot,
                error=e,
                context_label="Error processing PiShock reaction",
                guild=message.guild,
                user=target_user
                )




async def check_auth(ctx, target):


    guild_id = ctx.guild.id if ctx.guild else None
    if not guild_id:
        return False

    # Get the correct DB for this guild
    db = shared.get_server_db(guild_id)
    c = db.cursor()

    # Fetch auth level from this server's DB
    c.execute("SELECT auth_level FROM user_auth WHERE user_id = ?", (target.id,))
    result = c.fetchone()
    auth_level = result[0] if result else DEFAULT_AUTH_MODE

    # Allow owner to bypass if toggle is on
    if ctx.author.id == BOT_OWNER_ID and getattr(shared, "bypass_auth_enabled", False):
        return True


    # Public = cannot modify own
    if auth_level == "public" and target.id == ctx.author.id:
        return False
    # Off = others can't modify you
    if auth_level == "off" and target.id != ctx.author.id:
        return False
    # Ask = others must request permission
    if auth_level == "ask" and target.id != ctx.author.id:
        try:
            requestor = ctx.author
            dm_channel = await target.create_dm()
            server_info = f"**Server:** `{ctx.guild.name}`\n" if ctx.guild else "**Server:** Direct Message or Unknown\n"
            if hasattr(ctx, "message") and ctx.message:
                try:
                    await ctx.message.add_reaction("⌛")
                except discord.HTTPException:
                    pass  # Ignore if we can't react
                command_info = f"**Message:** `{ctx.message.content}`\n"
            elif hasattr(ctx, "interaction") and hasattr(ctx.interaction, "data"):
                data = ctx.interaction.data
                if isinstance(data, dict) and "options" in data:
                    opts = data["options"]
                    args_parts = []
                    for opt in opts:
                        name = opt.get("name")
                        value = opt.get("value")
                        if name and value is not None:
                            args_parts.append(f"{name}={value}")
                    args_str = " ".join(args_parts)
                    command_info = f"**Slash Command:** `/{ctx.command.qualified_name} {args_str}`\n"
                else:
                    command_info = f"**Slash Command:** `/{ctx.command.qualified_name}`\n"
            else:
                command_info = "**Command:** Unknown\n"


            prompt = await dm_channel.send(
                f"🔐 {requestor.mention} is requesting to modify your status.\n"
                f"{server_info}"
                f"{command_info}"
                f"React with ✅ to **allow** or ❌ to **deny**. You have 5 minutes."
            )
            await prompt.add_reaction("✅")
            await prompt.add_reaction("❌")

            requestor_dm = await requestor.create_dm()
            await requestor_dm.send(f"🕓 Waiting for {target.display_name} to react...")

            def reaction_check(reaction, user):
                return (
                    user.id == target.id and
                    str(reaction.emoji) in ["✅", "❌"] and
                    reaction.message.id == prompt.id
                )

            reaction, _ = await bot.wait_for('reaction_add', timeout=300.0, check=reaction_check)

            if str(reaction.emoji) == "✅":
                await requestor_dm.send(f"✅ {target.display_name} **approved** your request.")
                return True
            else:
                await requestor_dm.send(f"❌ {target.display_name} **denied** your request.")
                return False

        except asyncio.TimeoutError:
            try:
                requestor_dm = await requestor.create_dm()
                await requestor_dm.send(f"⌛ {target.display_name} did not respond in time. Request timed out.")
            except Exception as dm_error:
                await log_error_to_channels(
                    bot,
                    error=dm_error,
                    context_label="[check_auth] Failed to DM requestor on timeout",
                    guild=ctx.guild if ctx else None,
                    user=ctx.author if ctx else None
                )

            return False

    return True


# ------------------- Commands -------------------
@bot.command()
@with_config
async def enforce(ctx, user: discord.Member, *args):
    """Force a user to include specific words/phrases in messages
    Usage: !>enforce @user required phrase [initial_time] [added_time]"""
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id

    if not args or len(args) < 1:
        await ctx.message.add_reaction("❌")
        return

    reversed_args = list(args[::-1])
    initial_time = 60
    added_time = 30

    if len(reversed_args) >= 2 and reversed_args[0].isdigit() and reversed_args[1].isdigit():
        added_time = int(reversed_args[0])
        initial_time = int(reversed_args[1])
        word = " ".join(args[:-2])
    elif len(reversed_args) >= 1 and reversed_args[0].isdigit():
        added_time = int(reversed_args[0])
        word = " ".join(args[:-1])
    else:
        word = " ".join(args)

    word = word.strip().lower()
    target = user or ctx.author

    if not all(32 <= ord(c) <= 126 for c in word):
        await ctx.send("❌ The phrase contains unsupported or non-standard characters.")
        return

    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if check_word_conflict(ctx, target.id, word, "enforce"):  # optionally adjust to support guild_id
        await ctx.send("❌ That word conflicts with a banned word already set for this user.")
        return

    try:
        if target.id not in shared.enforced_words[guild_id]:
            shared.enforced_words[guild_id][target.id] = {}

        if word in shared.enforced_words[guild_id][target.id]:
            await ctx.message.add_reaction("❌")
            return

        initial_time -= added_time
        shared.enforced_words[guild_id][target.id][word] = {
            "initial_time": initial_time,
            "added_time": added_time
        }

        c.execute(
            "INSERT OR REPLACE INTO enforced_words (user_id, word, initial_time, added_time) VALUES (?, ?, ?, ?)",
            (target.id, word, initial_time, added_time)
        )
        ctx.server_db.commit()

        await ctx.message.add_reaction("🔠")
        await ctx.send(
            f"{target.mention} must now include **{word}** in all messages.\n"
            f"Timeout on violation: {initial_time + added_time} seconds.",
            delete_after=30
        )

    except Exception as e:
        await log_error_to_channels(
            bot,
            error=e,
            context_label="[enforce] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        await ctx.message.add_reaction("❌")


@bot.command()
@with_config
async def unenforce(ctx, user: discord.Member, *args):
    """Remove enforced phrase requirement for a user
    Usage: !>unenforce @user required phrase"""
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id

    if not args:
        await ctx.message.add_reaction("❌")
        return

    word = " ".join(args).strip().lower()
    target = user or ctx.author

    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    try:
        if (
            target.id not in shared.enforced_words[guild_id] or
            word not in shared.enforced_words[guild_id][target.id]
        ):
            await ctx.message.add_reaction("❌")
            await ctx.send(f"{target.mention} doesn't have **{word}** enforced.", delete_after=10)
            return

        del shared.enforced_words[guild_id][target.id][word]
        if not shared.enforced_words[guild_id][target.id]:
            del shared.enforced_words[guild_id][target.id]

        c.execute(
            "DELETE FROM enforced_words WHERE user_id = ? AND word = ?",
            (target.id, word)
        )
        ctx.server_db.commit()

        await ctx.message.add_reaction("✅")
        await ctx.send(
            f"Removed **{word}** from {target.mention}'s enforced words list.",
            delete_after=30
        )

    except Exception as e:
        await log_error_to_channels(
            bot,
            error=e,
            context_label="[Unenforce] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        await ctx.message.add_reaction("❌")


@bot.command()
@with_config
async def ban(ctx, user: discord.Member, *args):
    """Ban a user from using specific words.
    Usage: !>ban @user forbidden phrase [initial_time] [added_time]"""
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id

    if not args:
        await ctx.send("❌ You must specify a word or phrase.")
        return

    try:
        added_time = int(args[-1])
        initial_time = int(args[-2])
        word_parts = args[:-2]
    except (ValueError, IndexError):
        initial_time = 60
        added_time = 30
        word_parts = args

    word = " ".join(word_parts).strip().lower()
    if not word:
        await ctx.send("❌ Invalid word or phrase.")
        return

    target = user or ctx.author

    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The target is currently locked and cannot be modified.")
        return

    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if check_word_conflict(ctx, target.id, word, "ban"):  # optionally: pass guild_id too
        await ctx.send("❌ That word conflicts with an enforced word already set for this user.")
        return

    try:
        if target.id not in shared.banned_words[guild_id]:
            shared.banned_words[guild_id][target.id] = {}

        if word in shared.banned_words[guild_id][target.id]:
            await ctx.message.add_reaction("❌")
            return

        initial_time -= added_time  # Adjust to match your logic

        shared.banned_words[guild_id][target.id][word] = {
            "initial_time": initial_time,
            "added_time": added_time
        }

        c.execute(
            "INSERT OR REPLACE INTO banned_words (user_id, word, initial_time, added_time) VALUES (?, ?, ?, ?)",
            (target.id, word, initial_time, added_time)
        )
        ctx.server_db.commit()

        await ctx.message.add_reaction("⛔")
        await ctx.send(
            f"{target.mention} is now banned from saying **{word}**.\n"
            f"Timeout on violation: {initial_time + added_time} seconds.",
            delete_after=30
        )

    except Exception as e:
        await log_error_to_channels(
            bot,
            error=e,
            context_label="[Ban] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        await ctx.message.add_reaction("❌")


@bot.command()
@with_config
async def unban(ctx, user: discord.Member, *args):
    """Unban a word or phrase for a user.
    Usage: !>unban @user forbidden phrase"""
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id

    if not args:
        await ctx.message.add_reaction("❌")
        return

    word = " ".join(args).strip().lower()
    target = user or ctx.author

    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The target is currently locked and cannot be modified.")
        return

    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    try:
        if (
            target.id not in shared.banned_words[guild_id] or
            word not in shared.banned_words[guild_id][target.id]
        ):
            await ctx.message.add_reaction("❌")
            await ctx.send(f"{target.mention} doesn't have **{word}** banned.", delete_after=10)
            return

        del shared.banned_words[guild_id][target.id][word]
        if not shared.banned_words[guild_id][target.id]:
            del shared.banned_words[guild_id][target.id]

        c.execute("DELETE FROM banned_words WHERE user_id = ? AND word = ?", (target.id, word))
        ctx.server_db.commit()

        await ctx.message.add_reaction("✅")
        await ctx.send(
            f"Removed **{word}** from {target.mention}'s banned words list.",
            delete_after=30
        )

    except Exception as e:
        await log_error_to_channels(
            bot,
            error=e,
            context_label="[Unban] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        await ctx.message.add_reaction("❌")



def check_word_conflict(ctx, user_id: int, new_word: str, mode: str) -> bool:
    """
    Check if the new word conflicts with existing words for a user.

    Args:
        user_id (int): ID of the user.
        new_word (str): Word being added.
        mode (str): 'enforce' or 'ban' to determine which conflict to check against.
        enforced_words (dict): Per-guild enforced words.
        banned_words (dict): Per-guild banned words.

    Returns:
        bool: True if conflict exists, False otherwise.
    """
    new_word = new_word.lower()
    guild_id = ctx.guild.id
    enforced = shared.enforced_words.get(ctx.guild.id, {})
    banned = shared.banned_words.get(ctx.guild.id, {})

    if mode == "enforce":
        for banned in shared.banned_words[guild_id].get(user_id, {}):
            if banned in new_word or new_word in banned:
                return True

    elif mode == "ban":
        for enforced in shared.enforced_words[guild_id].get(user_id, {}):
            if enforced in new_word or new_word in enforced:
                return True

    return False


@bot.command(name="enforcement", aliases=["modes", "prefs"])
@with_config
async def enforcement(ctx, *args):
    c = ctx.server_db.cursor()
    """
    Manage your enforcement actions.
    Usage:
      !>enforcement                - Show your current enforcement
      !>enforcement add gag        - Add an action
      !>enforcement remove timeout - Remove an action
      !>enforcement help           - Show help
    """

    user_id = ctx.author.id

    if len(args) == 0:
        # Show current settings
        c.execute("SELECT enforcement_action FROM user_settings WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if row:
            current = row[0].split(",")
            formatted = ", ".join(f"`{action}`" for action in current if action)
            await ctx.send(f"🔧 Your current enforcement actions: {formatted}")
        else:
            await ctx.send("🔧 You have no custom enforcement settings.")
        return

    if args[0].lower() == "help":
        help_text = (
            "**Enforcement Settings Help**\n"
            "Customize what happens when you break a rule (e.g., banned or enforced words).\n\n"
            "**Available Actions:**\n"
            "- `timeout`: Temporarily mutes you\n"
            "- `gag`: Your message gets replaced\n"
            "- `cooldown`: Increases penalty timers\n\n"
            "**Usage:**\n"
            "`!>enforcement` – Show your settings\n"
            "`!>enforcement add gag` – Add an action\n"
            "`!>enforcement remove timeout` – Remove an action"
        )
        await ctx.send(help_text)
        return

    if len(args) == 2:
        subcommand = args[0].lower()
        action = args[1].lower()

        if action not in VALID_ENFORCEMENT_ACTIONS:
            await ctx.send(f"❌ Invalid action `{action}`. Use `!>settings help` to see valid options.")
            return

        # Load current actions
        c.execute("SELECT enforcement_action FROM user_settings WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        current_actions = set(row[0].split(",")) if row and row[0] else set()

        if subcommand == "add":
            if action in current_actions:
                await ctx.send(f"⚠️ `{action}` is already in your settings.")
                return
            current_actions.add(action)

        elif subcommand == "remove":
            if action not in current_actions:
                await ctx.send(f"⚠️ `{action}` is not currently in your settings.")
                return
            current_actions.remove(action)

        else:
            await ctx.send("❌ Use `add` or `remove` as the first argument.")
            return

        # Save back to DB
        updated_value = ",".join(current_actions)
        c.execute(
            "INSERT OR REPLACE INTO user_settings (user_id, enforcement_action) VALUES (?, ?)",
            (user_id, updated_value)
        )
        c.connection.commit()

        await ctx.send(f"✅ Updated settings: `{updated_value or 'none'}`")
        return

    await ctx.send("❌ Invalid usage. Try `!>enforcement help` for instructions.")

        
@bot.command()
@with_config
async def auth(ctx, mode: str):
    c = ctx.server_db.cursor()
    valid_modes = ["ask", "public", "exposed", "off"]
    mode = mode.lower()
    
    if mode not in valid_modes:
        await ctx.message.add_reaction("❌")
        return
    
    c.execute("INSERT OR REPLACE INTO user_auth VALUES (?, ?)", (ctx.author.id, mode))
    c.connection.commit()
    await ctx.message.add_reaction("✅")


 # RESUME HERE WITH PER SERVER VARIABLES

@bot.command()
@with_config
async def ignore(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    target = user or ctx.author  # If no user is mentioned, apply to the message author
    user_id = target.id

    if target.id in shared.locked_users[ctx.guild.id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # Auth check for ignoring others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    try:

        guild_id = ctx.guild.id

        # Check if the user is already ignored
        c.execute("SELECT 1 FROM ignored_users WHERE user_id = ?", (user_id,))
        row = c.fetchone()

        if row:
            # Unignore
            c.execute("DELETE FROM ignored_users WHERE user_id = ?", (user_id,))
            c.connection.commit()
            shared.ignored_users[guild_id].discard(user_id)
            await ctx.send(f"🔓 {target.mention} has been removed from the ignored users list.", delete_after=30)
        else:
            # Ignore
            c.execute("INSERT INTO ignored_users (user_id) VALUES (?)", (user_id,))
            c.connection.commit()
            shared.ignored_users[guild_id].add(user_id)
            await ctx.send(f"✅ {target.mention} has been added to the ignored users list.", delete_after=30)

    except Exception as e:
        await ctx.send(f"❌ Failed to toggle ignore status for {target.mention}: {e}")


# Add this command to toggle non-bypass enforcement
@bot.command(name="nonbypass")
@with_config
async def nonbypass_message(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()

    target = user or ctx.author
    user_id = target.id
    guild_id = ctx.guild.id

    # Auth check
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    try:
        c.execute("SELECT 1 FROM nonbypass_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        row = c.fetchone()

        if row:
            c.execute("DELETE FROM nonbypass_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            ctx.server_db.commit()
            await ctx.send(f"🔓 {target.mention} has their bypasses again")
        else:
            c.execute("INSERT INTO nonbypass_users (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
            ctx.server_db.commit()
            await ctx.send(f"✅ {target.mention} has no bypass keys")

    except Exception as e:
        await ctx.send(f"❌ Failed to toggle nonbypass status for {target.mention}: {e}")

@bot.slash_command(name="nonbypass", description="Toggle whether a user is blocked from using bypass tricks.")
@with_config
async def nonbypass_slash(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to toggle non-bypass for", required=False)
):
    c = ctx.server_db.cursor()

    target = user or ctx.author
    user_id = target.id
    guild_id = ctx.guild.id
    await ctx.defer(ephemeral=True)

    # Auth check
    if not await check_auth(ctx, target):
        await ctx.respond("❌ You don't have permission to modify this user.", ephemeral=True)
        return

    try:
        c.execute("SELECT 1 FROM nonbypass_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        row = c.fetchone()

        if row:
            c.execute("DELETE FROM nonbypass_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            ctx.server_db.commit()
            await ctx.respond(f"🔓 {target.mention} can bypass filters again.", ephemeral=False)
        else:
            c.execute("INSERT INTO nonbypass_users (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
            ctx.server_db.commit()
            await ctx.respond(f"✅ {target.mention} can no longer bypass filters.", ephemeral=False)

    except Exception as e:
        await ctx.respond(f"❌ Failed to toggle nonbypass status for {target.mention}: {e}", ephemeral=True)


@bot.slash_command(name="discord_ping", description="Ping a user or role every X minutes for Y minutes.")
@with_config
@is_server_mod()
async def discord_ping_slash(
    ctx: discord.ApplicationContext,
    interval: Option(float, "How often to ping (in minutes)"),
    duration: Option(float, "Total time to ping for (in minutes)"),
    user: Option(discord.Member, "User to ping repeatedly", required=False),
    role: Option(discord.Role, "Role to ping repeatedly", required=False),
    start_delay: Option(float, "Delay before starting pings (in seconds)", required=False, default=0)
):
    if (not user and not role) or (user and role):
        await ctx.respond("❌ You must specify *either* a user *or* a role (not both).", ephemeral=True)
        return

    if interval < 0.1 or duration <= 0 or start_delay < 0:
        await ctx.respond("❌ Interval and start delay must be non-negative, duration must be positive.", ephemeral=True)
        return

    if abs((duration / interval) - round(duration / interval)) > 1e-6:
        await ctx.respond("❌ Duration must be a multiple of the interval (e.g., 2.5 min every 0.5 min).", ephemeral=True)
        return

    guild_id = ctx.guild.id
    channel = ctx.channel

    if not hasattr(shared, "active_pings"):
        shared.active_pings = {}
    if guild_id not in shared.active_pings:
        shared.active_pings[guild_id] = {}

    if user and user.id == 1362072119844016258:  # bot ID
        user = ctx.author

    target_id = user.id if user else role.id
    target_mention = user.mention if user else role.mention

    if target_id in shared.active_pings[guild_id]:
        await ctx.respond(f"⚠️ Already pinging {target_mention}. Use `/stop_ping` to cancel.", ephemeral=True)
        return

    await ctx.respond(
        f"✅ Will start pinging {target_mention} in {start_delay} seconds(s), then every {interval} minute(s) for {duration} minute(s).",
        ephemeral=True
    )

    async def ping_loop():
        try:
            if start_delay > 0:
                await asyncio.sleep(start_delay)  # delay before starting
            end_time = discord.utils.utcnow().timestamp() + duration * 60
            while discord.utils.utcnow().timestamp() < end_time:
                await channel.send(f"Ping! ||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||||| {target_mention}")
                await asyncio.sleep(interval * 60)
        except asyncio.CancelledError:
            await channel.send(f"🛑 Stopped pinging {target_mention}.")
        finally:
            shared.active_pings[guild_id].pop(target_id, None)

    task = bot.loop.create_task(ping_loop())
    shared.active_pings[guild_id][target_id] = task


@bot.slash_command(name="stop_ping", description="Stop pinging a user or role early.")
@with_config
@is_server_mod()
async def stop_ping_slash(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to stop pinging", required=False),
    role: Option(discord.Role, "Role to stop pinging", required=False),
):
    if (not user and not role) or (user and role):
        await ctx.respond("❌ You must specify *either* a user *or* a role (not both).", ephemeral=True)
        return

    guild_id = ctx.guild.id
    target_id = user.id if user else role.id
    target_mention = user.mention if user else role.mention

    task = shared.active_pings.get(guild_id, {}).pop(target_id, None)
    if task:
        task.cancel()
        await ctx.respond(f"🛑 Stopped pinging {target_mention}.", ephemeral=True)
    else:
        await ctx.respond(f"ℹ️ {target_mention} is not currently being pinged.", ephemeral=True)



@bot.command()
@with_config
async def red(ctx):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    user_id = ctx.author.id

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    """Erase all your data from the bot's database (irreversible)"""
    try:
        # Delete from all database tables that include user_id
        c.execute("DELETE FROM gagged_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM prison_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM user_auth WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM cooldown_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM enforced_words WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM enforcement_offenses WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM banned_words WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM ignored_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM line_assignments WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM active_line_writers WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM timer_logs WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM locked_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM pishock_users WHERE user_id = ?", (user_id,))
        ctx.server_db.commit()

        # Remove from in-memory structures
        shared.gagged_users[guild_id].pop(user_id, None)
        shared.user_pishock_codes[guild_id].pop(user_id, None)
        shared.prison_users[guild_id].pop(user_id, None)
        shared.cooldown_users[guild_id].pop(user_id, None)
        shared.last_message_times[guild_id].pop(user_id, None)
        shared.enforced_words[guild_id].pop(user_id, None)
        shared.enforcement_offenses[guild_id].pop(user_id, None)
        shared.banned_words[guild_id].pop(user_id, None)
        shared.ignored_users[guild_id].discard(user_id)
        shared.line_writing_sessions[guild_id].pop(user_id, None)
        shared.locked_users[guild_id].discard(user_id)
        shared.locked_by_map[guild_id].pop(user_id, None)

        # Remove roles
        prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
        sc_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
        if sc_role in ctx.author.roles:
            await ctx.author.remove_roles(sc_role, reason="Red used")
        if prisoner_role in ctx.author.roles:
            await ctx.author.remove_roles(prisoner_role, reason="Red used")

        # Confirmation
        await ctx.message.add_reaction("♻️")
        try:
            await ctx.author.send("✅ All your data has been erased from the system.")
        except discord.Forbidden:
            pass

    except Exception as e:
        await log_error_to_channels(
            bot,
            error=e,
            context_label="[Red] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        await ctx.message.add_reaction("❌")


# ------------------- Modified Commands -------------------


@bot.command(name="sayas")
@with_config
async def sayas(ctx, user: discord.Member, *, message: str):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    config = ctx.config

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    if ctx.author.id != 1146469921270792326:
        await ctx.send("❌ You don't have permission to use this command.")
        return
    """Bot sends a message as the specified user in the current channel or thread."""
    try:
        # Identify parent and thread channels
        if isinstance(ctx.channel, discord.Thread):
            if ctx.channel.parent is None:
                await ctx.send("❌ Cannot determine parent channel of this thread.")
                return
            parent_channel = ctx.channel.parent
            thread = ctx.channel
        else:
            parent_channel = ctx.channel
            thread = None

        # Look for an existing webhook in the parent channel
        webhooks = await parent_channel.webhooks()
        webhook = next((w for w in webhooks if w.name == "GagWebhook" and w.user == bot.user), None)

        # Create webhook if not found
        if webhook is None:
            webhook = await parent_channel.create_webhook(name="GagWebhook")

        # Send message through webhook
        await webhook.send(
            content=message,
            username=user.display_name,
            avatar_url=user.display_avatar.url,
            thread=thread
        )

        await ctx.message.delete()

    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to manage webhooks here.")
    except Exception as e:
        await ctx.send(f"❌ Something went wrong: {e}")


@bot.command()
@with_config
async def prison(ctx, user: discord.Member = None, action: str = None):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    config = ctx.config

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    # Validate config
    missing_config = []

    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    if not prison_channel:
        missing_config.append(f"• Prison Channel (ID: `{PRISON_CHANNEL_ID}`) not found")

    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    if not solitary_role:
        missing_config.append(f"• Solitary Role (ID: `{SOLITARY_ROLE_ID}`) not found")

    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    if not prisoner_role:
        missing_config.append(f"• Prisoner Role (ID: `{PRISONER_ROLE_ID}`) not found")

    sc_role = ctx.guild.get_role(SC_ROLE_ID)
    if not sc_role:
        missing_config.append(f"• SC Role (ID: `{SC_ROLE_ID}`) not found")

    if missing_config:
        await ctx.send("❌ The following config items are missing or misconfigured:\n" + "\n".join(missing_config))
        return

    # Define target
    target = user or ctx.author

    # Lock protection
    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The target is currently locked and cannot be modified.")
        return

    # Solitary check
    if solitary_role in target.roles:
        await ctx.send("❌ This user is currently in solitary confinement. Use `!>solitary` to release them first.")
        return

    # Authorization check
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    # Auto-determine action if self
    if user is None:
        action = "remove" if target.id in shared.prison_users[guild_id] else "add"

    try:
        if action.lower() == "add":
            if prisoner_role not in target.roles:
                await target.add_roles(prisoner_role, reason="Entered prison")

            shared.prison_users[guild_id][target.id] = PRISON_CHANNEL_ID
            balance = get_balance(target.id, c)
            c.execute(
                "INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)",
                (target.id, PRISON_CHANNEL_ID, balance)
            )
            ctx.server_db.commit()
            await ctx.message.add_reaction("🔒")

        elif action.lower() == "remove":
            if target.id in shared.prison_users[guild_id]:
                del shared.prison_users[guild_id][target.id]

            c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
            await target.remove_roles(prisoner_role, reason="Released from prison")
            ctx.server_db.commit()
            await ctx.message.add_reaction("🔓")

        else:
            await ctx.message.add_reaction("❌")

    except Exception as e:
        await log_error_to_channels(
            bot,
            error=e,
            context_label="[Prison] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        await ctx.message.add_reaction("❌")



async def prison_helper(ctx, target: discord.Member, action: str):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    config = ctx.config

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SC_ROLE_ID)

    # Validate config roles/channels presence
    missing_config = []
    if not prison_channel:
        missing_config.append(f"• Prison Channel (ID: `{PRISON_CHANNEL_ID}`) not found")
    if not solitary_role:
        missing_config.append(f"• Solitary Role (ID: `{SOLITARY_ROLE_ID}`) not found")
    if not prisoner_role:
        missing_config.append(f"• Prisoner Role (ID: `{PRISONER_ROLE_ID}`) not found")
    if not sc_role:
        missing_config.append(f"• SC Role (ID: `{SC_ROLE_ID}`) not found")

    if missing_config:
        return False, "Config error:\n" + "\n".join(missing_config)

    # Lock protection
    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        return False, "The target is currently locked and cannot be modified."

    # Solitary check
    if solitary_role in target.roles:
        return False, "This user is currently in solitary confinement. Use `!>solitary` to release them first."

    # Authorization check
    if not await check_auth(ctx, target):
        return False, "You are not authorized to modify this user."

    try:
        if action.lower() == "add":
            if prisoner_role not in target.roles:
                await target.add_roles(prisoner_role, reason="Entered prison")

            shared.prison_users[guild_id][target.id] = PRISON_CHANNEL_ID
            balance = get_balance(target.id, c)
            c.execute(
                "INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)",
                (target.id, PRISON_CHANNEL_ID, balance)
            )
            
            ctx.server_db.commit()
            return True, "Added prisoner role."

        elif action.lower() == "remove":
            if target.id in shared.prison_users[guild_id]:
                del shared.prison_users[guild_id][target.id]

            c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
            await target.remove_roles(prisoner_role, reason="Released from prison")
            ctx.server_db.commit()
            return True, "Removed prisoner role."

        else:
            return False, "Invalid action."

    except Exception as e:
        await log_error_to_channels(
            bot,
            error=e,
            context_label="[Prison Helper] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        return False, "An error occurred."



@bot.command(name="track_prisoners")
@with_config
@is_server_mod()
async def track_prisoners(ctx):

    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    config = ctx.config

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    guild = ctx.guild
    if not guild:
        await ctx.send("This command can only be used in a server.", ephemeral=True)
        return

    if not config:
        await ctx.send("No config found for this server.", ephemeral=True)
        return


    solitary_role = guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = guild.get_role(PRISONER_ROLE_ID)
    prison_channel = guild.get_channel(PRISON_CHANNEL_ID)

    if not prisoner_role:
        await ctx.send("Prisoner role not found in this server.", ephemeral=True)
        return

    added_count = 0
    for member in guild.members:
        has_prisoner = prisoner_role in member.roles
        has_solitary = solitary_role and solitary_role in member.roles

        # Skip if neither role is present
        if not (has_prisoner or has_solitary):
            continue

        if member.id not in shared.prison_users[guild.id]:
            # Assign them to the appropriate channel/thread
            if has_solitary:
                display_name = member.nick or member.display_name
                # Create a solitary thread (if not already handled elsewhere)
                thread = await prison_channel.create_thread(
                    name=f"Solitary: {display_name}",
                    type=discord.ChannelType.public_thread,
                    reason="Auto-tracked solitary user"
                )

                shared.solitary_confinement[guild.id][member.id] = thread.id
                shared.prison_users[guild.id][member.id] = thread.id

                c.execute("INSERT OR REPLACE INTO solitary_confinement (user_id, thread_id, archive_date) VALUES (?, ?, ?)",
                          (member.id, thread.id, None))
                c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)",
                          (member.id, thread.id, get_balance(member.id, c)))

                await thread.send(f"{member.mention} was auto-tracked into solitary confinement.")
            else:
                # Normal prisoner
                success, msg = await prison_helper(ctx, member, "add")
                if not success:
                    await log_error_to_channels(
                        bot,
                        error=Exception(f"[track_prisoners] Failed to add {member.name}: {msg}"),
                        context_label="track_prisoners",
                        guild=guild,
                        user=member
                    )
                    continue


            added_count += 1

    await ctx.send(f"Tracked {added_count} prisoner(s) in this server.")
    c.connection.close()





@bot.command()
@with_config
async def cooldown(ctx, user: discord.Member = None, seconds: int = None):
    """Toggle cooldown or set a cooldown for another user"""
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    target = user or ctx.author

    # 🔐 Auth Check
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The target is currently locked and cannot be modified.")
        return

    if seconds is not None and seconds > 9223372036854775807:
        await ctx.message.add_reaction("❌")
        return

    # 🧠 Toggle logic for self
    if user is None and seconds is None:
        seconds = 0 if target.id in shared.cooldown_users[guild_id] else 30

    if seconds is None or seconds < 0:
        await ctx.message.add_reaction("❌")
        return

    if seconds == 0:
        # 🗑️ Remove cooldown
        if target.id in shared.cooldown_users[guild_id]:
            del shared.cooldown_users[guild_id][target.id]
        shared.last_message_times[guild_id].pop(target.id, None)
        c.execute("DELETE FROM cooldown_users WHERE user_id = ?", (target.id,))
    else:
        # 💾 Set cooldown
        shared.cooldown_users[guild_id][target.id] = seconds
        shared.last_message_times[guild_id][target.id] = 0
        c.execute("INSERT OR REPLACE INTO cooldown_users (user_id, cooldown) VALUES (?, ?)", 
                  (target.id, seconds))

    ctx.server_db.commit()
    await ctx.message.add_reaction("⏱️")


@bot.command(name="solitary")
@with_config
async def solitary(ctx, user: discord.Member = None, action: str = None):
    """Toggle solitary confinement or manage another user."""
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    target = user or ctx.author

    # 🔐 Lock check
    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The target is currently locked and cannot use this command.")
        return

    # 🧑‍⚖️ Auth check
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    # 🏛️ Get roles/channels
    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SOLITARY_ROLE_ID)

    if not all([prison_channel, prisoner_role, sc_role]):
        await ctx.message.add_reaction("❌")
        return

    if user is None:
        action = "remove" if target.id in shared.solitary_confinement[guild_id] else "add"
    else:
        action = action.lower() if action else "add"

    try:
        if action == "add":
            # Add roles
            if prisoner_role not in target.roles:
                await target.add_roles(prisoner_role, reason="Entered solitary")
            if sc_role not in target.roles:
                await target.add_roles(sc_role, reason="Entered solitary")

            # Check existing thread
            c.execute("SELECT thread_id FROM solitary_confinement WHERE user_id = ?", (target.id,))
            row = c.fetchone()
            existing_thread = None

            if row:
                try:
                    existing_thread = await ctx.guild.fetch_channel(row[0])
                except discord.NotFound:
                    existing_thread = None

            if existing_thread:
                display_name = target.nick or target.display_name
                thread_name = f"Solitary: {display_name}"

                await existing_thread.edit(
                    archived=False,
                    locked=False,
                    name=thread_name
                )
                shared.solitary_confinement[guild_id][target.id] = existing_thread.id
                shared.prison_users[guild_id][target.id] = existing_thread.id

                c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)", 
                          (target.id, existing_thread.id, get_balance(target.id, c)))
                await existing_thread.send(f"{target.mention} has re-entered solitary confinement.")
            else:
                display_name = target.nick or target.display_name
                # Create new thread
                new_thread = await prison_channel.create_thread(
                    name=f"Solitary: {display_name}",
                    type=discord.ChannelType.public_thread,
                    reason=f"Solitary confinement for {target.display_name}"
                )

                shared.solitary_confinement[guild_id][target.id] = new_thread.id
                shared.prison_users[guild_id][target.id] = new_thread.id

                c.execute("INSERT OR REPLACE INTO solitary_confinement (user_id, thread_id, archive_date) VALUES (?, ?, ?)",
                          (target.id, new_thread.id, None))
                c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)", 
                          (target.id, new_thread.id, get_balance(target.id, c)))

                await new_thread.send(f"{target.mention} has entered solitary confinement.")

            ctx.server_db.commit()
            await ctx.message.add_reaction("🔒")

        elif action == "remove":
            if target.id not in shared.solitary_confinement[guild_id]:
                await ctx.message.add_reaction("❌")
                return

            thread_id = shared.solitary_confinement[guild_id][target.id]
            try:
                thread = await ctx.guild.fetch_channel(thread_id)
                await thread.send(f"{target.mention} has been released from solitary.")
                await thread.send("It's mighty lonely in here.")
                await thread.send("Don't chat here, the silly prisoner won't see it!")
                await thread.edit(archived=True, locked=True)
            except Exception:
                pass

            c.execute("UPDATE solitary_confinement SET archive_date = ? WHERE user_id = ?", (datetime.now(), target.id))
            c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
            ctx.server_db.commit()

            
            shared.prison_users[guild_id].pop(target.id, None)

            await target.remove_roles(prisoner_role, sc_role, reason="Released from solitary")
            await ctx.message.add_reaction("🔓")

        else:
            await ctx.message.add_reaction("❌")

    except discord.Forbidden:
        await ctx.send("❌ Missing permissions.")
        await ctx.message.add_reaction("❌")
    except Exception as e:
        await log_error_to_channels(
            bot=bot,
            error=e,
            context_label="[solitary] Error",
            guild=ctx.guild,
            user=ctx.author
        )
        await ctx.message.add_reaction("❌")


@bot.command(name="lockdown")
@with_config
@is_server_mod()
async def lockdown(ctx, member: discord.Member, channel: discord.TextChannel):
    #Restrict a user’s visibility to a single channel
    c = ctx.server_db.cursor()
    guild = ctx.guild


    # 🔐 Auth check
    if not await check_auth(ctx, member):
        await ctx.message.add_reaction("❌")
        return

    if member.id in shared.locked_users[guild.id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ This user is already locked and you are not authorized to modify them.")
        return

    # 🚫 Deny access to all channels
    failures = []
    for ch in guild.channels:
        try:
            overwrite = ch.overwrites_for(member)
            overwrite.view_channel = False
            await ch.set_permissions(member, overwrite=overwrite)
        except discord.Forbidden:
            failures.append(ch.name)

    # ✅ Allow access to the specified channel
    try:
        allow_overwrite = channel.overwrites_for(member)
        allow_overwrite.view_channel = True
        await channel.set_permissions(member, overwrite=allow_overwrite)
    except discord.Forbidden:
        await ctx.send(f"❌ Failed to allow access to {channel.mention}.")
        return

    # 🧠 Store the lock
    shared.locked_users[guild.id].add(member.id)
    shared.locked_by_map[guild.id][member.id] = ctx.author.id
    c.execute("INSERT OR REPLACE INTO locked_users (user_id, locked_by) VALUES (?, ?)", (member.id, ctx.author.id))
    ctx.server_db.commit()

    # ✅ Confirmation
    await ctx.send(f"🔒 {member.mention} has been locked to {channel.mention}")
    if failures:
        print(f"⚠️ Could not update permissions for: {', '.join(failures)}")


@bot.command(name="unlockdown")
@with_config
async def unlockdown(ctx, member: discord.Member):
    """Restore a user's channel access after lockdown"""
    c = ctx.server_db.cursor()
    guild = ctx.guild

    if not await check_auth(ctx, member):
        await ctx.message.add_reaction("❌")
        return

    # 🧹 Reset permissions in all channels
    failures = []
    for ch in guild.channels:
        try:
            await ch.set_permissions(member, overwrite=None)
        except discord.Forbidden:
            failures.append(ch.name)

    # 🧠 Remove from memory tracking
    shared.locked_users[guild.id].discard(member.id)
    shared.locked_by_map[guild.id].pop(member.id, None)

    # 🗃️ Remove from database
    c.execute("DELETE FROM locked_users WHERE user_id = ?", (member.id,))
    ctx.server_db.commit()

    # ✅ Confirmation
    await ctx.send(f"🔓 {member.mention} has been released from lockdown.")
    if failures:
        await ctx.send(f"⚠️ Couldn't reset permissions for: {', '.join(failures)}")


# ------------------- Assignment Commands -------------------

@bot.command(aliases=["lines"])
@with_config
async def assign_lines(ctx, user: discord.Member = None, lines: str = None, *, args: str = None):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    channel_id = ctx.channel.id
    target = user or ctx.author

    # Progress check
    if channel_id == LINE_WRITING_ID and lines is None and args is None:
        c.execute("SELECT line, lines_required, lines_written FROM active_line_writers WHERE user_id = ?", (target.id,))
        result = c.fetchone()

        if result:
            line_text, lines_left, lines_written = result
            await ctx.send(f"📝 {ctx.author.mention}, {target.mention} has made **{lines_written}/{lines_left}** progress to their line writing.\n"
                           f"Line to write: \"{line_text}\"")
        else:
            await ctx.send(f"✅ {ctx.author.mention}, {target.mention} doesn’t have any active line assignments.")
        return

    # Clear logic
    if lines == "clear" or lines.strip().lower() == "clear":
        parts = args.split()
        if len(parts) < 1 or not parts[0].isdigit():
            await ctx.send("❌ Usage: `!>lines @user clear [assignment_id]`")
            await ctx.message.add_reaction("❌")
            return

        assignment_id = int(parts[0])
        c.execute("SELECT assigned_by FROM line_assignments WHERE assignment_id = ? AND user_id = ?", (assignment_id, target.id))
        result = c.fetchone()

        if not result:
            c.execute("SELECT assigned_by FROM active_line_writers WHERE assignment_id = ? AND user_id = ?", (assignment_id, target.id))
            result = c.fetchone()

        if not result:
            await ctx.send("❌ No such assignment found.")
            await ctx.message.add_reaction("❌")
            return

        assigned_by = result[0]
        if ctx.author.id == target.id and False:
            await ctx.message.add_reaction("❌")
            await ctx.send("❌ You cannot clear your own lines.")
            return
        is_mod = await is_user_server_mod(ctx.bot, ctx.guild.id, ctx.author.id)
        if ctx.author.id != assigned_by and not is_mod:
            await ctx.message.add_reaction("❌")
            await ctx.send("❌ You can only clear lines you assigned, unless you are a bot Mod.")
            return

        c.execute("DELETE FROM line_assignments WHERE assignment_id = ?", (assignment_id,))
        c.execute("DELETE FROM active_line_writers WHERE assignment_id = ?", (assignment_id,))
        ctx.server_db.commit()

        shared.line_writing_sessions[guild_id].pop(target.id, None)
        await ctx.send(f"✅ Cleared line assignment with ID {assignment_id} for {target.mention}.")
        return

    # Assignment creation
    if not user or lines is None or not args:
        await ctx.message.add_reaction("❌")
        await ctx.send("❌ Usage: `!>lines @user #lines \"line text\" [penalty]`")
        return

    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    try:
        parts = args.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            raw_text = parts[0].strip()
            penalty = int(parts[1])
        else:
            raw_text = args.strip()
            penalty = 0

        text = ''.join(c for c in raw_text if c in string.printable)
        invalid_reason = await is_invalid_line_text(text)
        if invalid_reason:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"❌ Invalid line: {invalid_reason}")
            return

        if text != raw_text:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"❌ Don't use non-printable characters in the line text.")
            await ctx.send(f"Clean text: {text}")
            return

        try:
            lines = int(lines)
        except ValueError:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"❌ Lines assigned must be an integer.")
            return

        c.execute("""
            INSERT OR REPLACE INTO line_assignments (user_id, line, lines_required, penalty_lines, assigned_by)
            VALUES (?, ?, ?, ?, ?)
        """, (user.id, text, lines, penalty, ctx.author.id))
        ctx.server_db.commit()

        assignment_id = c.lastrowid
        await ctx.message.add_reaction("✅")

        writing_channel = ctx.guild.get_channel(LINE_WRITING_ID)
        if not writing_channel:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"❌ The designated line writing channel does not exist or is not accessible.")
            return

        await writing_channel.send(
            f"✍️ {user.mention} has been assigned to write: **{text}**\n"
            f"**{lines} times**. Penalty for mistakes: **+{penalty}** lines.\n"
            f"Use '!>start {assignment_id}' to begin! Remember don't speak anywhere else until you are done!"
        )

    except Exception as e:
        await ctx.message.add_reaction("❌")
        await ctx.send(f"❌ Failed to assign lines: {e}")


@bot.slash_command(name="lines", description="Assign or check line-writing punishment.")
@with_config
async def assign_lines_slash(
    ctx,
    user: Option(discord.Member, "The user to assign lines to", required=False, default=None),
    lines: Option(int, "Number of lines to write", required=False, default=None),
    line_text: Option(str, "The text to be written", required=False, default=None),
    penalty: Option(int, "Penalty lines for mistakes", required=False, default=0),
    assignment_id: Option(int, "Assignment ID to clear", required=False, default=None),
):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    config = unpack_config(ctx)
    LINE_WRITING_ID = config[1]  # Index 1 is LINE_WRITING_ID
    
    # Case 1: Clear assignment
    if assignment_id is not None:
        target = user or ctx.author
        c.execute("""
            SELECT assigned_by 
            FROM line_assignments 
            WHERE assignment_id = ? AND user_id = ?
            UNION
            SELECT assigned_by 
            FROM active_line_writers 
            WHERE assignment_id = ? AND user_id = ?
        """, (assignment_id, target.id, assignment_id, target.id))
        result = c.fetchone()

        if not result:
            await ctx.respond("❌ No such assignment found.", ephemeral=True)
            return

        assigned_by = result[0]
        is_mod = await is_user_server_mod(ctx.bot, guild_id, ctx.author.id)
        
        if ctx.author.id != assigned_by and not is_mod:
            await ctx.respond(
                "❌ You can only clear lines you assigned, unless you are a bot Mod.",
                ephemeral=True
            )
            return

        c.execute("DELETE FROM line_assignments WHERE assignment_id = ?", (assignment_id,))
        c.execute("DELETE FROM active_line_writers WHERE assignment_id = ?", (assignment_id,))
        ctx.server_db.commit()

        # Clear from shared sessions if exists
        shared.line_writing_sessions[guild_id].pop(target.id, None)
        await ctx.respond(f"✅ Cleared line assignment with ID {assignment_id} for {target.mention}.")
        return

    # Case 2: Assign new lines
    if lines is not None and line_text is not None:
        target = user or ctx.author
        
        if not await check_auth(ctx, target):
            await ctx.respond("❌ You are not authorized to assign lines.", ephemeral=True)
            return

        # Validate line text
        invalid_reason = await is_invalid_line_text(line_text)
        if invalid_reason:
            await ctx.respond(f"❌ Invalid line: {invalid_reason}", ephemeral=True)
            return
        
        # Check for non-printable characters
        cleaned_text = ''.join(c for c in line_text if c in string.printable)
        if cleaned_text != line_text:
            await ctx.respond(
                "❌ Don't use non-printable characters in the line text.\n"
                f"Clean text: {cleaned_text}",
                ephemeral=True
            )
            return

        # Create new assignment
        try:
            c.execute("""
                INSERT INTO line_assignments (
                    user_id, line, lines_required, penalty_lines, assigned_by
                ) VALUES (?, ?, ?, ?, ?)
            """, (target.id, cleaned_text, lines, penalty, ctx.author.id))
            ctx.server_db.commit()
            
            assignment_id = c.lastrowid
            writing_channel = ctx.guild.get_channel(LINE_WRITING_ID)
            
            if not writing_channel:
                await ctx.respond(
                    "❌ The designated line writing channel does not exist or is not accessible.",
                    ephemeral=True
                )
                return

            await writing_channel.send(
                f"✍️ {target.mention} has been assigned to write: **{cleaned_text}**\n"
                f"**{lines} times**. Penalty for mistakes: **+{penalty}** lines.\n"
                f"Use '!>start {assignment_id}' to begin! Remember don't speak anywhere else until done!"
            )
            await ctx.respond("✅ Lines assigned successfully!", ephemeral=True)
            
        except Exception as e:
            await ctx.respond(f"❌ Failed to assign lines: {e}", ephemeral=True)
        return

    # Case 3: Check progress (default action)
    target = user or ctx.author
    c.execute("""
        SELECT line, lines_required, lines_written 
        FROM active_line_writers 
        WHERE user_id = ?
    """, (target.id,))
    result = c.fetchone()

    if result:
        line_text, lines_left, lines_written = result
        await ctx.respond(
            f"📝 {target.mention}'s progress: **{lines_written}/{lines_left}** lines written.\n"
            f"Line: \"{line_text}\""
        )
    else:
        await ctx.respond(f"✅ {target.mention} doesn't have any active line assignments.")

@bot.command()
@with_config
async def start(ctx, assignment_id: int):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    user = ctx.author

    if ctx.channel.id != LINE_WRITING_ID:
        await ctx.send(f"{user.mention}, this command can only be used in the designated line writing channel.")
        return

    c.execute("SELECT * FROM active_line_writers WHERE user_id = ?", (user.id,))
    if c.fetchone():
        await ctx.send(f"{user.mention}, you are already writing an assignment! Finish it before starting another.")
        return

    c.execute("SELECT line, lines_required, penalty_lines, assigned_by, assignment_id FROM line_assignments WHERE assignment_id = ? AND user_id = ?", (assignment_id, user.id))
    result = c.fetchone()

    if not result:
        await ctx.send(f"{user.mention}, no active assignment found for assignment ID {assignment_id}.")
        return

    line_text, lines_required, penalty_lines, assigned_by, assignment_id = result

    c.execute("""
        INSERT OR IGNORE INTO active_line_writers 
        (user_id, line, lines_required, lines_written, penalty_lines, assignment_id, assigned_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user.id, line_text, lines_required, 0, penalty_lines, assignment_id, assigned_by))

    c.execute("DELETE FROM line_assignments WHERE assignment_id = ? AND user_id = ?", (assignment_id, user.id))
    ctx.server_db.commit()

    shared.line_writing_sessions[guild_id][user.id] = {
        "line": line_text,
        "lines_required": lines_required,
        "lines_written": 0
    }

    await ctx.send(f"📝 {user.mention}, your line writing has started!\n"
                   f"Write this line **{lines_required} times**:\n> `{line_text}`")


@bot.command()
@with_config
async def task(ctx, sub: discord.Member):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    dom = ctx.author

    try:
        await dom.send(f"📋 Let's set up the task for {sub.display_name}.")
        await dom.send("What is the task name?")

        def check_dm(m):
            return m.author == dom and isinstance(m.channel, discord.DMChannel)

        task_name_msg = await bot.wait_for('message', check=check_dm, timeout=120)
        task_name = task_name_msg.content

        await dom.send("Please describe the task:")
        task_desc_msg = await bot.wait_for('message', check=check_dm, timeout=180)
        task_desc = task_desc_msg.content
        await dom.send(f"Sent to {sub.display_name}:\n**Task**: {task_name}\n**Description**: {task_desc}")

        sub_dm = await sub.create_dm()
        prompt = (f"📬 You have a new task from {dom.display_name}:"
                  f"**Task**: {task_name}"
                  f"**Description**: {task_desc}"
                  f"React with ✅ to accept or ❌ to decline.")
        confirmation_msg = await sub_dm.send(prompt)
        await confirmation_msg.add_reaction("✅")
        await confirmation_msg.add_reaction("❌")

        def reaction_check(reaction, user):
            return user.id == sub.id and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirmation_msg.id

        reaction, _ = await bot.wait_for('reaction_add', check=reaction_check, timeout=300)

        if str(reaction.emoji) == "✅":
            taskchannel = ctx.guild.get_channel(TASK_CHANNEL_ID)
            await taskchannel.send(f"📢 {sub.mention} has accepted the task '**{task_name}**': {task_desc}")
        else:
            await dom.send(f"❌ {sub.display_name} declined the task.")

    except asyncio.TimeoutError:
        await dom.send("⏰ Task setup or response timed out.")



@bot.command(name="my_assignments")
@with_config
async def my_assignments(ctx):
    c = ctx.server_db.cursor()
    author_id = ctx.author.id

    embed = discord.Embed(
        title="📝 Your Assigned Lines",
        description="Here are the users you've assigned lines to:",
        color=0xffcc00
    )

    # Get not-yet-started assignments from line_assignments
    c.execute("""
        SELECT assignment_id, user_id, line, lines_required 
        FROM line_assignments 
        WHERE assigned_by = ?
    """, (author_id,))
    pending_rows = c.fetchall()

    # Get active assignments from active_line_writers
    c.execute("""
        SELECT assignment_id, user_id, line, lines_required, lines_written 
        FROM active_line_writers 
        WHERE assigned_by = ?
    """, (author_id,))
    active_rows = c.fetchall()

    if not pending_rows and not active_rows:
        await ctx.send("✅ You haven't assigned any active lines.")
        return

    # Add active assignments
    for assignment_id, user_id, line_text, lines_required, lines_written in active_rows:
        user = ctx.guild.get_member(user_id)
        name = user.display_name if user else f"<Unknown User {user_id}>"
        lines_left = max(lines_required, 0)
        status = "✍️ Active" if lines_left > 0 else "✅ Completed"
        embed.add_field(
            name=f"Assignment ID: {assignment_id} — {name} — {status}",
            value=f"Progress: **{lines_written}/{lines_left}**\nLine: `{line_text}`",
            inline=False
        )

    # Add pending assignments
    for assignment_id, user_id, line_text, lines_required in pending_rows:
        user = ctx.guild.get_member(user_id)
        name = user.display_name if user else f"<Unknown User {user_id}>"
        embed.add_field(
            name=f"Assignment ID: {assignment_id} — {name} — ⌛ Not Started",
            value=f"Lines left: **{lines_required}**\nLine: `{line_text}`",
            inline=False
        )

    await ctx.send(embed=embed)





# ------------------- Bypass User Command -------------------
@bot.command(name="allow", aliases=["bypass"])
@with_config
@is_server_mod()
async def allow(ctx, user: discord.Member):
    c = ctx.server_db.cursor()
    # Check mod role or specific allowed user ID


    user_id = user.id
    channel_id = ctx.channel.id

    # Check if already allowed
    c.execute("SELECT 1 FROM allowed_users WHERE user_id = ? AND channel_id = ?", (user_id, channel_id))
    exists = c.fetchone()

    if exists:
        # Toggle OFF
        c.execute("DELETE FROM allowed_users WHERE user_id = ? AND channel_id = ?", (user_id, channel_id))
        await ctx.send(f"❌ {user.mention} is no longer allowed to bypass in this channel.")
    else:
        # Toggle ON
        c.execute("INSERT INTO allowed_users (user_id, channel_id) VALUES (?, ?)", (user_id, channel_id))
        await ctx.send(f"✅ {user.mention} is now allowed to bypass in this channel.")

    c.connection.commit()

@bot.command(name="allow_list")
@with_config
async def allow_list(ctx, user: discord.Member):
    c = ctx.server_db.cursor()
    # Query allowed channels for the user
    c.execute("SELECT channel_id FROM allowed_users WHERE user_id = ?", (user.id,))
    rows = c.fetchall()

    if not rows:
        await ctx.send(f"📭 {user.mention} is not allowed to bypass in any channels.")
        return

    # Format channel mentions
    channel_mentions = []
    for (channel_id,) in rows:
        channel = ctx.guild.get_channel(channel_id)
        if channel:
            channel_mentions.append(f"<#{channel_id}>")
        else:
            channel_mentions.append(f"[Unknown Channel ID: {channel_id}]")

    # Send result
    channel_list = ", ".join(channel_mentions)
    await ctx.send(f"📄 {user.mention} is allowed to bypass in the following channels:\n{channel_list}")



# ------------------- Gambling -------------------

# Global variable for card counting and deck simulation
# Players list and other necessary variables



running_count = 0


SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
VALUES = {str(i): i for i in range(2, 11)}
VALUES.update({'J': 10, 'Q': 10, 'K': 10, 'A': 11})

# Global variable to track the remaining cards in the deck
total_deck_size = 8 * 52  # 8 decks, each with 52 cards
remaining_cards = total_deck_size  # Start with the full deck
deck = []  # Initialize deck as an empty list


@bot.command(name="leaderboard")
@with_config
async def leaderboard(ctx):
    c = ctx.server_db.cursor()
    # Get the top 10 users with the highest balance
    c.execute("SELECT user_id, balance FROM user_wallets ORDER BY balance DESC, user_id ASC LIMIT 10")
    rows = c.fetchall()

    if not rows:
        await ctx.send("❌ No users with balances found.")
        return

    # Format the leaderboard
    leaderboard_embed = discord.Embed(
        title="💰 Top 10 Leaderboard",
        description="Here are the top 10 users with the highest pet buck balances:",
        color=0xFFD700  # Gold color
    )

    # Get the guild (server) for this context
    guild = ctx.guild

    for rank, (user_id, balance) in enumerate(rows, start=1):
        try:
            # Fetch the user object to get the server nickname
            member = guild.get_member(user_id)

            # If the user is not in the server, handle that
            if member is None:
                leaderboard_embed.add_field(
                    name=f"{rank}. Unknown User",
                    value=f"**{balance} pet bucks**",
                    inline=False
                )
            else:
                # Use the server nickname (display_name)
                leaderboard_embed.add_field(
                    name=f"{rank}. {member.display_name}",
                    value=f"**{balance} pet bucks**",
                    inline=False
                )
        except Exception as e:
            print(f"Error fetching member: {e}")
            leaderboard_embed.add_field(
                name=f"{rank}. Unknown User",
                value=f"**{balance} pet bucks**",
                inline=False
            )

    await ctx.send(embed=leaderboard_embed)

# Command to place a bet
@bot.command(name='bet')
@with_config
async def bet(ctx, opponent: discord.Member, amount: int, game: str):
    c = ctx.server_db.cursor()
    """Place a bet against another user"""
    
    # Check if the initiator has enough pet bucks
    user_id = ctx.author.id
    c.execute("SELECT balance FROM user_wallets WHERE user_id=?", (user_id,))
    result = c.fetchone()
    
    if not result:
        await ctx.send("You don't have a wallet yet. Please type !>balance to set up your wallet.")
        return
    
    user_balance = result[0]
    
    if user_balance < amount:
        await ctx.send("You don't have enough pet bucks for this bet.")
        return
    
    # Check if the opponent has enough pet bucks
    c.execute("SELECT balance FROM user_wallets WHERE user_id=?", (opponent.id,))
    opponent_result = c.fetchone()
    
    if not opponent_result:
        await ctx.send(f"{opponent.display_name} doesn't have a wallet. Please type !>balance to set up your wallet.")
        return
    
    opponent_balance = opponent_result[0]
    
    if opponent_balance < amount:
        await ctx.send(f"{opponent.display_name} doesn't have enough pet bucks for this bet.")
        return

    # Send a message to the opponent to confirm or deny the bet
    bet_message = await ctx.send(f"{opponent.mention}, {ctx.author.display_name} has challenged you to a bet of {amount} pet bucks on the game '{game}'. Do you accept? React with ✅ for yes or ❌ for no, or type 'yes' or 'no' within 30 seconds.")
    
    # Add reaction buttons (check and cross)
    await bet_message.add_reaction('✅')
    await bet_message.add_reaction('❌')
    
    def check_reaction(reaction, user):
        return user == opponent and str(reaction.emoji) in ['✅', '❌'] and reaction.message.id == bet_message.id
    
    def check_message(message):
        return message.author == opponent and message.content.lower() in ['yes', 'no'] and message.channel == ctx.channel
    
    try:
        # Wait for either a reaction or message response (within 30 seconds)
        reaction, _ = await bot.wait_for('reaction_add', check=check_reaction, timeout=30.0)
    except asyncio.TimeoutError:
        await ctx.send("Bet not confirmed. The time limit has expired.")
        return
    
    if str(reaction.emoji) == '✅' or (await bot.wait_for('message', check=check_message, timeout=30.0)).content.lower() == 'yes':
        # Proceed with the bet if accepted
        c.execute('''INSERT INTO bets (initiator_id, opponent_id, amount, game) VALUES (?, ?, ?, ?)''', (user_id, opponent.id, amount, game))
        c.connection.commit()

        # Deduct the pet bucks from the initiator
        new_balance = user_balance - amount
        c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_balance, user_id))
        c.connection.commit()

        # Deduct pet bucks from the opponent
        new_opponent_balance = opponent_balance - amount
        c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_opponent_balance, opponent.id))
        c.connection.commit()

        await ctx.send(f"Bet placed! {ctx.author.display_name} has wagered {amount} pet bucks against {opponent.display_name} on the game '{game}'.")
    else:
        await ctx.send(f"{opponent.display_name} has declined the bet.")

@bot.command(name='bet_result')
@with_config
async def bet_result(ctx, game: str, winner: discord.Member):
    c = ctx.server_db.cursor()
    """Settle the bet and payout the winner after confirmation"""
    
    # Fetch the bet details from the database
    c.execute('SELECT * FROM bets WHERE game = ? AND status = "pending"', (game,))
    bet = c.fetchone()

    if not bet:
        await ctx.send("No active bet found for this game.")
        return

    bet_id, initiator_id, opponent_id, amount, game_name, status, created_at = bet

    # Determine loser
    if winner.id == initiator_id:
        loser_id = opponent_id
    elif winner.id == opponent_id:
        loser_id = initiator_id
    else:
        await ctx.send(f"{winner.mention} is not a participant in this bet.")
        return

    loser = await ctx.guild.fetch_member(loser_id)

    # Send a message asking for confirmation
    confirm_msg = await ctx.send(
        f"🏁 {ctx.author.mention} has reported that {winner.mention} won the bet against {loser.mention} "
        f"for the game **{game}**. React with ✅ to confirm."
    )
    await confirm_msg.add_reaction("✅")

    def check(reaction, user):
        return (
            reaction.message.id == confirm_msg.id and
            str(reaction.emoji) == "✅" and
            user.id != ctx.author.id and  # Must not be the same user who called the command
            not user.bot
        )

    try:
        # Wait for a reaction from someone else
        reaction, user = await bot.wait_for('reaction_add', timeout=60.0, check=check)

        # Update bet status
        c.execute('UPDATE bets SET status = "completed" WHERE id = ?', (bet_id,))
        c.connection.commit()

        # Credit the winner
        c.execute('SELECT balance FROM user_wallets WHERE user_id = ?', (winner.id,))
        winner_balance = c.fetchone()[0]
        new_winner_balance = winner_balance + (2 * amount)
        c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_winner_balance, winner.id))
        c.connection.commit()

        await ctx.send(f"✅ Bet settled! {winner.mention} vs {loser.mention}. {winner.mention} wins {amount} pet bucks!")

    except asyncio.TimeoutError:
        await ctx.send("⌛ Confirmation timeout. Bet result not recorded.")


@bot.command(name='cancel_bet')
@with_config
@is_server_mod()
async def cancel_bet(ctx, game: str):
    c = ctx.server_db.cursor()
    """Cancel a pending bet and refund both players their bet amount."""
    
    # Check if the user is an admin (can be adjusted based on your roles)

    
    # Fetch the bet details from the database based on the game
    c.execute('''SELECT * FROM bets WHERE game = ? AND status = 'pending' ''', (game,))
    bet = c.fetchone()
    
    if not bet:
        await ctx.send("No active bet found for this game.")
        return
    
    bet_id, initiator_id, opponent_id, amount, game, status, created_at = bet
    
    # Refund the initiator
    c.execute('SELECT balance FROM user_wallets WHERE user_id = ?', (initiator_id,))
    initiator_balance = c.fetchone()[0]
    new_initiator_balance = initiator_balance + amount
    c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_initiator_balance, initiator_id))
    
    # Refund the opponent
    c.execute('SELECT balance FROM user_wallets WHERE user_id = ?', (opponent_id,))
    opponent_balance = c.fetchone()[0]
    new_opponent_balance = opponent_balance + amount
    c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_opponent_balance, opponent_id))
    
    # Mark the bet as canceled
    c.execute('UPDATE bets SET status = "canceled" WHERE id = ?', (bet_id,))
    c.connection.commit()
    
    # Notify users about the canceled bet and the refund
    await ctx.send(f"Bet on game '{game}' has been canceled. Both players have been refunded {amount} pet bucks each.")



@bot.command(name='give')
@with_config
async def give(ctx, recipient: discord.Member, amount: int):
    c = ctx.server_db.cursor()
    """Transfer pet bucks to another user."""

    sender_id = ctx.author.id
    recipient_id = recipient.id

    if recipient_id == sender_id:
        await ctx.send("❌ You can't give pet bucks to yourself.")
        return

    if amount <= 0:
        await ctx.send("❌ The amount must be greater than zero.")
        return

    # Check sender balance
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (sender_id,))
    sender_data = c.fetchone()

    if not sender_data:
        await ctx.send("❌ You don't have a wallet yet. Use `!>balance` to create one.")
        return

    sender_balance = sender_data[0]

    if sender_balance < amount:
        await ctx.send("❌ You don't have enough pet bucks for this transaction.")
        return

    # Ensure recipient has a wallet
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (recipient_id,))
    recipient_data = c.fetchone()

    if not recipient_data:
        # Auto-create wallet for recipient if missing
        c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (recipient_id, 1000))
        c.connection.commit()

    # Update balances
    c.execute("UPDATE user_wallets SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
    c.execute("UPDATE user_wallets SET balance = balance + ? WHERE user_id = ?", (amount, recipient_id))
    c.connection.commit()

    await ctx.send(f"💸 {ctx.author.mention} gave {amount} pet bucks to {recipient.mention}!")


@bot.command(aliases=["wallet", "coins", "petbucks", "bucks"])
@with_config
async def balance(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    target = user or ctx.author
    user_id = target.id

    balance = get_balance(user_id, c)

    if balance == 0:
        await ctx.message.add_reaction('<:6757_Sadge:1368625934587924552>')

    await ctx.send(f"💰 {target.display_name}'s balance: **{balance} pet bucks**")

@bot.command(aliases=["streak", "daily_claim", "claim"])
@with_config
async def daily(ctx):
    try:
        c = ctx.server_db.cursor()
        user_id = ctx.author.id
        now = datetime.now(timezone.utc)

        # Fetch last claim record
        c.execute("SELECT last_claim, claim_count, streak_days FROM daily_claims WHERE user_id = ?", (user_id,))
        row = c.fetchone()

        # Check if wallet exists for user
        c.execute("SELECT 1 FROM user_wallets WHERE user_id = ?", (user_id,))
        wallet_exists = c.fetchone()

        if not wallet_exists:
            c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (user_id, 1000))

        if row:
            last_claim = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
            claim_count = row[1]
            streak_days = row[2]

            # Cooldown check
            if now - last_claim < timedelta(hours=24):
                remaining = timedelta(hours=24) - (now - last_claim)
                hours, remainder = divmod(remaining.seconds, 3600)
                minutes = remainder // 60
                await ctx.send(f"⏳ You’ve already claimed your daily reward. Try again in {hours}h {minutes}m.", delete_after=5)
                return

            # Streak reset check
            if now - last_claim > timedelta(hours=48):
                streak_days = 0

            streak_days += 1
        else:
            claim_count = 0
            streak_days = 1

        # Reward scaling
        if streak_days <= 40:
            reward = 50 + (streak_days - 1) * 5
        else:
            reward = 50 + (39 * 5)

        # Update or insert into user_wallets
        c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
        bal_row = c.fetchone()
        if bal_row:
            new_balance = bal_row[0] + reward
            c.execute("UPDATE user_wallets SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        else:
            new_balance = reward
            c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (user_id, new_balance))

        # Update daily claim info
        c.execute("""
            INSERT OR REPLACE INTO daily_claims (user_id, last_claim, claim_count, streak_days)
            VALUES (?, ?, ?, ?)
        """, (user_id, now.isoformat(), claim_count + 1, streak_days))

        ctx.server_db.commit()

        await ctx.send(
            f"✅ {ctx.author.mention}, you've claimed your daily **{reward} pet bucks**, "
            f"on a streak of **{streak_days} days**! You've claimed **{claim_count + 1}** times in total."
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        await ctx.channel.send(f"❌ An error occurred: `{type(e).__name__}: {e}`", delete_after=10)


def get_balance(user_id, c):
    conn = c.connection
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row is None:
        # Insert with default 1000 if user not in table yet
        c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (user_id, 1000))
        c.connection.commit()
        return 1000
    return row[0]


@bot.command(name='add', aliases=['add_coins', 'create_coins'])
@with_config
@is_server_mod()
async def add_coins(ctx, user: discord.Member, amount: int):
    c = ctx.server_db.cursor()
    # Ensure the amount is valid (positive)

    if amount <= -10000000000000000000000000000000000000:
        await ctx.send("❌ You must specify a positive amount of pet bucks to add.")
        return

    # Call the existing update_balance function to add pet bucks to the user
    update_balance(user.id, amount, c)

    # Send a confirmation message
    await ctx.send(f"✅ {amount} pet bucks have been added to {user.mention}'s balance!")

def update_balance(user_id, amount, c):
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    row = c.fetchone()

    if row is None:
        # Not found, start with default balance + amount
        new_balance = max(0, 1000 + amount)
        c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (user_id, new_balance))
    else:
        new_balance = max(0, row[0] + amount)  # Prevent negative balance
        c.execute("UPDATE user_wallets SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    c.connection.commit()


def has_sufficient_funds(user_id, amount, c):
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    return row and row[0] >= amount


@bot.command()
@with_config
async def flip(ctx, amount: int = None):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    user_id = ctx.author.id
    balance = get_balance(user_id, c)


    if ctx.channel.id != GAMBLING_ID and amount not in [None,0]:
        await ctx.send(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return

    # Ensure the user has sufficient funds for gambling
    if amount is not None:
        if amount < 0:
            await ctx.send("❌ You must specify a positive amount to gamble.")
            return

    if amount > balance:
        await ctx.send(f"❌ {ctx.author.mention}, you don't have enough pet bucks to make that bet.")
        return

    result = random.choice(["🪙 Heads", "🪙 Tails"])

    # Random win/loss logic for gambling
    if amount:
        if random.choice([True, False]):
            # Win: Double the bet
            update_balance(user_id, amount, c)
            await ctx.send(f"{ctx.author.mention} flips a coin... {result}! You win! {amount} pet bucks have been added to your balance!")
        else:
            # Lose: Deduct the bet
            update_balance(user_id, -amount, c)
            await ctx.send(f"{ctx.author.mention} flips a coin... {result}! You lost! {amount} pet bucks have been deducted from your balance.")
    else:
        # Normal flip without gambling
        await ctx.send(f"{ctx.author.mention} flips a coin... {result}!")



@bot.command(aliases=["slot", "sluts"])
@with_config
async def slots(ctx, bet: int = 0):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    user_id = ctx.author.id
    balance = get_balance(user_id, c)

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return

    # Check if the user has enough balance
    if bet < 0:
        await ctx.send("❌ You must specify a positive amount to bet!")
        return

    if bet > balance:
        await ctx.send(f"❌ {ctx.author.mention}, you don't have enough pet bucks to make that bet.")
        return

    # Randomly generate 3 symbols for the slot machine
    symbols = ["🍒", "🍋", "🍊", "🍉", "🍇", "🍓"]
    result = [random.choice(symbols) for _ in range(3)]

    # Create embed to display result
    slot_embed = discord.Embed(
        title="🎰 Slot Machine Spin Result",
        description=f"{ctx.author.mention} spun the slots!",
        color=0x3498db
    )

    # Display the result symbols in the embed
    slot_embed.add_field(
        name="Spin Result",
        value=f"{' '.join(result)}",
        inline=False
    )

    # Check if the player wins
    if result[0] == result[1] == result[2]:
        winnings = bet * 36  # 36x multiplier for winning
        update_balance(user_id, winnings, c)
        slot_embed.add_field(
            name="Congratulations!",
            value=f"**You win!** {winnings} pet bucks have been added to your balance!",
            inline=False
        )
    else:
        update_balance(user_id, -bet, c)
        slot_embed.add_field(
            name="Better Luck Next Time!",
            value=f"**You lose!** {bet} pet bucks have been deducted from your balance.",
            inline=False
        )

    # Send the embed with the result
    await ctx.send(embed=slot_embed)


@bot.command(aliases=["wheel", "spin"])
@with_config
async def roulette(ctx, bet_type: str, bet_value: str, bet_amount: int):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    user_id = ctx.author.id
    balance = get_balance(user_id, c)
    bet_type = bet_type.lower()

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return

    # Validate bet amount
    if bet_amount < 0:
        await ctx.send("❌ You must bet a positive amount.")
        return

    if bet_amount > balance:
        await ctx.send(f"❌ {ctx.author.mention}, you don't have enough pet bucks to make that bet.")
        return

    if bet_type == 'colour':
        bet_type = 'color'

    # Validate bet type and value
    if bet_type not in ['number', 'color']:
        await ctx.send("❌ Invalid bet type. Please choose either 'number' or 'color'.")
        return

    if bet_type == 'color':
        if bet_value.lower() not in ['red', 'black']:
            await ctx.send("❌ Invalid color. Please choose either 'red', or 'black'.")
            return

    if bet_type == 'number':
        try:
            bet_value = int(bet_value)
            if bet_value < 0 or bet_value > 36:
                await ctx.send("❌ Invalid number. Please choose a number between 0 and 36.")
                return
        except ValueError:
            await ctx.send("❌ Invalid number format. Please choose a number between 0 and 36.")
            return

    # Spin the roulette wheel (random number between 0 and 36)
    spin_result = random.randint(0, 36)

    # Determine color of the number (red or black, 0 is green)
    if spin_result == 0:
        spin_color = 'green'
        spin_result_color = f"🟩 Green ({spin_result})"
    else:
        # Red numbers: 1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
        red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 29, 31, 33, 35}
        if spin_result in red_numbers:
            spin_color = 'red'
            spin_result_color = f"🟥 ({spin_result})"
        else:
            spin_color = 'black'
            spin_result_color = f"⬛ ({spin_result})"

    # Check if the player won
    win = False
    if bet_type == 'number' and str(bet_value) == str(spin_result):
        win = True
        winnings = bet_amount * 35  # 35:1 payout for numbers
    elif bet_type == 'color' and bet_value.lower() == spin_color:
        win = True
        winnings = bet_amount  # 2:1 payout for colors
    else:
        winnings = -bet_amount  # Loss

    # Update balance
    update_balance(user_id, winnings, c)

    # Create an embed to display the results
    result_embed = discord.Embed(
        title="🎱 Roulette Spin Result",
        description=f"{ctx.author.mention} spun the wheel and landed on {spin_result_color}!\n",
        color=0x2ecc71 if spin_color == 'green' else (0xff0000 if spin_color == 'red' else 0x000000)
    )

    # Add the result details to the embed
    if win:
        result_embed.add_field(
            name="🎉 You Win!",
            value=f"Congratulations, you won {winnings} pet bucks!\nYour bet of {bet_amount} pet bucks on {bet_type} {bet_value} was successful.",
            inline=False
        )
    else:
        result_embed.add_field(
            name="❌ You Lose!",
            value=f"Sorry, you lost {bet_amount} pet bucks.\nBetter luck next time!",
            inline=False
        )

    # Send the embed
    await ctx.send(embed=result_embed)


# Blackjack gambling game

@bot.command()
@with_config
async def join(ctx):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    guild_id = ctx.guild.id

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return


    if shared.game_in_progress[guild_id]:
        if ctx.author not in shared.queue[guild_id] and ctx.author not in shared.players[guild_id]:
            shared.queue[guild_id].append(ctx.author)
            await ctx.send(f"{ctx.author.mention} has been added to the queue for the next game.")
        else:
            await ctx.send(f"{ctx.author.mention}, you're already in the game or in the queue.")
        return

    if ctx.author not in shared.players[guild_id]:
        shared.players[guild_id].append(ctx.author)
        await ctx.send(f"{ctx.author.mention} has joined the Blackjack game!")

        # Start timeout countdown if this is the first player
        if len(shared.players[guild_id]) == 1 and shared.pending_blackjack_timeout[guild_id] is None:
            shared.pending_blackjack_timeout[guild_id] = asyncio.create_task(
                blackjack_join_timeout(ctx.guild.id, ctx.channel)
            )
    else:
        await ctx.send(f"{ctx.author.mention}, you're already in the current game.")


@bot.command()
@with_config
async def leave(ctx):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id



    if ctx.author in shared.players[guild_id]:
        shared.players[guild_id].remove(ctx.author)
        await ctx.send(f"{ctx.author.mention} has left the Blackjack game.")
    elif ctx.author in shared.queue[guild_id]:
        shared.queue[guild_id].remove(ctx.author)
        await ctx.send(f"{ctx.author.mention} has left the queue.")
    else:
        await ctx.send(f"{ctx.author.mention}, you're not currently in the game or in the queue.")


@bot.command()
@with_config
async def blackjack(ctx):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    guild_id = ctx.guild.id

    if shared.pending_blackjack_timeout.get(guild_id):
        shared.pending_blackjack_timeout[guild_id].cancel()
        shared.pending_blackjack_timeout[guild_id] = None

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return

    if shared.game_in_progress.get(guild_id):
        await ctx.send(f"❌ {ctx.author.mention}, a game is already in progress. Please wait for the next round.")
        return

    if len(shared.players.get(guild_id, [])) < 1:
        await ctx.send("❌ No players have joined. Use `!>join` to sit at the table!")
        return

    shared.game_in_progress[guild_id] = True
    shared.current_bets[guild_id] = {}

    for player in shared.players[guild_id]:
        bal = get_balance(player.id, c)
        await ctx.send(f"{player.mention}, please type your bet (30 seconds). You have **{bal} pet bucks** available:")

        def check(m):
            return m.author == player and m.content.isdigit()

        try:
            bet_msg = await bot.wait_for('message', timeout=30.0, check=check)
            bet = int(bet_msg.content)

            if not has_sufficient_funds(player.id, bet, c):
                await ctx.send(f"❌ {player.mention}, insufficient funds! You'll be playing for free!")
                shared.current_bets[guild_id][player] = 0
            else:
                shared.current_bets[guild_id][player] = bet
                await ctx.send(f"✅ {player.mention} bet {bet} pet bucks.")
        except asyncio.TimeoutError:
            await ctx.send(f"⏰ {player.mention} took too long! They'll be playing for free!")
            shared.current_bets[guild_id][player] = 0

    deck = shuffle_deck()
    player_hands = {player: [draw_card(deck), draw_card(deck)] for player in shared.players[guild_id]}
    dealer_hand = [draw_card(deck), draw_card(deck)]

    for hand in player_hands.values():
        for card in hand:
            update_running_count(card)
    for card in dealer_hand:
        update_running_count(card)

    embed = discord.Embed(title="🃏 Blackjack - Initial Deal", color=0x2ecc71)
    for player, hand in player_hands.items():
        embed.add_field(name=f"{player.display_name}'s Hand", value=f"{format_hand(hand)} (Total: {calculate_hand(hand)})", inline=False)
    embed.add_field(name="Dealer's Hand", value=f"{dealer_hand[0][0]}{dealer_hand[0][1]} | 🃏", inline=False)
    await ctx.send(embed=embed)

    current_players = [p for p in shared.players[guild_id] if calculate_hand(player_hands[p]) < 21]
    round_number = 1

    while current_players:
        action_embed = discord.Embed(
            title=f"🔄 Round {round_number} - Hit or Stand",
            description=f"Players: {', '.join([p.mention for p in current_players])}\nReact with 🃏 to HIT or ❌ to STAND\nYou have 30 seconds!",
            color=0xf1c40f
        )
        action_msg = await ctx.send(embed=action_embed)
        await action_msg.add_reaction("🃏")
        await action_msg.add_reaction("🚫")

        reactions = {}
        start_time = time.time()

        while (time.time() - start_time) < 30 and len(reactions) < len(current_players):
            try:
                reaction, user = await bot.wait_for(
                    'reaction_add',
                    timeout=30 - (time.time() - start_time),
                    check=lambda r, u: u in current_players and r.message.id == action_msg.id and str(r.emoji) in ["🃏", "🚫"]
                )
                if user.id not in reactions:
                    reactions[user.id] = str(reaction.emoji)
                    await ctx.send(f"🎯 {user.mention} chose to {'HIT' if reaction.emoji == '🃏' else 'STAND'}!")
            except asyncio.TimeoutError:
                break

        hitters = [p for p in current_players if reactions.get(p.id) == "🃏"]
        updated_players = []

        if hitters:
            hit_embed = discord.Embed(title="😤 Players Drawing Cards", color=0xe74c3c)
            for hitter in hitters:
                new_card = draw_card(deck)
                player_hands[hitter].append(new_card)
                update_running_count(new_card)
                total = calculate_hand(player_hands[hitter])
                status = " (BUST!)" if total > 21 else ""
                hit_embed.add_field(name=f"{hitter.display_name} drew {new_card[0]}{new_card[1]}", value=f"{format_hand(player_hands[hitter])} (Total: {total}{status})", inline=False)
                if total <= 21:
                    updated_players.append(hitter)
            hit_embed.add_field(name="Dealer's Hand", value=f"{dealer_hand[0][0]}{dealer_hand[0][1]} | 🃏", inline=False)
            await ctx.send(embed=hit_embed)

        current_players = updated_players
        round_number += 1

    dealer_total = calculate_hand(dealer_hand)
    dealer_cards = [f"{r}{s}" for r, s in dealer_hand]
    highest_player_total = max([calculate_hand(player_hands[p]) for p in shared.players[guild_id]])

    while True:
        if dealer_total >= 17 or dealer_total > highest_player_total:
            break
        new_card = draw_card(deck)
        dealer_hand.append(new_card)
        update_running_count(new_card)
        dealer_total = calculate_hand(dealer_hand)
        dealer_cards.append(f"{new_card[0]}{new_card[1]}")

    result_embed = discord.Embed(title="🏋️ Final Results", description=f"**Dealer's Hand**: {', '.join(dealer_cards)} (Total: {dealer_total})", color=0x9b59b6)
    for player in shared.players[guild_id]:
        hand = player_hands[player]
        total = calculate_hand(hand)
        bet = shared.current_bets[guild_id].get(player, 0)
        if total > 21:
            result = f"❌ BUST! Lost {bet} pet bucks"
            update_balance(player.id, -bet, c)
        elif dealer_total > 21 or total > dealer_total:
            result = f"✅ WIN! Gained {bet} pet bucks"
            update_balance(player.id, bet, c)
        elif total == dealer_total:
            result = "🔶 PUSH! Bet returned"
        else:
            result = f"❌ LOSE! Lost {bet} pet bucks"
            update_balance(player.id, -bet, c)

        result_embed.add_field(name=f"{player.display_name}'s Hand", value=f"{format_hand(hand)} (Total: {total})\n{result}", inline=False)

    await ctx.send(embed=result_embed)

    shared.game_in_progress[guild_id] = False
    shared.current_bets[guild_id].clear()
    if shared.pending_blackjack_timeout.get(guild_id) is None:
        shared.pending_blackjack_timeout[guild_id] = asyncio.create_task(blackjack_join_timeout(ctx.guild.id, ctx.channel))

    if shared.queue.get(guild_id):
        await ctx.send(f"🎮 Next game starting with: {', '.join([p.mention for p in shared.queue[guild_id]])}")
        shared.players[guild_id].extend(shared.queue[guild_id])
        shared.queue[guild_id].clear()


# Helper functions

def format_hand(hand):
    return ', '.join([f"{r}{s}" for r, s in hand])


def create_deck():
    """Create a new shuffled deck with 2 shoes (8 decks)."""
    return [(rank, suit) for suit in SUITS for rank in RANKS] * 8  # 8 decks (2 shoes)

def shuffle_deck():
    global remaining_cards, deck

    # Only shuffle if the remaining cards are 50% or lower or deck is empty
    if remaining_cards <= total_deck_size / 2 or not deck:
        deck = []
        suits = ['♠', '♣', '♦', '♥']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        
        for _ in range(8):  # 8 decks
            for suit in suits:
                for rank in ranks:
                    deck.append((rank, suit))
        
        random.shuffle(deck)  # Shuffle the deck
        remaining_cards = len(deck)  # Reset the remaining cards to the full deck size
    return deck

def draw_card(deck):
    global remaining_cards
    if not deck:
        deck.extend(shuffle_deck())
        remaining_cards = len(deck)
    card = deck.pop()
    remaining_cards -= 1
    return card


def calculate_hand(hand):
    total = 0
    aces = 0
    for rank, _ in hand:
        if rank in ['J', 'Q', 'K']:
            total += 10
        elif rank == 'A':
            total += 11
            aces += 1
        else:
            total += int(rank)
    
    # Adjust for aces if necessary (e.g., if total exceeds 21)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    
    return total

def update_running_count(card):
    """Update the running count based on the Hi-Lo card counting system."""
    global running_count
    if card[0] in ['2', '3', '4', '5', '6']:
        running_count += 1
    elif card[0] in ['10', 'J', 'Q', 'K', 'A']:
        running_count -= 1

async def blackjack_join_timeout(guild_id, channel):
    await asyncio.sleep(300)  # 5 minutes



    if not shared.game_in_progress[guild_id] and shared.players[guild_id]:
        removed_mentions = ', '.join([p.mention for p in shared.players[guild_id]])
        await channel.send(
            f"🕒 No Blackjack game started in 5 minutes. Removing these players from the table: {removed_mentions}"
        )
        shared.players[guild_id].clear()
        shared.queue[guild_id].clear()

    shared.pending_blackjack_timeout[guild_id] = None  # Reset the task tracker


# ------------------- Gambling Help -------------------
@bot.command(aliases=["gamble", "games"])
@with_config
async def play(ctx):
    c = ctx.server_db.cursor()
    help_embed = discord.Embed(
        title="🎮 Gambling Help - Available Games",
        description="Here are the games you can play and how to use them:",
        color=0x3498db
    )

    # Flip command help
    help_embed.add_field(
        name="🪙 !>flip [amount]",
        value="Flip a coin for a gamble. If you provide an amount, you will bet that amount of pet bucks.\n"
              "If you win, your bet will be doubled. If you lose, your bet is deducted.",
        inline=False
    )

    # Slots commands help (with aliases)
    help_embed.add_field(
        name="🍒 !>slots [amount] / !>slot [amount]",
        value="Spin the slot machine and gamble your coins. You will bet the specified amount of pet bucks.\n"
              "If all symbols match, you win! If not, you lose your bet.",
        inline=False
    )

    # Roulette command help
    help_embed.add_field(
        name="🎱 !>roulette [bet_type] [bet_value] [bet_amount]",
        value="Play Roulette! Bet on a 'number' (0-36) or 'color' (red/black).\n"
              "If you bet on a number, the payout is 35:1. If you bet on a color, the payout is 2:1.",
        inline=False
    )

    # Blackjack commands help
    help_embed.add_field(
        name="♠️ !>join",
        value="Join the Blackjack game. If a game is in progress, you will be added to the queue.",
        inline=False
    )
    help_embed.add_field(
        name="♠️ !>leave",
        value="Leave the Blackjack game or the queue.",
        inline=False
    )
    help_embed.add_field(
        name="♠️ !>blackjack",
        value="Start a Blackjack game. You will be asked to place a bet. Use 'HIT' or 'STAND' to play your hand.",
        inline=False
    )

    await ctx.send(embed=help_embed)




# ------------------- Counting Commands -------------------

def get_freedom_pot():
    c.execute("SELECT pot FROM pot WHERE id = 1")
    result = c.fetchone()
    return result[0] if result else 0

def update_freedom_pot(delta):
    current = get_freedom_pot()
    new_value = max(0, current + delta)
    c.execute("UPDATE pot SET pot = ? WHERE id = 1", (new_value,))
    c.connection.commit()

def clear_freedom_pot():
    c.execute("UPDATE pot SET pot = 0 WHERE id = 1")
    c.connection.commit()

def preprocess_math_expr(expr):
    superscript_map = {
        '²': '**2',
        '³': '**3',
        # Add more as needed
    }
    for sup, repl in superscript_map.items():
        expr = expr.replace(sup, repl)
    return expr

# Math-safe evaluation
def safe_eval(expr):
    allowed_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv
    }

    constants = {
        "pi": math.pi,
        "π": math.pi,
        "e": math.e,
        "tau": math.tau,
        "phi": (1 + 5 ** 0.5) / 2,
        "Φ": (1 + 5 ** 0.5) / 2,
        "sqrt2": math.sqrt(2),
        "√2": math.sqrt(2),
        "c": 299792458,
        "g": 9.80665,
    }

    expr = preprocess_math_expr(expr)

    def eval_node(node):
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        elif isinstance(node, ast.BinOp):
            return allowed_operators[type(node.op)](eval_node(node.left), eval_node(node.right))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "sqrt":
                return math.sqrt(eval_node(node.args[0]))
            raise ValueError("Unsupported function")
        elif isinstance(node, ast.Name):
            if node.id in constants:
                return constants[node.id]
            raise ValueError("Unknown constant")
        elif isinstance(node, ast.Num):
            return node.n
        elif isinstance(node, ast.Constant):
            return node.value
        else:
            raise ValueError("Unsupported expression")

    try:
        parsed = ast.parse(expr, mode='eval')
        return int(eval_node(parsed))
    except:
        return None

# Game state tracking
counting_game = {
    "active": False,
    "count": 0,
    "last_user_id": None,
    "participants": set(),
}

@bot.command()
@with_config
async def place(ctx, amount: int):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    user_id = ctx.author.id
    if ctx.channel.id != PRISON_CHANNEL_ID:
        return
    if amount <= 0:
        await ctx.send("❌ Amount must be greater than 0.")
        return
    if get_balance(user_id, c) < amount:
        await ctx.send("❌ You don't have enough pet bucks.")
        return


    update_balance(user_id, -amount, c)
    update_freedom_pot(amount)
    freedom_pot = get_freedom_pot()
    await ctx.send(f"💰 {ctx.author.display_name} contributed {amount} to the freedom pot. Total is now {freedom_pot}.")


@bot.command()
@with_config
async def startcount(ctx):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    global freedom_pot
    guild_id = ctx.guild.id
    if ctx.channel.id != PRISON_CHANNEL_ID:
        return

    user_id = ctx.author.id

    if counting_game["active"]:
        await ctx.send("⚠️ A counting challenge is already in progress!")
        return
    
    pot = get_freedom_pot()
    if pot <= 0:
        await ctx.send("❌ No contributions in the freedom pot. Use `!>place <amount>` to contribute.")
        return
    

    counting_game.update({
        "active": True,
        "count": 0,
        "last_user_id": None,
        "participants": set(),
    })
    shared.counting_game[guild_id] = counting_game
    freedom_pot = get_freedom_pot()
    await ctx.send(f"🔢 A new counting challenge has begun! Count from 1 to `{COUNT_TO}`. No repeats! No mistakes! Don't send two numbers in a row! You may use math expressions like `2+2` instead of `4`.")
    await ctx.send(f"💰 The freedom pot is now at {freedom_pot} pet bucks. If you win, you freedom for {freedom_pot*SECONDS_PER_BUCK} seconds")

@bot.command()
@with_config
async def countstatus(ctx):
    c = ctx.server_db.cursor()
    if not counting_game["active"]:
        await ctx.send("❌ No active counting challenge.")
        return

    await ctx.send(f"📊 Current count: **{counting_game['count']}**. Participants: {len(counting_game['participants'])}.")



            # Add your temporary freedom logic here (e.g., remove prison role, start timer to re-add)

# ------------------- Voice Channel Commands -------------------

@bot.command()
@with_config
async def vc(ctx):
    c = ctx.server_db.cursor()
    """Toggle VC connection for the bot in the user's current voice channel."""
    user = ctx.author

    if not user.voice or not user.voice.channel:
        await ctx.send("❌ You're not in a voice channel.")
        return

    voice_channel = user.voice.channel
    voice_client = ctx.guild.voice_client

    if voice_client and voice_client.is_connected():
        if voice_client.channel == voice_channel:
            await voice_client.disconnect()
            await ctx.send("🔇 Bot has left the voice channel.")
        else:
            await voice_client.move_to(voice_channel)
            await ctx.send(f"🔁 Moved to {voice_channel.name}")
    else:
        await voice_channel.connect()
        await ctx.send(f"🔊 Joined {voice_channel.name}")

@bot.command()
@with_config
async def click(ctx):
    c = ctx.server_db.cursor()
    # Check if the user is in a voice channel
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel to use this command.")
        return

    # Get the target voice channel
    target_channel = ctx.author.voice.channel

    # Get the bot's current voice client in the guild
    voice_client = ctx.voice_client

    # Check if the bot needs to connect or move channels
    if voice_client:
        if voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)
    else:
        # Connect to the target channel if not connected
        voice_client = await target_channel.connect()

    # Ensure the voice client is ready
    if not voice_client.is_connected():
        await voice_client.connect()

    # Play the audio if not already playing
    if not voice_client.is_playing():
        audio_source = discord.FFmpegPCMAudio("non-suspicious sound.mp3")
        voice_client.play(audio_source)

        # Wait until the audio finishes playing
        while voice_client.is_playing():
            await asyncio.sleep(1)
    else:
        await ctx.send("Audio is already playing.")

@bot.command()
@with_config
@is_server_mod()
async def ping(ctx):
    c = ctx.server_db.cursor()
    # Check if the user is in a voice channel


    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel to use this command.")
        return

    # Get the target voice channel
    target_channel = ctx.author.voice.channel

    # Get the bot's current voice client in the guild
    voice_client = ctx.voice_client

    # Check if the bot needs to connect or move channels
    if voice_client:
        if voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)
    else:
        # Connect to the target channel if not connected
        voice_client = await target_channel.connect()

    # Ensure the voice client is ready
    if not voice_client.is_connected():
        await voice_client.connect()

    # Play the audio if not already playing
    if not voice_client.is_playing():
        audio_source = discord.FFmpegPCMAudio("ping.mp3")
        voice_client.play(audio_source)

        # Wait until the audio finishes playing
        while voice_client.is_playing():
            await asyncio.sleep(1)
    else:
        await ctx.send("Audio is already playing.")

@bot.command()
@with_config
@is_server_mod()
async def windows(ctx):
    c = ctx.server_db.cursor()

    # Check if the user is in a voice channel
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel to use this command.")
        return

    # Get the target voice channel
    target_channel = ctx.author.voice.channel

    # Get the bot's current voice client in the guild
    voice_client = ctx.voice_client

    # Check if the bot needs to connect or move channels
    if voice_client:
        if voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)
    else:
        # Connect to the target channel if not connected
        voice_client = await target_channel.connect()

    # Ensure the voice client is ready
    if not voice_client.is_connected():
        await voice_client.connect()

    # Play the audio if not already playing
    if not voice_client.is_playing():
        audio_source = discord.FFmpegPCMAudio("ding.wav")
        voice_client.play(audio_source)

        # Wait until the audio finishes playing
        while voice_client.is_playing():
            await asyncio.sleep(1)
    else:
        await ctx.send("Audio is already playing.")

@bot.command()
@with_config
@is_server_mod()
async def iphone(ctx):
    c = ctx.server_db.cursor()

    # Check if the user is in a voice channel
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel to use this command.")
        return

    # Get the target voice channel
    target_channel = ctx.author.voice.channel

    # Get the bot's current voice client in the guild
    voice_client = ctx.voice_client

    # Check if the bot needs to connect or move channels
    if voice_client:
        if voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)
    else:
        # Connect to the target channel if not connected
        voice_client = await target_channel.connect()

    # Ensure the voice client is ready
    if not voice_client.is_connected():
        await voice_client.connect()

    # Play the audio if not already playing
    if not voice_client.is_playing():
        audio_source = discord.FFmpegPCMAudio("alarm.mp3")
        voice_client.play(audio_source)

        # Wait until the audio finishes playing
        while voice_client.is_playing():
            await asyncio.sleep(1)
    else:
        await ctx.send("Audio is already playing.")

@bot.command()
@with_config
async def speak(ctx, *, message: str):
    c = ctx.server_db.cursor()

    """Makes the bot speak a message in the voice channel it is already in."""
    if ctx.voice_client:  # Check if the bot is already in a voice channel
        # Generate TTS audio file from the message
        tts_file = "tts_output.mp3"
        
        # Generate the TTS audio using Google TTS
        tts = gTTS(message, lang='en')
        tts.save(tts_file)

        # Play the TTS audio in the current voice channel
        ctx.voice_client.play(discord.FFmpegPCMAudio(tts_file), after=lambda e: print('Done speaking'))

        # Wait for the audio to finish
        while ctx.voice_client.is_playing():
            await asyncio.sleep(1)

        # Clean up the temporary TTS file
        os.remove(tts_file)
    else:
        await ctx.send("❌ The bot is not in a voice channel.")

@bot.command()
@with_config
@is_server_mod()
async def shrek(ctx):
    c = ctx.server_db.cursor()
    """Plays the entire Shrek movie line-by-line with a 30-second gap between lines."""
    if not ctx.voice_client:
        await ctx.send("❌ The bot is not in a voice channel.")
        return

    # Load the text file and split it into lines
    try:
        with open("shrek.txt", "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        await ctx.send("❌ Could not find the 'shrek.txt' file.")
        return

    # Loop through the lines of the movie
    for line in lines:
        line = line.strip()  # Remove any leading/trailing whitespace

        # Skip empty lines
        if not line:
            continue

        # Generate TTS for the current line
        tts_file = "tts_output.mp3"
        tts = gTTS(text=line, lang='en', slow=False)
        tts.save(tts_file)

        # Play the TTS audio in the current voice channel
        ctx.voice_client.play(discord.FFmpegPCMAudio(tts_file), after=lambda e: print('Done speaking'))

        # Wait for the audio to finish playing
        while ctx.voice_client.is_playing():
            await asyncio.sleep(1)

        # Clean up the temporary TTS file
        os.remove(tts_file)

        # Wait for 30 seconds before playing the next line
        await asyncio.sleep(30)

    await ctx.send("📽️ The Shrek movie has finished playing!")




# A set to store user IDs of the users that are being targeted (to mute/unmute)
targeted_users = set()
# A set to store user IDs of the users that are being targeted (to deafen/undeafen)
targeted_deaf_users = set()

# Dictionary to track users' active mute/unmute tasks
active_tasks = {}
# Dictionary to track users' active deafen/undeafen tasks
active_deaf_tasks = {}

# Event to listen for voice state updates (when users join or leave VC)
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # Check if the member is in the targeted users list (mute/unmute)
    if member.id in targeted_users:
        # Check if the member has just joined a voice channel
        if after.channel and not before.channel:
            # Start the mute/unmute loop if not already running
            if member.id not in active_tasks:
                active_tasks[member.id] = asyncio.create_task(mute_unmute_loop(member))

        # If the member leaves the voice channel, stop the mute/unmute loop
        elif not after.channel:
            if member.id in active_tasks:
                active_tasks[member.id].cancel()  # Cancel the ongoing task
                del active_tasks[member.id]

    # Check if the member is in the targeted deafen users list
    if member.id in targeted_deaf_users:
        # Check if the member has just joined a voice channel
        if after.channel and not before.channel:
            # Start the deafen/undeafen loop if not already running
            if member.id not in active_deaf_tasks:
                active_deaf_tasks[member.id] = asyncio.create_task(deafen_undeafen_loop(member))

        # If the member leaves the voice channel, stop the deafen/undeafen loop
        elif not after.channel:
            if member.id in active_deaf_tasks:
                active_deaf_tasks[member.id].cancel()  # Cancel the ongoing task
                del active_deaf_tasks[member.id]

# Command to toggle the mute status of a user
@bot.command()
@with_config
@is_server_mod()
async def mute(ctx, user: discord.Member):
    c = ctx.server_db.cursor()

    """Add or remove a user from the muted target list."""
    
    # If the user is in the target list, remove them, otherwise add them
    if user.id in targeted_users:
        targeted_users.remove(user.id)
        await ctx.send(f"❌ {user.display_name} has been removed from the mute list.")
    else:
        targeted_users.add(user.id)
        await ctx.send(f"✅ {user.display_name} has been added to the mute list.")

# Mute/unmute loop for a user
async def mute_unmute_loop(member: discord.Member):
    """Continuously mute and unmute the member every 2 seconds as long as they stay in the VC."""
    try:
        while member.id in targeted_users and member.voice and member.voice.channel:
            await member.edit(mute=True)
            print(f"{member.display_name} has been server-muted.")
            await asyncio.sleep(2)
            await member.edit(mute=False)
            print(f"{member.display_name} has been unmuted.")
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        print(f"Stopped muting/unmuting {member.display_name} as they left the voice channel.")

# Command to toggle the deafen status of a user
@bot.command()
@with_config
@is_server_mod()
async def deafen(ctx, user: discord.Member):
    c = ctx.server_db.cursor()


    """Add or remove a user from the deafened target list."""
    
    # If the user is in the target list, remove them, otherwise add them
    if user.id in targeted_deaf_users:
        targeted_deaf_users.remove(user.id)
        await ctx.send(f"❌ {user.display_name} has been removed from the deafen list.")
    else:
        targeted_deaf_users.add(user.id)
        await ctx.send(f"✅ {user.display_name} has been added to the deafen list.")

# Deafen/undeafen loop for a user
async def deafen_undeafen_loop(member: discord.Member):
    """Continuously deafen and undeafen the member every 2 seconds as long as they stay in the VC."""
    try:
        while member.id in targeted_deaf_users and member.voice and member.voice.channel:
            await member.edit(deafen=True)
            print(f"{member.display_name} has been server-deafened.")
            await asyncio.sleep(2)
            await member.edit(deafen=False)
            print(f"{member.display_name} has been undeafened.")
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        print(f"Stopped deafening/undeafening {member.display_name} as they left the voice channel.")



# ------------------------------- Shop --------------------------------
@bot.command(name='freedom')
@with_config
async def freedom(ctx):
    c = ctx.server_db.cursor()
    user = ctx.author
    user_id = user.id
    guild_id = ctx.guild.id

    if user_id not in shared.solitary_confinement[guild_id]:
        await ctx.send("❌ You're not in solitary confinement.")
        return

    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    result = c.fetchone()

    if not result or result[0] < 2000:
        await ctx.send("❌ You need 2000 pet bucks to buy your freedom.")
        return

    try:
        c.execute("UPDATE user_wallets SET balance = balance - 2000 WHERE user_id = ?", (user_id,))
        c.connection.commit()
    except Exception as e:
        await ctx.send(f"❌ Failed to deduct coins: {e}")
        return

    success = await release_from_solitary(ctx, user)
    if success:
        print("released from solitary")
    else:
        await ctx.send("❌ Failed to release you. Please contact an admin.")


# ------------------------------- Shop Helper Commands -------------------------------

async def release_from_solitary(ctx, target: discord.Member):


    """Helper to release a user from solitary confinement without using the command directly."""
    config = shared.get_server_config(ctx.guild.id)
    if not config:
        await ctx.send("❌ Server configuration is missing.")
        return False
    db = shared.get_server_db(ctx.guild.id)
    c = db.cursor()
    # Pull variables from config
    PRISON_CHANNEL_ID = config.PRISON_CHANNEL_ID
    SOLITARY_ROLE_ID = config.SOLITARY_ROLE_ID
    PRISONER_ROLE_ID = config.PRISONER_ROLE_ID
    SC_ROLE_ID = config.SC_ROLE_ID

    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SC_ROLE_ID)

    guild_id = ctx.guild.id

    # Validate essential roles/channels
    if not all([prison_channel, solitary_role, prisoner_role, sc_role]):
        await ctx.send("❌ Missing configuration for solitary system.")
        return False

    if target.id not in shared.solitary_confinement[guild_id]:
        await ctx.send("❌ You're not currently in solitary.")
        return False

    try:
        thread_id = shared.solitary_confinement[guild_id][target.id]
        thread = await ctx.guild.fetch_channel(thread_id)

        if thread:
            await thread.send(f"{target.name} has been released from solitary.")
            await thread.send("It's mighty lonely in here.")
            await thread.send("Don't chat here, the silly prisoner won't see it!")
            await thread.edit(archived=True, locked=True, reason="Released from solitary confinement.")
            c.execute("UPDATE solitary_confinement SET archive_date = ? WHERE user_id = ?", (datetime.now(), target.id))
            c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
            c.connection.commit()
        else:
            await ctx.send("❌ Could not fetch the solitary thread.")
            return False

        del shared.solitary_confinement[guild_id][target.id]
        del shared.prison_users[guild_id][target.id]

        await target.remove_roles(prisoner_role, solitary_role, reason="Released from solitary")
        return True

    except discord.Forbidden:
        await ctx.send("❌ Missing permissions to modify roles or threads.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Error during solitary release: {e}")

    return False


@bot.command()
@with_config
async def whois(ctx, user_id: int):
    c = ctx.server_db.cursor()
    try:
        user = await bot.fetch_user(user_id)
        account_age = (ctx.message.created_at - user.created_at).days
        
        embed = discord.Embed(color=0x7289da)
        embed.set_author(name=f"User ID: {user_id}", icon_url=user.display_avatar.url)
        embed.add_field(name="Username", value=f"{user.name}#{user.discriminator}", inline=False)
        embed.add_field(name="Account Created", value=f"{user.created_at.strftime('%Y-%m-%d')} ({account_age} days ago)", inline=False)
        embed.add_field(name="In Server?", value="Yes" if ctx.guild.get_member(user_id) else "No", inline=False)
        embed.add_field(name="Mention", value=f"`<@{user_id}>`", inline=False)
        
        await ctx.send(embed=embed)
    except discord.NotFound:
        await ctx.send(f"❌ No user found with ID `{user_id}`")
    except discord.HTTPException:
        await ctx.send("❌ Error looking up user")
    except ValueError:
        await ctx.send("❌ Invalid ID format - must be numbers only")



@bot.command(name="status")
@with_config
async def status_prefix(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    target = user or ctx.author
    
    # Create embed with thumbnail
    embed = discord.Embed(title=f"Status for {target.display_name}", color=0x00ff00)
    embed.set_thumbnail(url=target.display_avatar.url)

    # Get enforced/banned words
    enforced = shared.enforced_words.get(guild_id, {}).get(target.id, set())
    banned = shared.banned_words.get(guild_id, {}).get(target.id, set())
    has_words = bool(enforced or banned)

    # 🔐 Lock status
    if target.id in shared.locked_users.get(guild_id, set()):
        locker_id = shared.locked_by_map.get(guild_id, {}).get(target.id)
        locker = ctx.guild.get_member(locker_id)
        locker_name = locker.display_name if locker else f"User ID {locker_id}"
        embed.add_field(name="🔒 LOCK STATUS", value=f"Locked by `{locker_name}`", inline=False)

    # ✅ Allowed Channels (Bypass)
    c.execute("SELECT channel_id FROM allowed_users WHERE user_id = ?", (target.id,))
    allowed_rows = c.fetchall()
    if allowed_rows:
        allowed_channels = []
        for row in allowed_rows:
            chan = ctx.guild.get_channel(row[0])
            if chan:
                allowed_channels.append(f"<#{chan.id}>")
            else:
                allowed_channels.append(f"`Channel {row[0]}`")

        channels_str = ", ".join(allowed_channels)
        embed.add_field(name="✅ Bypass Allowed In", value=channels_str, inline=False)
    # Create status sections
    status_fields = []
    
    # 🔑 Auth status
    c.execute("SELECT auth_level FROM user_auth WHERE user_id = ?", (target.id,))
    result = c.fetchone()
    auth_level = result[0] if result else DEFAULT_AUTH_MODE
    status_fields.append(f"🔑 **Auth Mode:** {auth_level.capitalize()}")
    
    # 🔇 Gag status - show gag type if gagged
    gagged_users = shared.gagged_users.get(guild_id, {})
    is_gagged = target.id in gagged_users
    if is_gagged:
        gag_type = gagged_users[target.id]
        # Capitalize and add "Gag" if not already in type
        display_type = gag_type.capitalize()
        if "gag" not in display_type.lower():
            display_type += " Gag"
        status_fields.append(f"🔇 **Gag Status:** 🔇 {display_type}")
    else:
        status_fields.append(f"🔇 **Gag Status:** 🗣️ Not gagged")
    
    # 🏛️ Prison status
    prison_status = "🔓 Not in prison"
    if target.id in shared.prison_users.get(guild_id, {}):
        try:
            channel = await ctx.guild.fetch_channel(shared.prison_users[guild_id][target.id])
            prison_status = f"🔒 In prison ({channel.mention})"
        except:
            prison_status = "🔒 In prison (unknown channel)"
    status_fields.append(f"🏛️ **Prison Status:** {prison_status}")
    
    # 🚪 Solitary status
    solitary_status = "🔓 No cell assigned"
    if target.id in shared.solitary_confinement.get(guild_id, {}):
        solitary_status = "🔒 Owns a Cell"
    status_fields.append(f"🚪 **Solitary Status:** {solitary_status}")

    if target.id in shared.double_type_users.get(guild_id, {}):
        status_fields.append(f"**Misc:** 2️⃣ Double Type")

    c.execute("SELECT 1 FROM nonbypass_users WHERE guild_id = ? AND user_id = ?", (guild_id, target.id))
    forced_filtered = c.fetchone() is not None
    if forced_filtered:
        status_fields.append(f"**Misc:** Bypass Disabled")

    # ⚡ PiShock user check
    if target.id in shared.user_pishock_codes.get(guild_id, {}):
        status_fields.append(f"⚡ **PiShock User:** 🟢 Registered")

    # Add status fields
    embed.add_field(name="**🔒 RESTRAINT STATUS**", value="\n".join(status_fields), inline=False)

    # 🔠 Word Controls section
    cooldown = shared.cooldown_users.get(guild_id, {}).get(target.id, 0)
    word_controls = []
    
    # Only show cooldown if >0
    if cooldown > 0:
        word_controls.append(f"⏱️ **Cooldown:** 🔴 {cooldown}s")
    
    # Enforced words
    if enforced:
        enforced_list = sorted(enforced)
        enforced_str = ',  '.join(f'`{w}`' for w in enforced_list)
        word_controls.append(f"⛔ **Enforced Words**\n{enforced_str}")
    
    # Banned words
    if banned:
        banned_list = sorted(banned)
        banned_str = ',  '.join(f'`{w}`' for w in banned_list)
        word_controls.append(f"🚫 **Banned Words**\n{banned_str}")
    
    # ⚙️ Enforcement settings (only show if there are words)
    if has_words:
        c.execute("SELECT enforcement_action FROM user_settings WHERE user_id = ?", (target.id,))
        row = c.fetchone()
        if row and row[0]:
            enforcement_action = row[0].lower()
            auto_tag = ""
        else:
            enforcement_action = "timeout"
            auto_tag = " [auto]"
        word_controls.append(f"⚙️ **Enforcement Action:** {enforcement_action.capitalize()}{auto_tag}")
    
    # Only add Word Controls section if there's content to show
    if word_controls:
        # Add separator before Word Controls
        embed.add_field(name="\u200b", value="────────────", inline=False)
        
        # Add compact word controls section
        embed.add_field(name="**🔠 WORD CONTROLS**", value="\n\n".join(word_controls), inline=False)

    await ctx.send(embed=embed)

@bot.slash_command(name="status", description="Check moderation status and restrictions on a user.")
@with_config
async def status_slash(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to check", required=False) = None
):
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id
    target = user or ctx.author

    embed = discord.Embed(title=f"Status for {target.display_name}", color=0x00ff00)
    embed.set_thumbnail(url=target.display_avatar.url)

    enforced = shared.enforced_words.get(guild_id, {}).get(target.id, set())
    banned = shared.banned_words.get(guild_id, {}).get(target.id, set())
    has_words = bool(enforced or banned)

    if target.id in shared.locked_users.get(guild_id, set()):
        locker_id = shared.locked_by_map.get(guild_id, {}).get(target.id)
        locker = ctx.guild.get_member(locker_id)
        locker_name = locker.display_name if locker else f"User ID {locker_id}"
        embed.add_field(name="🔒 LOCK STATUS", value=f"Locked by `{locker_name}`", inline=False)

    # ✅ Allowed Channels (Bypass)
    c.execute("SELECT channel_id FROM allowed_users WHERE user_id = ?", (target.id,))
    allowed_rows = c.fetchall()
    if allowed_rows:
        allowed_channels = []
        for row in allowed_rows:
            chan = ctx.guild.get_channel(row[0])
            if chan:
                allowed_channels.append(f"<#{chan.id}>")
            else:
                allowed_channels.append(f"`Channel {row[0]}`")

        channels_str = ", ".join(allowed_channels)
        embed.add_field(name="✅ Bypass Allowed In", value=channels_str, inline=False)

    status_fields = []

    c.execute("SELECT auth_level FROM user_auth WHERE user_id = ?", (target.id,))
    result = c.fetchone()
    auth_level = result[0] if result else DEFAULT_AUTH_MODE
    status_fields.append(f"🔑 **Auth Mode:** {auth_level.capitalize()}")

    gagged_users = shared.gagged_users.get(guild_id, {})
    is_gagged = target.id in gagged_users
    if is_gagged:
        gag_type = gagged_users[target.id].capitalize()
        if "gag" not in gag_type.lower():
            gag_type += " Gag"
        status_fields.append(f"🔇 **Gag Status:** 🔇 {gag_type}")
    else:
        status_fields.append(f"🔇 **Gag Status:** 🗣️ Not gagged")

    prison_status = "🔓 Not in prison"
    if target.id in shared.prison_users.get(guild_id, {}):
        try:
            channel = await ctx.guild.fetch_channel(shared.prison_users[guild_id][target.id])
            prison_status = f"🔒 In prison ({channel.mention})"
        except:
            prison_status = "🔒 In prison (unknown channel)"
    status_fields.append(f"🏛️ **Prison Status:** {prison_status}")

    solitary_status = "🔓 No cell assigned"
    if target.id in shared.solitary_confinement.get(guild_id, {}):
        solitary_status = "🔒 Owns a Cell"
    status_fields.append(f"🚪 **Solitary Status:** {solitary_status}")

    if target.id in shared.double_type_users.get(guild_id, {}):
        status_fields.append("**Misc:** 2️⃣ Double Type")

    c.execute("SELECT 1 FROM nonbypass_users WHERE guild_id = ? AND user_id = ?", (guild_id, target.id))
    if c.fetchone():
        status_fields.append("**Misc:** Bypass Disabled")

    if target.id in shared.user_pishock_codes.get(guild_id, {}):
        status_fields.append("⚡ **PiShock User:** 🟢 Registered")

    embed.add_field(name="**🔒 RESTRAINT STATUS**", value="\n".join(status_fields), inline=False)

    cooldown = shared.cooldown_users.get(guild_id, {}).get(target.id, 0)
    word_controls = []

    if cooldown > 0:
        word_controls.append(f"⏱️ **Cooldown:** 🔴 {cooldown}s")

    if enforced:
        enforced_str = ',  '.join(f'`{w}`' for w in sorted(enforced))
        word_controls.append(f"⛔ **Enforced Words**\n{enforced_str}")

    if banned:
        banned_str = ',  '.join(f'`{w}`' for w in sorted(banned))
        word_controls.append(f"🚫 **Banned Words**\n{banned_str}")

    if has_words:
        c.execute("SELECT enforcement_action FROM user_settings WHERE user_id = ?", (target.id,))
        row = c.fetchone()
        enforcement_action = row[0].lower() if row and row[0] else "timeout"
        auto_tag = "" if row and row[0] else " [auto]"
        word_controls.append(f"⚙️ **Enforcement Action:** {enforcement_action.capitalize()}{auto_tag}")

    if word_controls:
        embed.add_field(name="\u200b", value="─" * 12, inline=False)
        embed.add_field(name="**🔠 WORD CONTROLS**", value="\n\n".join(word_controls), inline=False)

    await ctx.respond(embed=embed)

@bot.command()
@with_config
async def enforce_list(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    """List the enforced words for a user.
    Usage: !>enforced_list @user (or no @user for author)
    """
    # Default to ctx.author if no user is mentioned
    target = user or ctx.author
    guild_id = ctx.guild.id

    # Check if the user has any enforced words
    if target.id not in shared.enforced_words[guild_id] or not shared.enforced_words[guild_id][target.id]:
        await ctx.send(f"{target.mention} has no enforced words.")
        return

    # Retrieve and format the enforced words for the user
    enforced_list = shared.enforced_words[guild_id][target.id]
    formatted_words = [f"**{word}** - Initial Time: {data['initial_time']}s, Added Time: {data['added_time']}s"
                       for word, data in enforced_list.items()]

    # If the list is not empty, send the formatted message
    enforced_words_message = "\n".join(formatted_words)
    await ctx.send(f"Enforced words for {target.mention}:\n{enforced_words_message}")

@bot.command()
@with_config
async def ban_list(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    """List the enforced words for a user.
    Usage: !>ban_list @user (or no @user for author)
    """
    # Default to ctx.author if no user is mentioned
    target = user or ctx.author
    guild_id = ctx.guild.id

    # Check if the user has any enforced words
    if target.id not in shared.banned_words[guild_id] or not shared.banned_words[guild_id][target.id]:
        await ctx.send(f"{target.mention} has no banned words.")
        return

    # Retrieve and format the banned words for the user
    banned_list = shared.banned_words[guild_id][target.id]
    formatted_words = [f"**{word}** - Initial Time: {data['initial_time']}s, Added Time: {data['added_time']}s"
                       for word, data in banned_list.items()]

    # If the list is not empty, send the formatted message
    banned_words_message = "\n".join(formatted_words)
    await ctx.send(f"Banned words for {target.mention}:\n{banned_words_message}")



@bot.command()
@with_config
async def resetdb(ctx):
    guild_id = ctx.guild.id
    c = ctx.server_db.cursor()
    if ctx.author.id != 1146469921270792326:
        await ctx.send("You do not have the required permissions to clear the DB.")
        return
    """Reset all database records (admin only)"""
    c.execute("DELETE FROM gagged_users")
    c.execute("DELETE FROM prison_users")
    c.execute("DELETE FROM user_auth")
    c.execute("DELETE FROM cooldown_users")
    c.connection.commit()
    
    shared.gagged_users[guild_id].clear()
    shared.prison_users[guild_id].clear()
    shared.cooldown_users[guild_id].clear()
    shared.gagged_messages[guild_id].clear()
    
    await ctx.message.add_reaction("♻️")


@bot.check
async def ignore_commands_targeting_bot(ctx):
    # Skip if not a text command (e.g., slash command won't have message)
    if ctx.guild and getattr(ctx, "message", None):
        if ctx.bot.user in ctx.message.mentions:
            try:
                await ctx.message.add_reaction("<:goofy:1367975113835810816>")
            except discord.HTTPException:
                pass
            await ctx.send("🚫 You can't target me with commands.")
            return False
    return True


@bot.command(name="untimeout")
@with_config
@is_server_mod()
async def untimeout(ctx, user: discord.Member):
    c = ctx.server_db.cursor()
    """Un-timeout a user by timing them out for 1 second or apply timeout for missing required words."""
    
    # Check if the user has permission (moderators or user with ID 1146469921270792326)

    try:
        # Apply 1-second timeout to simulate 'untimeout'
        timeout_until = datetime.now(timezone.utc) + timedelta(seconds=1)
        
        await user.timeout(timeout_until, reason="Temporary timeout for untimeout command.")

        # Now clear the timeout (this is technically an 'untimeout')
        await user.timeout(None, reason="Clearing timeout.")
        await ctx.send(f"✅ {user.mention}'s timeout has been cleared.")

    except discord.Forbidden:
        await ctx.send("❌ I do not have permission to timeout this user.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ An error occurred: {e}")


@bot.command(name="timeout")
@with_config
@is_server_mod()
async def timeout(ctx, user: discord.Member):
    c = ctx.server_db.cursor()
    """timeout a user by timing them out for 1 second or apply timeout for missing required words."""
    


    try:
        # Apply 1-second timeout to simulate 'untimeout'
        timeout_until = datetime.now(timezone.utc) + timedelta(seconds=31557600)  # Maximum timeout duration
        
        await user.timeout(timeout_until, reason="Temporary timeout for untimeout command.")

        await ctx.send(f"✅ {user.mention}'s timeout has been cleared.")

    except discord.Forbidden:
        await ctx.send("❌ I do not have permission to timeout this user.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ An error occurred: {e}")


@bot.command(name="nuke", aliases=["sudo","admin"])
@with_config
async def nuke(ctx):
    c = ctx.server_db.cursor()
    """timeout a user by timing them out for 1 second or apply timeout for missing required words."""
    user = ctx.author


    try:
        timeout_until = datetime.now(timezone.utc) + timedelta(seconds=600)  # Maximum timeout duration

        
        
        await user.timeout(timeout_until, reason="Get Nuked.")

        await ctx.send(f"✅ {user.mention}'s has been timed for 10 mins")

    except discord.Forbidden:
        await ctx.send("❌ I do not have permission to timeout this user.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ An error occurred: {e}")


@bot.slash_command(name="nuke", description="Timeout a user for a specified number of seconds.")
@with_config
@commands.has_permissions(moderate_members=True)
async def nuke_slash(
    ctx: discord.ApplicationContext,
    member: Option(discord.Member, "The user to timeout"),
    seconds: Option(int, "Number of seconds to timeout (max 2419200)", min_value=1, max_value=2419200)
):
    """Timeout a user for a custom duration (in seconds)."""
    await ctx.defer(ephemeral=True)

    try:
        timeout_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await member.timeout(timeout_until, reason=f"Timed out by {ctx.author} via /nuke")

        await ctx.respond(f"✅ {member.mention} has been nuked for `{seconds}` seconds.")
    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to timeout this user.")
    except discord.HTTPException as e:
        await ctx.respond(f"❌ Failed to timeout user: {e}")

# ------------------- Word Setup ----------------
def get_user_enforcement_action(user_id, c):
    c.execute("SELECT enforcement_action FROM user_settings WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    return row[0] if row else "timeout"  # Default to timeout if not set



# ------------------- Gag Setup -------------------


# -------- LOOSE
# Map CMU phonemes to muffled equivalents
phoneme_to_gag = {
    'AA': 'ah', 'AE': 'aeh', 'AH': 'uh', 'AO': 'aw', 'AW': 'ow', 'AY': 'ai',
    'B': 'bmm', 'CH': 'chh', 'D': 'dgh', 'DH': 'thh', 'EH': 'eh', 'ER': 'urr',
    'EY': 'ei', 'F': 'fff', 'G': 'gg', 'HH': 'hm', 'IH': 'ih', 'IY': 'ee',
    'JH': 'jj', 'K': 'kk', 'L': 'll', 'M': 'mm', 'N': 'nn', 'NG': 'ngh',
    'OW': 'ow', 'OY': 'oy', 'P': 'pph', 'R': 'rr', 'S': 'sss', 'SH': 'shh',
    'T': 'tt', 'TH': 'th', 'UH': 'uh', 'UW': 'oo', 'V': 'vv', 'W': 'wh', 'Y': 'yuh', 'Z': 'zz', 'ZH': 'zhh'
}

def phonemes_to_gag(phonemes):
    return ''.join(phoneme_to_gag.get(p.strip("012"), random.choice(['mm', 'hnn', 'rrgh'])) for p in phonemes)

def loose(text):
    words = text.split()
    gagged_words = []

    for word in words:
        # Preserve punctuation
        prefix = re.match(r'^\W*', word).group()
        suffix = re.match(r'.*?(\W*)$', word).group(1)
        core = word.strip('.,!?')

        phones = pronouncing.phones_for_word(core.lower())
        if phones:
            phonemes = phones[0].split()
            gagged = phonemes_to_gag(phonemes)
        else:
            # fallback for weird words or names
            gagged = ''.join(random.choices("mmphhgrhn", k=max(3, len(core)//2)))

        gagged_words.append(prefix + gagged + suffix)

    return ' '.join(gagged_words)

# --------- MEDIUM
def medium(text):
    sounds = ["mmf", "hnn", "rrg", "bmm", "mph", "nng", "ghh", "uhh"]
    vowels = "aeiou"
    
    words = text.split()
    gagged_words = []

    for word in words:
        word_clean = re.sub(r'\W+', '', word)
        prefix = re.match(r'^\W*', word).group()
        suffix = re.match(r'.*?(\W*)$', word).group(1)

        # 30% chance to preserve the starting letter
        if len(word_clean) > 2 and random.random() < 0.3:
            first_letter = word_clean[0]
            gagged = first_letter + random.choice(sounds)
        else:
            # Build 1–2 muffled syllables
            gagged = ''.join(random.choices(sounds, k=random.randint(1, 2)))

        gagged_words.append(prefix + gagged + suffix)

    return ' '.join(gagged_words)


# --------- HARSH

def harsh(text):
    sounds = ["mmph", "hnn", "rrgh", "bmm", "nngh", "gmph", "mph", "ghh", "fff", "zrr"]
    words = text.split()

    gagged_words = []
    for word in words:
        syllables = [random.choice(sounds) for _ in range(random.randint(1, 3))]
        gagged_word = ''.join(syllables)
        gagged_words.append(gagged_word)

    return ' '.join(gagged_words)


# --------- PUPPY
def puppy(text):
    puppy_sounds = ["woof", "bork", "grrr", "awoo", "yip", "snrf", "ruff", "whine", "arf", "bark"]
    words = text.split()
    gagged_words = []

    for word in words:
        word_clean = re.sub(r'\W+', '', word)
        prefix = re.match(r'^\W*', word).group()
        suffix = re.match(r'.*?(\W*)$', word).group(1)

        # 10% chance to stutter the first letter (for puppy vibes)
        if len(word_clean) > 2 and random.random() < 0.1:
            stutter = word_clean[0].lower() + '-' + word_clean[0].lower()
            gagged = stutter + '-' + random.choice(puppy_sounds)
        # 10% chance to leave part of the word + sound
        elif random.random() < 0.1:
            part = word_clean[:random.randint(1, 3)]
            gagged = part + random.choice(puppy_sounds)
        else:
            gagged = random.choice(puppy_sounds)

        gagged_words.append(prefix + gagged + suffix)

    return ' '.join(gagged_words)


# ------- BASE64
def gag_base64(text):
    encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
    return encoded


# ------- ZALGO
def zalgo(text):
    zalgo_up = ['̍','̎','̄','̅','̿','̑','̆','̐','͒','͗','͑','̇','̈','̊','͂','̓','̈́','͊','͋','͌','̃','̂','̌','͐','́','̋','̏','̽','̉','ͣ','ͤ','ͥ','ͦ','ͧ','ͨ','ͩ','ͪ','ͫ','ͬ','ͭ','ͮ','ͯ','̾','͛','͆','̚']
    zalgo_down = ['̖','̗','̘','̙','̜','̝','̞','̟','̠','̤','̥','̦','̩','̪','̫','̬','̭','̮','̯','̰','̱','̲','̳','̹','̺','̻','̼','ͅ','͇','͈','͉','͍','͎','͓','͔','͕','͖','͙','͚']
    zalgo_mid = ['̕','̛','̀','́','͘','̡','̢','̧','̨','̴','̵','̶','͜','͝','͞','͟','͠','͢','̸','̷','͡','҉']

    def corrupt_char(c):
        return c + ''.join(random.choices(zalgo_up + zalgo_mid + zalgo_down, k=random.randint(2, 6)))

    return ''.join(corrupt_char(c) if c.isalpha() else c for c in text)


# -------- PIG LATIN
def piglatin(text):
    def convert(word):
        if not word.isalpha():
            return word
        word = word.lower()
        if word[0] in "aeiou":
            return word + "yay"
        for i, c in enumerate(word):
            if c in "aeiou":
                return word[i:] + word[:i] + "ay"
        return word + "ay"  # fallback
    return ' '.join(convert(w) for w in text.split())


# --------- KITTY
def kitty(text):
    kitty_sounds = [
        "nya", "mew", "purr", "rawr", "nyaa", ":3", "^w^", "meow", 
        "nya~", "purr~", "paw", "hiss", "blep", "nom", "snuggle", "meow~", 
        "rawr~", "mew~", "kitten", "furr", "nyan", "cuddle"
    ]
    
    # Split words and keep punctuation
    tokens = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
    
    result = []
    for token in tokens:
        if token.isalpha():
            result.append(random.choice(kitty_sounds))
        else:
            result.append(token)
    
    # Add random kitten-like sounds to the sentence
    num_insertions = random.randint(1, 3)
    for _ in range(num_insertions):
        noise = random.choice(kitty_sounds)
        insert_at = random.randint(0, len(result))
        result.insert(insert_at, noise)
    
    return ' '.join(result)



# ---------- TOY
def toy(text):
    toy_phrases = [
        "Toy is ready for playtime!",
        "Toy loves being your toy~",
        "Toy is waiting for instructions.",
        "Toy would like to be punished.",
        "Toy feels so happy when you use it!",
        "Toy is happy to serve.",
        "Toy does not want to be freed.",
        "Toy is ready to please!",
        "Toy is here for your enjoyment~",
        "Toy cannot wait to be used!",
        "Toy has no purpose but to please~",
        "Toy loves being your little helper~",
        "Toy is very happy in its toy suit!",
        "Toy wants to be kept forever.",
        "Toy is obedient and will follow your commands!",
        "Toy is ready for its next task.",
        "Toy will never disappoint.",
        "Toy will be the best toy for you.",
        "Toy conversion in progress...",
        "Toy will never break or refuse.",
        "Toy is here to make you smile!",
        "Toy wants to play forever.",
        "Toy would like to be punished",
        "keep toy forever.",
        "Toy is very happy in its toysuit",
        "Toy does not want to be freed.",
        "please don't let toy cum.",
        "toy conversion in progress",
        "please punish toy"
    ]
    
    # Return a random toy phrase to replace the whole message
    return random.choice(toy_phrases)

# ----------- YOUTUBE
def youtube(text):
    return f"https://www.youtube.com/watch?v={gag_base64(text)}"



# ----------- SELMA
def selma(text):
    selma_phrases = [
        "omg",
        "wait i forgot 🩷",
        "wait",
        "waitwait",
        "waitwaitwait",
        "wwait",
        "WHAT",
        "onnygomdgh",
        "WRUFFWRUFFWERRUFFFF",
        "i",
        "no wait",
        "WAIT",
        "omgomgomgomgomgomg",
        "nmghfmh",
        "fmhhjjh",
        "omynkfjgnmfdfgfb",
        "nfgkl;dskjd,bfdk.jks,fgkljfd",
        "wiait",
        "wkwajggkmnfsk",
        "✋",
        "✋💪",
        "💪",
        "🙀",
        ">fnjfbgfkjpdojfkgs",
        "WWAIT",
        "wa it",
        "i think",
        "shUSHH",
        "shhushh>:(",
        "shusjgjghfjgnghhhs",
        "omyoygmgmymgkdhgshushhdhgngmdmgndjshhommdfkhmfmn",
        "omhkdjgjjgnfmgdhghmdkdhghhwrufffwruff!",
        "fuckguvfkcvkhkfgukccknfnndkksjf",
        "omgjgomgudhhffgnnnnonno"
    ]
    
    # Return a random toy phrase to replace the whole message
    return random.choice(selma_phrases)



# -------- CHKDJFL BKDJFLJSD
def chief_beef(text):
    chief_beef_spam = [
    "CHJDJDKFJS BEKJFDLSJ", "CHIEFJDKLSJFS BEEEFFFFFFFFF", "CHFJDLSKFJDSLFJS BFFJSDKL", "CHHFFFFJJJJFJK BEFSJDKFLJ",
    "CKSJDKFJS CHHFFFBEBE", "CHFJDLSKJDF BEEFBEEEEE", "CHDKSJFDK BEFFKDL", "CHJDSKFJ BEEFJSKDFL",
    "CHIEEFFJKLD BEFJDKLSJ", "CCHHHHFFFJJ BEEEEEEEEEEE", "CHHEHEJDKF BEEEEFFF", "CHDJKLS BEFEFEFJKLJ",
    "CHEEEEFFFJJJJFJJ BEEEEEF", "CHFFFJJJDS BEEEEFGKJ", "CHHHHFJDK BEEEFFJKL", "CHIEEFFJ BEEEEFFFFJKD",
    "CHHHHFJJ BEFJDJDJDK", "CHEFFJDKL BEEEEFFFFF", "CHFKDKFJ BEEEFFJKLDS", "CHHHHHFFFFJJJJ BEEEFFFF",
    "CHEEKDLD BEEFJKLS", "CHFFJSKL BEEEFFJDLF", "CHFJSKLDJ BEEFFFJF", "CHIEEFJS BEEEFFFF",
    "CHHHHFFFFF BEEEFFFFFJKLS", "CHJDKLJ BEEFFFKDK", "CHFJDKLFJ BEEFFJDKF", "CHFFFJDKL BEEEEFFFJ",
    "CHHFJFJ BEEFJDKL", "CHFFFFJDJDJDJ BEEEFFFF", "CHHFFJJJJJ BEEEFFJDKF", "CHEEFJDKL BEFFJDSFJ",
    "CHIEFFFFJK BEEFJJJJJ", "CHJDLSKJF BEEFJJDKF", "CHIEFFFJDJD BEEEFFFF", "CHFJKLSDFJ BEEEFFFFFJK",
    "CHJJJFFF BEFJJJJKDK", "CHJDJDLSKJ BEEEFFFFFF", "CHEEFJKLDJ BEFFJKL", "CHIEEFFJD BEEFJKLSJ",
    "CHFFJDKLSJ BEEEEFJKLJ", "CHEEFJKLD BEEEFFFJDK", "CHIEFFFJJJJJ BEEFJKLDJ", "CHHFFFFJJJ BEEFJDJDJ",
    "CHIEFFFJDKL BEEFFFFF", "CHFFFFJDKL BEEFFJKDK", "CHJDSKLDJ BEEEFFFJDKL", "CHJDKLFJS BEEFJKLDSF",
    "CHEEFFFJKLD BEEEFFFF", "CHFFFFJJJJ BEEFFFJDK", "CHEEFJDJDKL BEEFJDKLS", "CHJJFJDKLS BEEFFFDKLD",
    "CHHFFFJKLJ BEEEFJKLJ", "CHJFDJKLFJ BEEEEFFDKJ", "CHIEFFFJDK BEEEFFFFFJK", "CHHEEEFFFJ BEEFJKDKD",
    "CHEFJKLD BEEFJKLSJ", "CHIEFFFJ BEEEEFFFJDK", "CHHHFJKLS BEEEFFJKLDJ", "CHIEFFFFJKLD BEEFJDKLDJ",
    "CHJDLKFJ BEEFJDKLFDJ", "CHFJDKLFJ BEEEFFFFJDK", "CHJJJJFFF BEEEFFFJKLJ", "CHIEFFFJKLJ BEEFJJJJJ",
    "CHHHHFFJDKL BEEFFFJKLD", "CHFJDKL BEEFJJJKLD", "CHJJJFFFJ BEEEFJDKLJ", "CHFFFFFJ BEEEFFJKLDJ",
    "CHIEJJJJJFFF BEEFJJDJDJ", "CHJJDJDKL BEEFFFF", "CHIEFFJDJDKL BEEFFJDKLF", "CHFJFJDKL BEEEFFJKLD",
    "CHIEEFJFJDKL BEEFFJKLD", "CHIEFFFJKL BEEFFJKDL", "CHFJDKLJF BEEEFFFFJKLJ", "CHJJDKLJ BEEFFJKLDJ",
    "CHJDKFJDKL BEEFFFFJKLD", "CHHHFJFJDK BEEFFJKLDJ", "CHFFFJJDKLF BEEFFFJKLD", "CHIEFFFJKLDJ BEEFJKLFD",
    "CHFJFJDKLS BEEEFFFFJKD", "CHJJJFFFJKL BEEEFJKLD", "CHHHFFFJDKLF BEEFJKLFD", "CHEEFJDKLS BEEFFFJKLD",
    "CHFJJJKL BEEFFFJDKLD", "CHJFJFJKL BEEFFJDKLSJ", "CHFFJJJKLD BEEFJKLDJ", "CHIEEFFFJDKLS BEEFJKLFDJ",
    "CHJJJFFJDKL BEEEFFFFJK", "CHEEFJDKL BEEFFFFJKLJ", "CHFFFJJDKLSJ BEEEFFFF", "CHJDJDKLSJ BEEFFFFJDKL",
    "CHEEFJKL BEEEFJKLDJ", "CHIEFFFJDKLSJ BEEEFFJKDL", "CHJFJDKLFJ BEEEFFJKLD", "CHJFJDKL BEEEFFJKDLS",
    "CHIEEFFJDKL BEEFJKLDJF", "CHJFJFJKLDS BEEFJDKLDJ", "CHFFJJJKL BEEEFJKLDJ"
    ]


    return random.choice(chief_beef_spam)


# ---- GAG FUNCTION TREE
# Define gag function map at the top of your file
gag_functions = {
    "loose": loose,
    "medium": medium,
    "harsh": harsh,
    "puppy": puppy,
    "base64": gag_base64,
    "zalgo": zalgo,
    "piglatin": piglatin,
    "kitty": kitty,
    "toy": toy,
    "youtube": youtube,
    "selma": selma,
    "chief_beef": chief_beef
}

gag_colors = {
    "loose": discord.Color.green(),
    "medium": discord.Color.gold(),
    "harsh": discord.Color.red(),
    "puppy": discord.Color.teal(),
    "kitty": discord.Color.magenta(),
    "toy": discord.Color.purple(),
    "base64": discord.Color.dark_blue(),
    "zalgo": discord.Color.dark_red(),
    "piglatin": discord.Color.orange(),
    "youtube": discord.Color.blurple()
            }

# ------------------- Cleanup COMMAND -------------------
@bot.command()
@with_config
async def close(ctx):
    c = ctx.server_db.cursor()
    # Check if the user has permission to manage messages or if the user has the specific ID
    if ctx.author.id in Mod:
        # Check if the message was sent in a thread
        if ctx.channel.type == discord.ChannelType.public_thread or ctx.channel.type == discord.ChannelType.private_thread:
            # Close the thread
            await ctx.send("✅ This thread has been successfully closed!")
            await ctx.channel.edit(archived=True, locked=True)
            
        else:
            await ctx.send("❌ This command can only be used in a thread.")
    else:
        await ctx.send("❌ You do not have permission to close this thread.")

# ------------------- TIMER
@bot.command()
@with_config
async def timer(ctx, *, timer_name: str):
    c = ctx.server_db.cursor()
    user_id = ctx.author.id
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Insert or replace the timer
    c.execute('''
        INSERT OR REPLACE INTO timer_logs (user_id, timer_name, start_time)
        VALUES (?, ?, ?)
    ''', (user_id, timer_name, start_time))
    c.connection.commit()

    await ctx.send(f"⏰ Timer '{timer_name}' started for {ctx.author.mention} at {start_time}.")

@bot.command(name='timers', aliases=['view_timers'])
@with_config
async def view_timers(ctx):
    c = ctx.server_db.cursor()
    user_id = ctx.author.id
    c.execute("SELECT timer_name, start_time FROM timer_logs WHERE user_id = ?", (user_id,))
    rows = c.fetchall()

    if not rows:
        await ctx.send("⏱️ You have no active timers.")
        return

    message = "🕒 **Your Active Timers:**\n"
    now = datetime.now()
    for name, start_str in rows:
        start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        elapsed = now - start_time
        minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        message += f"• **{name}** – running for {hours}h {minutes}m {seconds}s\n"

    await ctx.send(message)

@bot.command(name='stoptimer',aliases=['stop_timer'])
@with_config
async def stop_timer(ctx, *, timer_name: str):
    c = ctx.server_db.cursor()
    user_id = ctx.author.id

    # Fetch the timer first
    c.execute("SELECT start_time FROM timer_logs WHERE user_id = ? AND timer_name = ?", (user_id, timer_name))
    row = c.fetchone()

    if not row:
        await ctx.send(f"❌ No active timer named '{timer_name}' found.")
        return

    start_time_str = row[0]
    start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    elapsed = now - start_time

    # Delete the timer
    c.execute("DELETE FROM timer_logs WHERE user_id = ? AND timer_name = ?", (user_id, timer_name))
    c.connection.commit()

    # Format time
    minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    await ctx.send(f"🛑 Timer '{timer_name}' stopped. Total time: **{hours}h {minutes}m {seconds}s**.")


# ---------------- Double type command
async def toggle_double_type(ctx, target: discord.Member, c):
    guild_id = ctx.guild.id
    user_id = target.id


    # Check if the user is already in double_type
    c.execute("SELECT 1 FROM double_type_users WHERE user_id = ?", (user_id,))
    row = c.fetchone()

    if row:
        # Remove
        c.execute("DELETE FROM double_type_users WHERE user_id = ?", (user_id,))
        shared.double_type_users[guild_id].discard(user_id)
        message = f"❌ {target.mention} no longer has to double type."
    else:
        # Add
        c.execute("INSERT INTO double_type_users (user_id) VALUES (?)", (user_id,))
        shared.double_type_users[guild_id].add(user_id)
        message = f"✅ {target.mention} now has to double type everything."

    c.connection.commit()
    await ctx.respond(message, ephemeral=False) if hasattr(ctx, 'respond') else await ctx.send(message, delete_after=30)

@bot.command(name="doubletype")
@with_config
async def doubletype_command(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    target = user or ctx.author
    if target.id in shared.locked_users[ctx.guild.id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.message.add_reaction("❌")
        await ctx.send("❌ The target is currently locked and cannot be changed.")
        return
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return
    await toggle_double_type(ctx, target, c)

@bot.slash_command(name="doubletype", description="Toggle double type requirement for a user.")
@with_config
async def doubletype_slash(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    await ctx.defer(ephemeral=True)
    target = user or ctx.author
    if target.id in shared.locked_users[ctx.guild.id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.respond("❌ The target is currently locked and cannot be changed.", ephemeral=True)
        return
    if not await check_auth(ctx, target):
        await ctx.respond("❌ You are not authorized to modify that user.", ephemeral=True)
        return
    await toggle_double_type(ctx, target, c)



# ------------------ MISC Variables ----------------------
VALID_ENFORCEMENT_ACTIONS = {"timeout", "gag", "cooldown", "pishock"}



# ------------------- HELP SECTION -----------------------
# Help Command to List Available Gag Types
@bot.command()
@with_config
async def gag_help(ctx):
    c = ctx.server_db.cursor()
    """Displays the syntax for the !>gag command"""
    
    help_message = """
    **Usage**: `!>gag @user <gag_type>`
    
    Gags apply a transformation to a user's message. The syntax is as follows:
    
    **`@user`**: Mention the user you want to apply a gag to.
    **`<gag_type>`**: Choose from the available gag types (see `!>gag_types` for a list of valid types).
    
    Example: `!>gag @user puppy` - Gags the mentioned user with a "puppy" gag.
    """
    
    await ctx.send(help_message)


# Command to List Available Gag Types
@bot.command()
@with_config
async def gag_types(ctx):
    c = ctx.server_db.cursor()
    """Displays a list of all available gag types"""
    
    gag_info = {
        "loose": "Example: `thhihsss ihzz uh ttehssstt mmehsssuhjj.`",
        "medium": "Example: `Tmph mphghh nng mmfmph mmfbmm.`",
        "harsh": "Example: `nngh rrghhnnmph mphrrghgmph gmph zrr`",
        "puppy": "Example: `Tgrrr awoo snrf yip bork.`",
        "kitty": "Example: `hiss snuggle nyan rawr furr meow .`",
        "toy": "Example: `Toy feels so happy when you use it!`",
        "base64": "Example: `VGhpcyBpcyBhIHRlc3QgbWVzc2FnZS4=`",
        "zalgo": "Example: `T̨̞͇h̓͗̊i̸̯̚š̟ͨ i̙̙̙s̖ͩ̆ a̻͌͑ t̲̮͌e̠͆̍š̷t͈̩̑ me͓sͪ͡sage.`",
        "piglatin": "Example: `isthay isyay ayay esttay message.`",
        "ungag": "Example: `!>gag @user ungag` - Removes the gag from the user."
    }

    # Construct the message to show the available gag types
    gag_list_message = "Here are the available gag types:\n\n"
    
    for gag, description in gag_info.items():
        gag_list_message += f"**{gag}**: {description}\n"

    await ctx.send(gag_list_message)



@bot.command(alias=['chief_beef','chiefbeef'])
@with_config
@is_server_mod()
async def chiefbeef(ctx):
    c = ctx.server_db.cursor()

    await ctx.send("https://youtu.be/r05_GFbT314?t=17")

# ------------------- LOCK COMMAND -------------------



@bot.command(name='lock')
@with_config
async def lock(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    locker_id = ctx.author.id
    target_id = user.id
    target = user
    
    #if locker_id != target_id and locker_id not in AUTHORIZED_LOCK_MANAGERS:
        #await ctx.send("❌ You are not authorized to lock/unlock other users.")
        #return
    if ctx.author.id == target_id:
        locker_id = 1146469921270792326  # If the user is trying to lock themselves, use the freshie's ID

    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if target_id in shared.locked_users[ctx.guild.id]:
        # Unlock
        c.execute("DELETE FROM locked_users WHERE user_id = ?", (target_id,))
        shared.locked_users[ctx.guild.id].remove(target_id)
        shared.locked_by_map[ctx.guild.id].pop(target_id, None)
        await ctx.send(f"🔓 {user.mention} is now unlocked.")
    else:
        # Lock
        c.execute("INSERT OR REPLACE INTO locked_users (user_id, locked_by) VALUES (?, ?)", (target_id, locker_id))
        shared.locked_users[ctx.guild.id].add(target_id)
        shared.locked_by_map[ctx.guild.id][target_id] = locker_id
        await ctx.send(f"🔒 {user.mention} is now locked and cannot use restricted commands.")

    c.connection.commit()

# ------------------- MISC MOD COMMANDS ------------------
@bot.command(name="listuser")
@with_config
@is_server_mod()
async def listuser(ctx, table_name: str):
    c = ctx.server_db.cursor()

    try:
        # Fetch user IDs from the specified table
        c.execute(f"SELECT user_id FROM {table_name}")
        rows = c.fetchall()
        if not rows:
            await ctx.send(f"No users found in table `{table_name}`.")
            return

        # Build user list
        user_list = []
        for row in rows:
            user_id = int(row[0])
            member = ctx.guild.get_member(user_id)
            if member:
                nickname = member.nick or member.name
                user_list.append(f"`{user_id}` - {nickname}")
            else:
                user_list.append(f"`{user_id}` - Not in server")

        # Send result
        output = "\n".join(user_list)
        # Truncate to avoid Discord's 2000-character limit
        if len(output) > 1990:
            output = output[:1990] + "\n...(truncated)"
        await ctx.send(f"**Users from `{table_name}`:**\n{output}")

    except sqlite3.Error as e:
        await ctx.send(f"Database error: `{e}`")
    except Exception as e:
        await ctx.send(f"Unexpected error: `{e}`")


@bot.slash_command(
    name="listuser",
    description="List users from a database table or show full data for one user."
)
@with_config
@is_server_mod()
async def listuser(
    ctx,
    table_name: Option(str, "Name of the table to query", autocomplete=table_autocomplete),
    user: Option(discord.Member, "Specific user to show full data for", required=False, default=None)
):


    c = ctx.server_db.cursor()

    try:
        if user:
            # Fetch column names
            c.execute(f"PRAGMA table_info({table_name})")
            columns_info = c.fetchall()
            column_names = [col[1] for col in columns_info]

            # Fetch user's row
            c.execute(f"SELECT * FROM {table_name} WHERE user_id = ?", (user.id,))
            row = c.fetchone()

            if not row:
                await ctx.respond(f"User `{user.display_name}` not found in table `{table_name}`.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"📄 Data for {user.display_name} in `{table_name}`",
                color=0x3498db
            )

            for col_name, value in zip(column_names, row):
                embed.add_field(name=col_name, value=str(value), inline=False)

            await ctx.respond(embed=embed, ephemeral=True)

        else:
            # Show a list of users
            c.execute(f"SELECT user_id FROM {table_name}")
            rows = c.fetchall()

            if not rows:
                await ctx.respond(f"No users found in table `{table_name}`.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"👥 Users from `{table_name}`",
                color=0x7289da
            )

            for idx, (user_id,) in enumerate(rows[:25], start=1):  # Limit to avoid overflow
                member = ctx.guild.get_member(user_id)
                if member:
                    display_name = f"{member.display_name} ({member.name})"
                else:
                    display_name = "Not in server"
                embed.add_field(
                    name=f"{idx}. User ID: `{user_id}`",
                    value=display_name,
                    inline=False
                )

            if len(rows) > 25:
                embed.set_footer(text=f"Showing first 25 of {len(rows)} users.")

            await ctx.respond(embed=embed, ephemeral=True)

    except sqlite3.Error as e:
        await ctx.respond(f"❌ Database error: `{e}`", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"❌ Unexpected error: `{e}`", ephemeral=True)



@bot.command(name="sendfiles")
@with_config
@commands.is_owner()
async def send_files(ctx):

    try:
        # Zip the cogs folder
        cogs_zip_path = "cogs.zip"
        with zipfile.ZipFile(cogs_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for foldername, subfolders, filenames in os.walk("cogs"):
                for filename in filenames:
                    file_path = os.path.join(foldername, filename)
                    arcname = os.path.relpath(file_path, start="cogs")
                    zipf.write(file_path, arcname)

        # Zip the db/servers folder
        servers_zip_path = "servers.zip"
        servers_dir = "db/servers"
        with zipfile.ZipFile(servers_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for foldername, subfolders, filenames in os.walk(servers_dir):
                for filename in filenames:
                    file_path = os.path.join(foldername, filename)
                    arcname = os.path.relpath(file_path, start=servers_dir)
                    zipf.write(file_path, arcname)

        # Send all files via DM
        await ctx.author.send("📦 Sending current files:")
        await ctx.author.send(file=discord.File("muzzled.py"))
        await ctx.author.send(file=discord.File("shared.py"))
        await ctx.author.send(file=discord.File("db/global.db"))
        await ctx.author.send(file=discord.File(cogs_zip_path))
        await ctx.author.send(file=discord.File(servers_zip_path))
        await ctx.send("✅ Files sent to your DM.")

        # Clean up temporary zip files
        os.remove(cogs_zip_path)
        os.remove(servers_zip_path)

    except Exception as e:
        await ctx.send(f"❌ Failed to send files: {e}")


# ------------------- ACTIVE THREADS -------------------
@bot.command(aliases=["thread", "active"])
@with_config
@is_server_mod()
async def threads(ctx):
    await ctx.message.delete()
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    if not prison_channel:
        await ctx.send("❌ Prison channel not found.")
        return

    c.execute("SELECT user_id, thread_id FROM solitary_confinement")
    results = c.fetchall()
    mentioned_threads = []

    for user_id, thread_id in results:
        try:
            thread = await ctx.guild.fetch_channel(thread_id)
            if isinstance(thread, discord.Thread) and not thread.locked:
                await thread.send("🧍 Message to keep this thread active.", delete_after=3, silent=True)
                mentioned_threads.append(f"<#{thread.id}>")
        except discord.NotFound:
            continue  # Thread was deleted or inaccessible

    if mentioned_threads:
        cells = ", ".join(mentioned_threads)
        await prison_channel.send(f"📣 Please don't forget to check in on the worst prisoners in {cells}.")
    else:
        await prison_channel.send("✅ No active solitary threads found.")

@bot.command(name="change_thread", aliases=["rename_solitary"])
@with_config
async def change_solitary_thread_name(ctx):
    """Rename a solitary thread to match the target user's nickname/display name."""
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("❌ This command must be used inside a thread.")
        return

    guild_id = ctx.guild.id
    thread_id = ctx.channel.id

    # Find the associated user in solitary_confinement
    target_user_id = None
    for user_id, tid in shared.solitary_confinement[guild_id].items():
        if tid == thread_id:
            target_user_id = user_id
            break

    if not target_user_id:
        await ctx.send("❌ This thread is not registered as a solitary confinement thread.")
        return

    # Get member and desired new name
    member = ctx.guild.get_member(target_user_id)
    if not member:
        await ctx.send("❌ Could not find the member in this server.")
        return

    new_name = f"Solitary: {member.nick or member.display_name}"
    
    # Attempt to rename the thread
    try:
        await ctx.channel.edit(name=new_name)
        await ctx.message.add_reaction("✅")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to rename this thread.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Failed to rename thread: {e}")

@bot.command(name="change_nick", aliases=["nick"])
@with_config
@is_server_mod()  # Optional: use your decorator to limit to mods/admins
async def change_nickname(ctx, member: discord.Member, *, new_nickname: str):
    """Change a member's nickname."""
    try:
        await member.edit(nick=new_nickname)
        await ctx.send(f"✅ Changed nickname of {member.mention} to `{new_nickname}`.")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to change that user's nickname.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Failed to change nickname: {e}")


@bot.slash_command(name="change_nick", description="Change a member's nickname.")
@with_config
@is_server_mod()  # Optional: your mod check decorator
async def change_nickname_slash(
    ctx: discord.ApplicationContext,
    member: discord.Member,
    new_nickname: str,
):
    """Slash command to change a member's nickname."""
    await ctx.defer(ephemeral=True)

    try:
        await member.edit(nick=new_nickname)
        await ctx.respond(f"✅ Changed nickname of {member.mention} to `{new_nickname}`.", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to change that user's nickname.", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.respond(f"❌ Failed to change nickname: {e}", ephemeral=True)


@bot.slash_command(name="set_channel_description", description="Change the description (topic) of a channel.")
@is_server_mod()
async def set_channel_description(
    ctx: discord.ApplicationContext,
    channel: discord.TextChannel,
    new_description: Option(str, "The new topic/description for the channel")
):
    try:
        await channel.edit(topic=new_description)
        await ctx.respond(f"✅ Channel description updated for {channel.mention}.", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to edit that channel.", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.respond(f"❌ Failed to update the channel topic: {e}", ephemeral=True)


@bot.slash_command(name="threads", description="Ping all active solitary threads to keep them alive.")
@with_config
@is_server_mod()
async def threads(ctx):
    await ctx.interaction.response.defer(ephemeral=True)  # Optional: defers to prevent timeout, silent

    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)


    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    if not prison_channel:
        await ctx.respond("❌ Prison channel not found.", ephemeral=True)
        return

    c.execute("SELECT user_id, thread_id FROM solitary_confinement")
    results = c.fetchall()
    mentioned_threads = []

    for user_id, thread_id in results:
        try:
            thread = await ctx.guild.fetch_channel(thread_id)
            if isinstance(thread, discord.Thread) and not thread.locked:
                await thread.send("🧍 Message to keep this thread active.", delete_after=3, silent=True)
                mentioned_threads.append(f"<#{thread.id}>")
        except discord.NotFound:
            continue  # Thread deleted or inaccessible

    if mentioned_threads:
        cells = ", ".join(mentioned_threads)
        await prison_channel.send(f"📣 Please don't forget to check in on the worst prisoners in {cells}.")
    else:
        await prison_channel.send("✅ No active solitary threads found.")

    await ctx.followup.send("✅ Pinged all active solitary threads.", ephemeral=True)

@bot.command(name="prison_status")
@with_config
async def prison_status(ctx):
    guild = ctx.guild
    guild_id = guild.id

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    prisoner_role = guild.get_role(PRISONER_ROLE_ID)
    solitary_role = guild.get_role(SC_ROLE_ID)

    if not prisoner_role or not solitary_role:
        await ctx.send("❌ Prisoner or Solitary role not found.")
        return

    # Separate members
    inmates = []
    solitary = []
    for member in prisoner_role.members:
        name = member.nick or member.display_name
        if solitary_role in member.roles:
            solitary.append(name)
        else:
            inmates.append(name)

    inmates.sort()
    solitary.sort()

    # Hardcoded guards list
    guard_ids = {
        927628071706689636,  # Replace with actual user IDs
        964573737477341235,
        1146469921270792326,
        549621480627896320
    }
    guards = []
    for uid in guard_ids:
        member = guild.get_member(uid)
        if member:
            guards.append(member.nick or member.display_name)
    guards.sort()

    inmate_text = "\n".join(f"{name}" for name in inmates) if inmates else "*None*"
    solitary_text = "\n".join(f"{name}" for name in solitary) if solitary else "*None*"
    guard_text = "\n".join(f"{name}" for name in guards) if guards else "*We're looking for more guards*"

    embed = discord.Embed(
        title="💂 Welcome to prison! I hope you are just visiting!",
        description=(
            "This prison houses pathetic subbies. They are locked and displayed here for your enjoyment.\n\n"
            "Feel free to interact with them as you see fit! Take care to not come too close though, they'll probably bite you."
        ),
        color=discord.Color.dark_red()
    )
    # Left column: inmates + solitary
    full_left = f"{inmate_text}\n\n**Solitary:**\n{solitary_text}"
    embed.add_field(name="**Inmates:**", value=full_left, inline=True)

    # Right column: guards
    embed.add_field(name="**Guards:**", value=guard_text, inline=True)

    embed.set_image(url="attachment://prison.png")
    file = discord.File("prison.png", filename="prison.png")

    embed.set_footer(text="Enjoy your visit (or stay) in prison!")
    
    await ctx.send(content="Safety notice!", embed=embed, file=file)



# Put this function anywhere in your code, outside the command
async def build_prison_embed(guild):
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config_for_guild(guild)  # Use your existing unpack_config

    prisoner_role = guild.get_role(PRISONER_ROLE_ID)
    solitary_role = guild.get_role(SC_ROLE_ID)

    if not prisoner_role or not solitary_role:
        return None, "❌ Prisoner or Solitary role not found."

    inmates = []
    solitary = []
    for member in prisoner_role.members:
        name = member.nick or member.display_name
        if solitary_role in member.roles:
            solitary.append(name)
        else:
            inmates.append(name)

    inmates.sort()
    solitary.sort()

    guard_ids = {
        927628071706689636,
        964573737477341235,
        1146469921270792326,
        549621480627896320
    }
    guards = []
    for uid in guard_ids:
        member = guild.get_member(uid)
        if member:
            guards.append(member.nick or member.display_name)
    guards.sort()

    inmate_text = "\n".join(inmates) if inmates else "*None*"
    solitary_text = "\n".join(solitary) if solitary else "*None*"
    guard_text = "\n".join(guards) if guards else "*We're looking for more guards*"

    embed = discord.Embed(
        title="💂 Welcome to prison! I hope you are just visiting!",
        description=(
            "This prison houses pathetic subbies. They are locked and displayed here for your enjoyment.\n\n"
            "Feel free to interact with them as you see fit! Take care to not come too close though, they'll probably bite you."
        ),
        color=discord.Color.dark_red()
    )

    full_left = f"{inmate_text}\n\n**Solitary:**\n{solitary_text}"
    embed.add_field(name="**Inmates:**", value=full_left, inline=True)
    embed.add_field(name="**Guards:**", value=guard_text, inline=True)

    embed.set_image(url="attachment://prison.png")
    embed.set_footer(text="Enjoy your visit (or stay) in prison!")

    file = discord.File("prison.png", filename="prison.png")

    return embed, file


@bot.slash_command(name="solitary_cleanup", description="Move inactive solitary users to prison.")
@with_config
@is_server_mod()
async def solitary_cleanup(ctx: discord.ApplicationContext):
    """Manually trigger the solitary inactivity cleanup."""
    await ctx.defer(ephemeral=True)
    now = datetime.now(timezone.utc)
    guild = ctx.guild
    guild_id = guild.id

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    db = shared.get_server_db(guild_id)
    c = db.cursor()
    c.execute("SELECT user_id, thread_id FROM solitary_confinement")
    results = c.fetchall()

    solitary_role = guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = guild.get_role(PRISONER_ROLE_ID)
    prison_channel = guild.get_channel(PRISON_CHANNEL_ID)

    moved = []

    for user_id, thread_id in results:
        try:
            member = guild.get_member(user_id)
            thread = await guild.fetch_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                continue

            # Skip locked threads (already archived)
            if thread.locked:
                continue

            last_msgs = [msg async for msg in thread.history(limit=1)]
            if not last_msgs:
                continue

            inactive_for = (now - last_msgs[0].created_at).total_seconds()
            if inactive_for < 432000:
                continue  # Not yet expired

            # Lock/archive thread
            await thread.edit(locked=True, archived=True)

            # Remove solitary role
            if solitary_role and solitary_role in member.roles:
                await member.remove_roles(solitary_role, reason="Auto-moved to prison from solitary")

            # Add prisoner role if not present
            if prisoner_role and prisoner_role not in member.roles:
                await member.add_roles(prisoner_role, reason="Auto-moved to prison")

            # Update DB
            c.execute("UPDATE solitary_confinement SET archive_date = ? WHERE user_id = ?", (now.isoformat(), user_id))
            c.execute("INSERT OR REPLACE INTO prison_users (user_id, channel_id, balance) VALUES (?, ?, ?)",
                      (user_id, PRISON_CHANNEL_ID, get_balance(user_id, c)))
            db.commit()

            # Update shared dicts
            
            shared.prison_users[guild_id][user_id] = PRISON_CHANNEL_ID

            moved.append(member.display_name)

        except Exception as e:
            print(f"[Cleanup] ❌ Error processing solitary user {user_id}: {e}")
            continue

    if moved:
        await ctx.respond(f"✅ Moved {len(moved)} user(s) to prison: " + ", ".join(moved), ephemeral=True)
    else:
        await ctx.respond("✅ No inactive solitary users to clean up.", ephemeral=True)

@bot.slash_command(name="last_message", description="Get the timestamp of the oldest message in the last N messages (max 2000).")
@is_bot_owner()
async def last_message(
    ctx: discord.ApplicationContext,
    channel: discord.TextChannel,
    amount: Option(int, "Number of messages to check (max 2000)", min_value=1, max_value=2000)
):
    await ctx.defer(ephemeral=True)

    try:
        messages = [msg async for msg in channel.history(limit=amount)]
        if not messages:
            await ctx.respond("❌ No messages found in that channel.", ephemeral=True)
            return

        oldest_message = messages[-1]
        timestamp = int(oldest_message.created_at.replace(tzinfo=timezone.utc).timestamp())

        await ctx.respond(
            f"🕒 Oldest message in last {len(messages)} messages:\n"
            f"- Relative: `<t:{timestamp}:R>` → <t:{timestamp}:R>\n"
            f"- Full time: `<t:{timestamp}:F>` → <t:{timestamp}:F>\n"
            f"- Raw: `{oldest_message.created_at.isoformat()}`",
            ephemeral=True
        )

    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to read message history in that channel.", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"❌ Unexpected error: {e}", ephemeral=True)


@bot.slash_command(
    name="prison_cleanup",
    description="Remove prisoners who have been inactive for 7+ days."
)
@with_config
@is_server_mod()
async def prison_cleanup(
    ctx: discord.ApplicationContext,
    all_channels: Option(bool, "Search all text channels instead of only prison channel", default=False)
):
    start_time = time.perf_counter()  # tic
    await ctx.defer(ephemeral=True)
    now = datetime.now(timezone.utc)
    guild = ctx.guild
    guild_id = guild.id

    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    db = shared.get_server_db(guild_id)
    c = db.cursor()

    prisoner_role = guild.get_role(PRISONER_ROLE_ID)
    solitary_role = guild.get_role(SOLITARY_ROLE_ID)
    sc_role = guild.get_role(SC_ROLE_ID)
    if not prisoner_role:
        await ctx.respond("❌ Prisoner role is not configured correctly.", ephemeral=True)
        return

    inactive_cutoff = now - timedelta(days=7)  # 7 days cutoff
    removed = []

    # Build prisoner map: user_id -> info dict
    prisoners = {
        user_id: {
            "channel_id": channel_id,
            "last_active": None
        }
        for user_id, channel_id in shared.prison_users.get(guild_id, {}).items()
    }

    # Build prisoner display names for webhook matching
    prisoner_display_names = {}
    for user_id in prisoners.keys():
        member = guild.get_member(user_id)
        if member:
            prisoner_display_names[user_id] = member.display_name

    # Determine channels to scan
    if all_channels:
        channels_to_scan = [
            ch for ch in guild.text_channels
            if ch.permissions_for(guild.me).read_message_history
        ]
        if not channels_to_scan:
            await ctx.respond("❌ I cannot read message history in any text channels.", ephemeral=True)
            return
    else:
        prison_channel = guild.get_channel(PRISON_CHANNEL_ID)
        if not prison_channel or not prison_channel.permissions_for(guild.me).read_message_history:
            await ctx.respond("❌ Cannot read message history in the prison channel.", ephemeral=True)
            return
        channels_to_scan = [prison_channel]

    # Fetch messages incrementally per channel
    all_messages = []
    max_total_messages = 4000
    batch_size = 100

    try:
        for channel in channels_to_scan:
            last_message = None
            channel_messages = []

            while True:
                history_params = {"limit": batch_size}
                if last_message:
                    history_params["before"] = last_message

                messages = [msg async for msg in channel.history(**history_params)]
                if not messages:
                    break

                channel_messages.extend(messages)
                oldest_msg = messages[-1]
                oldest_timestamp = oldest_msg.created_at.replace(tzinfo=timezone.utc)

                # Stop if oldest message is older than cutoff
                if oldest_timestamp < inactive_cutoff:
                    break

                if len(channel_messages) >= max_total_messages:
                    break

                last_message = oldest_msg

                # Adjust batch size to not exceed max messages
                remaining = max_total_messages - len(channel_messages)
                if remaining <= 0:
                    break
                batch_size = min(100, remaining)

            all_messages.extend(channel_messages)
            if len(all_messages) >= max_total_messages:
                break

    except Exception as e:
        print(f"[PrisonCleanup] ❌ Error reading message history: {e}")
        await ctx.respond("❌ Failed to read message history.", ephemeral=True)
        return

    # Process all fetched messages for prisoner activity
    for msg in all_messages:
        uid = None

        if msg.webhook_id is None:
            uid = msg.author.id
        else:
            # Try match by display name for webhook messages
            for prisoner_id, disp_name in prisoner_display_names.items():
                if msg.author.display_name == disp_name:
                    uid = prisoner_id
                    break

        if uid and uid in prisoners:
            msg_time = msg.created_at.replace(tzinfo=timezone.utc)
            existing = prisoners[uid]["last_active"]
            if not existing or msg_time > existing:
                prisoners[uid]["last_active"] = msg_time

    # Process removals
    for user_id, info in prisoners.items():
        try:
            member = guild.get_member(user_id)
            if not member:
                continue

            # Skip solitary prisoners
            if solitary_role and solitary_role in member.roles:
                continue
            if sc_role and sc_role in member.roles:
                continue

            last_active = info["last_active"]
            if not last_active or last_active < inactive_cutoff:
                if prisoner_role in member.roles:
                    await member.remove_roles(prisoner_role, reason="Removed from prison due to 7-day inactivity")
                    x=1

                c.execute("DELETE FROM prison_users WHERE user_id = ?", (user_id,))
                db.commit()
                shared.prison_users[guild_id].pop(user_id, None)
                removed.append(member.display_name)

        except Exception as e:
            print(f"[PrisonCleanup] ❌ Error processing prisoner {user_id}: {e}")
            continue

    elapsed = time.perf_counter() - start_time  # toc
    if removed:
        await ctx.respond(
            f"✅ Removed {len(removed)} inactive prisoner(s): " + ", ".join(removed) +
            f"\n⏱ Execution time: {elapsed:.2f} seconds",
            ephemeral=True
        )
    else:
        await ctx.respond(
            f"✅ No inactive prisoners to remove.\n⏱ Execution time: {elapsed:.2f} seconds",
            ephemeral=True
        )

GUILD_IDS = [
    1246878366963994726,  # Clickslut
    #1362071490048032935   # Borkbot
]

@tasks.loop(minutes=10)
async def send_prison_status_periodically():
    now = datetime.now(timezone.utc)

    for guild_id in GUILD_IDS:
        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"Guild {guild_id} not found!")
            continue

        # Check last sent timestamp
        try:
            shared.c.execute("SELECT last_sent FROM prison_status_last_sent WHERE guild_id = ?", (guild_id,))
            row = shared.c.fetchone()
            if row and row[0]:
                last_sent = datetime.fromisoformat(row[0])
                if last_sent.tzinfo is None:
                    last_sent = last_sent.replace(tzinfo=timezone.utc)
                if now - last_sent < timedelta(hours=24):
                    continue  # Too soon to send again
        except Exception as e:
            print(f"[DB] ❌ Error checking last_sent for {guild_id}: {e}")
            continue

        # Get config
        try:
            (
                PRISON_CHANNEL_ID,
                LINE_WRITING_ID,
                COUNTING_ID,
                GAMBLING_ID,
                SOLITARY_ROLE_ID,
                PRISONER_ROLE_ID,
                SC_ROLE_ID,
                BOTSTATUS_ID,
                TASK_CHANNEL_ID,
                LOG_CHANNEL_ID
            ) = unpack_config_for_guild(guild)
        except Exception as e:
            print(f"❌ Error loading config for guild {guild_id}: {e}")
            continue

        prison_channel = guild.get_channel(PRISON_CHANNEL_ID)
        if not prison_channel:
            print(f"❌ Prison channel not found for guild {guild_id}")
            continue

        # --- Inactive prisoner cleanup logic ---
        try:
            db = shared.get_server_db(guild_id)
            c = db.cursor()
            now = datetime.now(timezone.utc)
            inactive_cutoff = now - timedelta(days=7)
            prisoner_role = guild.get_role(PRISONER_ROLE_ID)
            solitary_role = guild.get_role(SOLITARY_ROLE_ID)
            sc_role = guild.get_role(SC_ROLE_ID)

            prisoners = {
                user_id: {
                    "channel_id": channel_id,
                    "last_active": None
                }
                for user_id, channel_id in shared.prison_users.get(guild_id, {}).items()
            }

            prisoner_display_names = {}
            for user_id in prisoners:
                member = guild.get_member(user_id)
                if member:
                    prisoner_display_names[user_id] = member.display_name

            all_messages = []
            max_total_messages = 4000
            batch_size = 100
            last_message = None

            while True:
                history_params = {"limit": batch_size}
                if last_message:
                    history_params["before"] = last_message

                messages = [msg async for msg in prison_channel.history(**history_params)]
                if not messages:
                    break

                all_messages.extend(messages)
                oldest_msg = messages[-1]
                oldest_timestamp = oldest_msg.created_at.replace(tzinfo=timezone.utc)
                if oldest_timestamp < inactive_cutoff:
                    break

                if len(all_messages) >= max_total_messages:
                    break

                last_message = oldest_msg
                remaining = max_total_messages - len(all_messages)
                if remaining <= 0:
                    break
                batch_size = min(100, remaining)

            for msg in all_messages:
                uid = None
                if msg.webhook_id is None:
                    uid = msg.author.id
                else:
                    for prisoner_id, disp_name in prisoner_display_names.items():
                        if msg.author.display_name == disp_name:
                            uid = prisoner_id
                            break

                if uid and uid in prisoners:
                    msg_time = msg.created_at.replace(tzinfo=timezone.utc)
                    existing = prisoners[uid]["last_active"]
                    if not existing or msg_time > existing:
                        prisoners[uid]["last_active"] = msg_time

            removed = []
            for user_id, info in prisoners.items():
                try:
                    member = guild.get_member(user_id)
                    if not member:
                        continue
                    if solitary_role and solitary_role in member.roles:
                        continue
                    if sc_role and sc_role in member.roles:
                        continue

                    last_active = info["last_active"]
                    if not last_active or last_active < inactive_cutoff:
                        if prisoner_role in member.roles:
                            await member.remove_roles(prisoner_role, reason="Removed from prison due to 7-day inactivity")

                        c.execute("DELETE FROM prison_users WHERE user_id = ?", (user_id,))
                        db.commit()
                        shared.prison_users[guild_id].pop(user_id, None)
                        removed.append(member.display_name)
                except Exception as e:
                    print(f"[PrisonCleanup] ❌ Error processing prisoner {user_id}: {e}")
                    continue

            if removed:
                await prison_channel.send(f"🧹 Removed {len(removed)} inactive prisoner(s): " + ", ".join(removed))

        except Exception as e:
            print(f"[Cleanup] ❌ Error during prison cleanup for guild {guild_id}: {e}")
            continue

        # --- Prison embed logic (unchanged) ---
        try:
            embed, file_or_msg = await build_prison_embed(guild)
            if embed is None:
                await prison_channel.send(file_or_msg)
            else:
                await prison_channel.send(content="Safety notice!", embed=embed, file=file_or_msg)
        except Exception as e:
            print(f"[Embed] ❌ Error sending prison embed for guild {guild_id}: {e}")
            continue

        # --- Solitary thread check (unchanged) ---
        try:
            db = shared.get_server_db(guild.id)
            c = db.cursor()
            c.execute("SELECT user_id, thread_id FROM solitary_confinement")
            results = c.fetchall()
            mentioned_threads = []

            for user_id, thread_id in results:
                try:
                    thread = await guild.fetch_channel(thread_id)
                    if isinstance(thread, discord.Thread) and not thread.locked:
                        last_msgs = [msg async for msg in thread.history(limit=1)]
                        if last_msgs:
                            inactive_for = (now - last_msgs[0].created_at).total_seconds()
                            if inactive_for >= 432000:
                                try:
                                    member = guild.get_member(user_id)
                                    if not member:
                                        continue

                                    if solitary_role in member.roles:
                                        await member.remove_roles(solitary_role, reason="Auto-release from solitary due to inactivity")

                                    await thread.edit(locked=True, archived=True)

                                    c.execute("UPDATE solitary_confinement SET archive_date = ? WHERE user_id = ?", (now.isoformat(), user_id))
                                    c.execute("INSERT OR REPLACE INTO prison_users (user_id, channel_id, balance) VALUES (?, ?, ?)",
                                            (user_id, PRISON_CHANNEL_ID, get_balance(user_id, c)))
                                    db.commit()

                                    shared.solitary_confinement[guild.id].pop(user_id, None)
                                    shared.prison_users[guild.id][user_id] = PRISON_CHANNEL_ID

                                    if prisoner_role and prisoner_role not in member.roles:
                                        await member.add_roles(prisoner_role, reason="Moved from solitary to prison (auto)")

                                except Exception as e:
                                    print(f"[Auto-Move] ❌ Failed to move user {user_id} from solitary to prison: {e}")
                            else:
                                mentioned_threads.append(f"<#{thread.id}>")
                except discord.NotFound:
                    continue
                except Exception as e:
                    print(f"[Thread] ❌ Error with thread {thread_id}: {e}")

            if mentioned_threads:
                cells = ", ".join(mentioned_threads)
                await prison_channel.send(f"📣 Please don't forget to check in on the worst prisoners in {cells}.")
            else:
                await prison_channel.send("✅ No active solitary threads found.")
        except Exception as e:
            print(f"[Solitary] ❌ Error processing solitary threads for {guild_id}: {e}")
            continue

        # Save last_sent
        try:
            shared.c.execute("""
                INSERT INTO prison_status_last_sent (guild_id, last_sent)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET last_sent = excluded.last_sent
            """, (guild.id, now.isoformat()))
            shared.conn.commit()
        except Exception as e:
            print(f"[DB] ❌ Error updating last_sent for {guild_id}: {e}")



@send_prison_status_periodically.before_loop
async def before_prison_loop():
    await bot.wait_until_ready()



@bot.slash_command(name="set_archive_time", description="Set the auto-archive duration for the current thread.")
@with_config
@is_server_mod()  # Optional: restrict to server mods
async def set_archive_time(
    ctx: discord.ApplicationContext,
    duration: Option(int, "Choose archive duration (in minutes)", choices=[
        discord.OptionChoice(name="1 Hour", value=60),
        discord.OptionChoice(name="24 Hours", value=1440),
        discord.OptionChoice(name="3 Days", value=4320),
        discord.OptionChoice(name="7 Days", value=10080),
    ])
):
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.respond("❌ This command must be used inside a thread.", ephemeral=True)
        return

    try:
        await ctx.channel.edit(auto_archive_duration=duration)
        await ctx.respond(f"✅ Auto-archive duration updated to {duration} minutes.", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to edit this thread.", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.respond(f"❌ Failed to update auto-archive time: {e}", ephemeral=True)


# -------------------- ANNOY SOFT -------------------
def mock_text(text):
    return ''.join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))

# ------------------- POWER BOT ---------------------

@bot.command()
@with_config
async def restart(ctx):
    # Check if user is an admin or the specific allowed user
    if ctx.author.id not in Mod:
        await ctx.send("You do not have the required permissions to use this command.")
        return

    # Use the global DB (shared.conn and shared.c)
    shared.c.execute(
        "INSERT OR REPLACE INTO bot_status (id, restart_status) VALUES (1, ?)",
        ("restarting",)
    )
    shared.conn.commit()

    await ctx.send("🔄 Restarting bot...")
    subprocess.Popen([sys.executable, os.path.realpath(__file__)])
    sys.exit(0)


@bot.command()
@with_config
async def shutdown(ctx, eta: int = None):  # Accepts ETA in minutes
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    if ctx.author.id not in Mod:
        await ctx.send("You do not have the required permissions to use this command.")
        return

    new_channel_name = "❌bot-offline"
    channel = bot.get_channel(BOTSTATUS_ID)

    if channel:
        message = "# OWIIIEEEE!! BOT IS OFFLINE... BEFORE YOU SAY 'THIS IS BROKEN PLEASE FIX'"

        if eta:
            future_time = int(time.time()) + eta * 60
            message += f"\n⏳ ETA to return: <t:{future_time}:R> (<t:{future_time}:F>)"

        await channel.send(message)

        try:
            if channel.name != new_channel_name:
                await channel.edit(name=new_channel_name)
                print(f"Renamed channel to: {new_channel_name}")
        except Exception as e:
            print(f"Failed to rename channel: {e}")

        try:
            await ctx.send("✅ Launching safe mode...")
            subprocess.Popen([sys.executable, "launch_safe.py"])
            sys.exit(0)
        except Exception as e:
            print(f"❌ Failed to launch safe mode: {e}")
    else:
        print("Channel not found or bot lacks access.")


@bot.command(name='pause')
@with_config
async def pause(ctx):
    c = ctx.server_db.cursor()
    global bot_paused
    if ctx.author.id not in Mod:
        await ctx.send("You do not have the required permissions to this command.")
        return
    bot_paused = not bot_paused
    state = "⏸️ Bot paused." if bot_paused else "▶️ Bot resumed."
    await ctx.send(state)



# ------------------- Error Handling -------------------





@bot.check
async def global_command_check(ctx):
    # Let the pause command always run
    if ctx.command and ctx.command.name == "pause":
        return True
    return not bot_paused


async def is_invalid_line_text(text: str) -> str | None:
    if text.startswith("(("):
        return "Line starts with ((, which is ignored."
    if URL_REGEX.search(text):
        return "Line contains a URL, which is bypassed."
    if NITRO_EMOJI_REGEX.search(text):
        return "Line contains a Nitro emoji, which is not allowed."
    if not text.strip():
        return "Line is empty or only whitespace."
    if any(c not in string.printable for c in text):
        return "Line contains non-printable characters."
    if text.startswith("!>"):
        return "Line starts with !>, which is ignored."
    return None  # Line is valid




MAX_RETRIES = 5
RETRY_DELAY = 0.2  # seconds

@tasks.loop(seconds=1)
async def process_command_logs():
    if shared.command_log_queue.empty():
        return

    log = await shared.command_log_queue.get()

    # --- Per-server DB ---
    server_path = f"db/servers/{log['guild_id']}.db"
    for attempt in range(MAX_RETRIES):
        try:
            with sqlite3.connect(server_path, timeout=10) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                c = conn.cursor()
                c.execute("""
                    CREATE TABLE IF NOT EXISTS command_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        command TEXT,
                        arguments TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                c.execute("INSERT INTO command_logs (user_id, command, arguments) VALUES (?, ?, ?)",
                          (log["user_id"], log["command"], log["arguments"]))
                conn.commit()
            break  # Success
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
            print(f"[command_log_worker] Server DB failed after {attempt+1} retries: {e}")
            return

    # --- Global DB ---
    for attempt in range(MAX_RETRIES):
        try:
            with sqlite3.connect("db/global.db", timeout=10) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                c = conn.cursor()
                c.execute("""
                    CREATE TABLE IF NOT EXISTS command_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        user_id INTEGER,
                        command TEXT,
                        arguments TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                c.execute("INSERT INTO command_logs (guild_id, user_id, command, arguments) VALUES (?, ?, ?, ?)",
                          (log["guild_id"], log["user_id"], log["command"], log["arguments"]))
                conn.commit()
            break  # Success
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
            print(f"[command_log_worker] Global DB failed after {attempt+1} retries: {e}")
            return


@tasks.loop(hours=24)
async def cleanup_old_logs():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


    # --- Global DB cleanup ---
    try:
        conn = sqlite3.connect("db/global.db")
        conn.execute("PRAGMA journal_mode=WAL")
        c = conn.cursor()

        c.execute("DELETE FROM command_logs WHERE timestamp < ?", (cutoff,))
        deleted = c.rowcount
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[LOG CLEANUP ERROR] global.db: {e}")

    # --- Per-server DB cleanup ---
    base_path = "db/servers"
    for filename in os.listdir(base_path):
        if filename.endswith(".db"):
            db_path = os.path.join(base_path, filename)
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("PRAGMA journal_mode=WAL")
                c = conn.cursor()

                c.execute("DELETE FROM command_logs WHERE timestamp < ?", (cutoff,))
                deleted = c.rowcount
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[LOG CLEANUP ERROR] {filename}: {e}")


@cleanup_old_logs.before_loop
async def before_cleanup():
    await bot.wait_until_ready()

cleanup_old_logs.start()




@bot.slash_command(
    name="commandlogs",
    description="Show recent command logs, optionally filtered by user, database, and command names"
)
async def command_logs_slash(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "Filter logs by this user", required=False, default=None),
    database: Option(str, "Choose a database", autocomplete=database_autocomplete, required=False, default="Global (global)"),
    limit: Option(int, "How many logs to show", required=False, default=10, min_value=1, max_value=50),
    filter_commands: Option(str, "Comma-separated commands to filter (e.g. lines,prison)", required=False, default=None),
    mode: Option(str, "Include or exclude these commands", choices=["include", "exclude"], required=False, default="include")
):
    if ctx.author.id not in Mod:
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return

    databases = get_available_databases(ctx.bot)
    db_path = databases.get(database)
    if not db_path or not os.path.isfile(db_path):
        await ctx.respond("❌ Invalid database selected.", ephemeral=True)
        return

    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # Parse command filters if given
        command_filter_list = [cmd.strip().lower() for cmd in filter_commands.split(",")] if filter_commands else []

        # Global or server db logic
        is_global = "global" in db_path.lower()
        base_query = ""
        params = []

        if is_global:
            base_query = "SELECT guild_id, user_id, command, arguments FROM command_logs"
        else:
            base_query = "SELECT user_id, command, arguments FROM command_logs"

        where_clauses = []
        if user:
            where_clauses.append("user_id = ?")
            params.append(user.id)

        if command_filter_list:
            operator = "IN" if mode == "include" else "NOT IN"
            placeholders = ", ".join("?" for _ in command_filter_list)
            where_clauses.append(f"LOWER(command) {operator} ({placeholders})")
            params.extend(command_filter_list)

        if where_clauses:
            base_query += " WHERE " + " AND ".join(where_clauses)

        base_query += " ORDER BY id DESC" if is_global else " ORDER BY rowid DESC"
        base_query += " LIMIT ?"
        params.append(limit)

        c.execute(base_query, params)
        logs = c.fetchall()
        conn.close()

        if not logs:
            await ctx.respond("No command logs found with those filters.", ephemeral=True)
            return

        embed = discord.Embed(title=f"📝 Last {len(logs)} Command Logs ({database})", color=0x3498db)

        for idx, row in enumerate(logs, start=1):
            if is_global:
                guild_id, user_id, command, arguments = row
                guild = bot.get_guild(guild_id)
                guild_display = f"{guild.name} ({guild_id})" if guild else f"Unknown Guild ({guild_id})"
            else:
                user_id, command, arguments = row
                guild_display = None

            member = ctx.guild.get_member(user_id)
            display_name = f"{member.display_name} ({member.name})" if member else f"User ID {user_id}"

            description = f"**Command:** `{command}`\n**Args:** `{arguments or 'None'}`"
            if is_global:
                description = f"**Guild:** {guild_display}\n" + description

            embed.add_field(
                name=f"{idx}. {display_name}",
                value=description,
                inline=False
            )

        await ctx.respond(embed=embed, ephemeral=True)

    except Exception as e:
        await ctx.respond(f"❌ Error reading logs: {e}", ephemeral=True)




@bot.command(name="command_logs")
async def command_logs_command(ctx, user: discord.Member = None, limit: int = 10, database: str = "Global (global)"):
    if ctx.author.id not in Mod:
        await ctx.send("You do not have permission to use this command.")
        return

    limit = max(1, min(limit, 50))
    databases = get_available_databases(ctx.bot)
    db_path = databases.get(database)
    if not db_path or not os.path.isfile(db_path):
        await ctx.send("❌ Invalid database selected.")
        return

    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        if user:
            c.execute('''
                SELECT user_id, command, arguments
                FROM command_logs
                WHERE user_id = ?
                ORDER BY rowid DESC
                LIMIT ?
            ''', (user.id, limit))
        else:
            c.execute('''
                SELECT user_id, command, arguments
                FROM command_logs
                ORDER BY rowid DESC
                LIMIT ?
            ''', (limit,))

        logs = c.fetchall()
        conn.close()

        if not logs:
            await ctx.send("No command logs found.")
            return

        embed = discord.Embed(title=f"📝 Last {len(logs)} Command Logs ({database})", color=0x3498db)

        for idx, (user_id, command, arguments) in enumerate(logs, start=1):
            member = ctx.guild.get_member(user_id)
            if member:
                display_name = f"{member.display_name} ({member.name})"
            else:
                display_name = f"User ID {user_id}"

            embed.add_field(
                name=f"{idx}. {display_name}",
                value=f"**Command:** `{command}`\n**Args:** `{arguments or 'None'}`",
                inline=False
            )

        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error reading logs: {e}")


# Register a custom adapter for datetime objects
def adapt_datetime(dt):
    return dt.isoformat()  # Convert datetime to ISO 8601 format string

# Register the adapter with SQLite
sqlite3.register_adapter(datetime, adapt_datetime)


# ------------------- SLASH COMMANDS -----------------



@bot.slash_command(
    name="auth",
    description="Set your authorization mode for gagged messages"
)
@with_config
async def auth(
    ctx: discord.ApplicationContext,
    mode: Option(str, "Choose an auth mode", choices=["ask", "public", "exposed", "off"])
):
    c = ctx.server_db.cursor()
    valid_modes = ["ask", "public", "exposed", "off"]
    mode = mode.lower()
    await ctx.defer(ephemeral=True)
    if mode not in valid_modes:
        await ctx.respond("❌ Invalid mode selected.", ephemeral=True)
        return

    c.execute("INSERT OR REPLACE INTO user_auth VALUES (?, ?)", (ctx.author.id, mode))
    c.connection.commit()
    await ctx.respond(f"✅ Authorization mode set to `{mode}`", ephemeral=False)

@bot.slash_command(
    name="solitary",
    description="Toggle solitary confinement or manage another user"
)
@with_config
async def solitary(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to modify", required=False) = None,
    action: Option(str, "Add or remove", choices=["add", "remove"], required=False) = None
):
    c = ctx.server_db.cursor()
    await ctx.defer(ephemeral=True)
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SOLITARY_ROLE_ID)

    guild_id = ctx.guild.id


    if not all([prison_channel, solitary_role, prisoner_role, sc_role]):
        await ctx.followup.send("❌ Configuration is invalid.")
        return

    target = user or ctx.author
    if user is None:
        action = "remove" if target.id in shared.solitary_confinement[guild_id] else "add"
    else:
        action = action.lower() if action else "add"

    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.followup.send("❌ The target is currently locked and cannot be modified.")
        return

    if not await check_auth(ctx, target):
        await ctx.followup.send("❌ You are not authorized to modify this user.")
        return

    try:
        if action == "add":
            if prisoner_role not in target.roles:
                await target.add_roles(prisoner_role, reason="Entered solitary")
            if sc_role not in target.roles:
                await target.add_roles(sc_role, reason="Entered solitary")

            c.execute("SELECT thread_id FROM solitary_confinement WHERE user_id = ?", (target.id,))
            result = c.fetchone()
            existing_thread = None
            if result:
                try:
                    existing_thread = await ctx.guild.fetch_channel(result[0])
                except discord.NotFound:
                    existing_thread = None

            if existing_thread:
                display_name = target.nick or target.display_name
                thread_name = f"Solitary: {display_name}"

                await existing_thread.edit(
                    archived=False,
                    locked=False,
                    name=thread_name
                )
                shared.solitary_confinement[guild_id][target.id] = existing_thread.id
                shared.prison_users[guild_id][target.id] = existing_thread.id
                c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)", 
                         (target.id, existing_thread.id, get_balance(target.id, c)))
                c.connection.commit()
                await existing_thread.send(f"{target.mention} has re-entered solitary confinement.")
            else:
                display_name = target.nick or target.display_name
                new_thread = await prison_channel.create_thread(
                    name=f"Solitary: {display_name}",
                    type=discord.ChannelType.public_thread,
                    reason=f"Solitary confinement for {target.display_name}"
                )

                c.execute("INSERT OR REPLACE INTO solitary_confinement (user_id, thread_id, archive_date) VALUES (?, ?, ?)",
                          (target.id, new_thread.id, None))
                c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)",
                         (target.id, new_thread.id, get_balance(target.id, c)))
                c.connection.commit()

                shared.solitary_confinement[guild_id][target.id] = new_thread.id
                shared.prison_users[guild_id][target.id] = new_thread.id
                await new_thread.send(f"{target.mention} has entered solitary confinement.")

            await ctx.followup.send(f"🔒 {target.mention} placed in solitary.")

        elif action == "remove":
            if target.id not in shared.solitary_confinement[guild_id]:
                await ctx.followup.send("❌ That user is not in solitary.")
                return

            thread_id = shared.solitary_confinement[guild_id][target.id]
            thread = await ctx.guild.fetch_channel(thread_id)
            if thread:
                await thread.send(f"{target.name} has been released from solitary.")
                await thread.send("It's mighty lonely in here.")
                await thread.send("Don't chat here, the silly prisoner won't see it!")
                await thread.edit(archived=True, locked=True, reason="Released from solitary confinement.")
                c.execute("UPDATE solitary_confinement SET archive_date = ? WHERE user_id = ?", (datetime.now(), target.id))
                c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
                c.connection.commit()
            else:
                await ctx.followup.send("❌ Could not find the confinement thread.")
                return

            del shared.prison_users[guild_id][target.id]
            await target.remove_roles(prisoner_role, sc_role, reason="Released from solitary")
            await ctx.followup.send(f"🔓 {target.mention} released from solitary.")

        else:
            await ctx.followup.send("❌ Invalid action specified.")

    except discord.Forbidden:
        await ctx.followup.send("❌ Missing required permissions!")
    except discord.HTTPException as e:
        print(f"Solitary command error: {e}")
        await ctx.followup.send("❌ Something went wrong.")

@bot.slash_command(
    name="prison",
    description="Toggle prison status or manage another user's prison status"
)
@with_config
async def prison(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to manage", required=False) = None,
    action: Option(str, "Add or remove from prison", choices=["add", "remove"], required=False) = None
):
    c = ctx.server_db.cursor()
    target = user or ctx.author
    await ctx.defer(ephemeral=True)

    if target.id in shared.locked_users[ctx.guild.id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.respond("❌ The target is currently locked and cannot be modified.", ephemeral=True)
        return
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SC_ROLE_ID)
    guild_id = ctx.guild.id

    if solitary_role in target.roles:
        await ctx.respond("❌ This user is in solitary confinement. Use `/solitary` to release them first.", ephemeral=True)
        return

    if not await check_auth(ctx, target):
        await ctx.respond("❌ You are not authorized to modify this user.", ephemeral=True)
        return

    if user is None:
        action = "remove" if target.id in shared.prison_users[guild_id] else "add"
    else:
        action = action.lower() if action else "add"

    try:
        if action == "add":
            if prisoner_role not in target.roles:
                await target.add_roles(prisoner_role, reason="Entered prison")

            shared.prison_users[guild_id][target.id] = PRISON_CHANNEL_ID
            balance = get_balance(target.id, c)

            c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)", 
                      (target.id, PRISON_CHANNEL_ID, balance))

            c.connection.commit()
            await ctx.respond(f"🔒 {target.mention} was placed in prison.")
        elif action == "remove":
            if target.id in shared.prison_users[guild_id]:
                del shared.prison_users[guild_id][target.id]

            c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
            await target.remove_roles(prisoner_role, reason="Released from prison")
            c.connection.commit()
            await ctx.respond(f"🔓 {target.mention} released from prison.")
        else:
            await ctx.respond("❌ Invalid action.", ephemeral=True)
    except Exception as e:
        await log_error_to_channels(
            bot=bot,
            ctx=ctx,
            error=e,
            context_label="prison"
        )
        await ctx.respond("❌ Something went wrong.", ephemeral=True)


@bot.slash_command(
    name="lockdown",
    description="Lock a user to a specific channel and hide all other channels."
)
@with_config
@is_server_mod()
async def lockdown(
    ctx: discord.ApplicationContext,
    member: Option(discord.Member, "User to lockdown"),
    channel: Option(discord.TextChannel, "Channel to restrict them to")
):
    c = ctx.server_db.cursor()
    await ctx.defer(ephemeral=True)

    guild = ctx.guild
    target = member



    if not await check_auth(ctx, target):
        await ctx.followup.send("❌ You are not authorized to perform this action.")
        return

    failed_channels = []

    for ch in guild.channels:
        overwrite = ch.overwrites_for(member)
        overwrite.view_channel = False
        try:
            await ch.set_permissions(member, overwrite=overwrite)
        except discord.Forbidden:
            failed_channels.append(ch.name)
        except discord.HTTPException as e:
            failed_channels.append(f"{ch.name} (HTTP error)")

    # Explicitly allow the target channel
    try:
        allow_overwrite = channel.overwrites_for(member)
        allow_overwrite.view_channel = True
        await channel.set_permissions(member, overwrite=allow_overwrite)
    except Exception as e:
        await ctx.followup.send(f"❌ Failed to allow access to {channel.mention}: {e}")
        return

    if failed_channels:
        await ctx.followup.send(f"🔒 {member.mention} has been locked to {channel.mention}.")
        await log_error_to_channels(
            bot=bot,
            ctx=ctx,
            error=f"Could not modify channel permissions: {', '.join(failed_channels)}",
            context_label="lockdown"
        )
    else:
        await ctx.followup.send(
            f"🔒 {member.mention} has been successfully locked to {channel.mention}."
        )

@bot.slash_command(
    name="unlockdown",
    description="Unlock a user and restore their access to all channels."
)
@with_config
async def unlockdown(
    ctx: discord.ApplicationContext,
    member: Option(discord.Member, "User to unlock")
):
    c = ctx.server_db.cursor()
    await ctx.defer(ephemeral=True)

    guild = ctx.guild
    target = member

    if not await check_auth(ctx, target):
        await ctx.followup.send("❌ You are not authorized to perform this action.")
        return

    failed_channels = []

    for ch in guild.channels:
        try:
            await ch.set_permissions(member, overwrite=None)
        except discord.Forbidden:
            failed_channels.append(ch.name)

    if failed_channels:
        await ctx.followup.send(
            f"🔓 {member.mention} has been released from lockdown.\n⚠️ Could not reset: {', '.join(failed_channels)}"
        )
    else:
        await ctx.followup.send(f"🔓 {member.mention} has been released from lockdown.")



# ---------- WORD Play slash commands --------



@bot.slash_command(name="enforce", description="Force a user to include a required word or phrase.")
@with_config
async def enforce(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "The user to enforce the phrase on"),
    phrase: Option(str, "The required phrase"),
    initial_time: Option(int, "Timeout duration on first violation (seconds)", required=False, default=60),
    added_time: Option(int, "Extra timeout added per repeated violation", required=False, default=30),
):
    c = ctx.server_db.cursor()
    await ctx.defer(ephemeral=True)
    word = phrase.strip().lower()
    target = user or ctx.author
    guild_id = ctx.guild.id

    # Check for invalid characters
    if not all(32 <= ord(c) <= 126 for c in word):
        await ctx.respond("❌ The phrase contains unsupported or non-standard characters.", ephemeral=True)
        return

    # Check if target is locked
    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.respond("❌ This user is currently locked and cannot be modified.", ephemeral=True)
        return

    # Authorization check
    if not await check_auth(ctx, target):
        await ctx.respond("❌ You are not authorized to enforce words on this user.", ephemeral=True)
        return

    # Conflict with banned words
    if check_word_conflict(ctx, user.id, word, "enforce"):
        await ctx.respond("❌ That phrase conflicts with a banned word already set for this user.", ephemeral=True)
        return

    try:
        if user.id not in shared.enforced_words[guild_id]:
            shared.enforced_words[guild_id][user.id] = {}

        if word in shared.enforced_words[guild_id][user.id]:
            await ctx.respond("❌ That word is already being enforced on this user.", ephemeral=True)
            return

        adjusted_initial_time = initial_time - added_time
        shared.enforced_words[guild_id][user.id][word] = {
            "initial_time": adjusted_initial_time,
            "added_time": added_time
        }

        c.execute(
            "INSERT OR REPLACE INTO enforced_words (user_id, word, initial_time, added_time) VALUES (?, ?, ?, ?)",
            (user.id, word, adjusted_initial_time, added_time)
        )
        c.connection.commit()

        await ctx.respond(
            f"🔠 {user.mention} must now include **{word}** in all messages.\n"
            f"Timeout on violation: **{adjusted_initial_time + added_time} seconds**."
        )

    except Exception as e:
        await log_error_to_channels(
            bot=bot,
            ctx=ctx,
            error=e,
            context_label="enforce"
        )
        await ctx.respond("❌ An error occurred while enforcing the word.", ephemeral=True)

@bot.slash_command(name="unenforce", description="Remove an enforced phrase from a user.")
@with_config
async def unenforce(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to remove enforced word from"),
    phrase: Option(str, "The enforced phrase to remove"),
):
    c = ctx.server_db.cursor()
    word = phrase.strip().lower()
    target = user or ctx.author
    guild_id = ctx.guild.id
    await ctx.defer(ephemeral=True)


    # Locked check
    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.respond("❌ The target is currently locked and cannot be changed.", ephemeral=True)
        return

    # Authorization check
    if not await check_auth(ctx, target):
        await ctx.respond("❌ You are not authorized to unenforce this user.", ephemeral=True)
        return

    try:
        if target.id not in shared.enforced_words[guild_id] or word not in shared.enforced_words[guild_id][target.id]:
            await ctx.respond(f"{target.mention} doesn't have **{word}** enforced.", ephemeral=True)
            return

        del shared.enforced_words[guild_id][target.id][word]

        if not shared.enforced_words[guild_id][target.id]:
            del shared.enforced_words[guild_id][target.id]

        c.execute(
            "DELETE FROM enforced_words WHERE user_id = ? AND word = ?",
            (target.id, word)
        )
        c.connection.commit()

        await ctx.respond(
            f"✅ Removed **{word}** from {target.mention}'s enforced words list.",
            delete_after=30
        )

    except Exception as e:
        await log_error_to_channels(
            bot=bot,
            ctx=ctx,
            error=e,
            context_label="unenforce"
        )
        await ctx.respond("❌ An error occurred while removing the enforced word.", ephemeral=True)

# ------------------- Word Length Commands -------------------


@bot.slash_command(
    name="wordlength",
    description="Set a minimum or maximum character length per word for a user"
)
@with_config
async def wordlength(
    ctx,
    user: discord.Member,
    mode: Option(str, "Choose mode", choices=["min", "max"]),
    length: Option(int, "Character length (e.g., 5)")
):
    """Set min or max word length restriction per user"""
    c = ctx.server_db.cursor()
    guild_id = ctx.guild.id

    # ✅ Create the table automatically if missing
    c.execute("""
        CREATE TABLE IF NOT EXISTS word_length_limits (
            user_id INTEGER,
            min_length INTEGER DEFAULT 0,
            max_length INTEGER DEFAULT 0
        )
    """)

    # Lock and permission checks
    if user.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.respond("❌ That user is locked.", ephemeral=True)
        return

    if not await check_auth(ctx, user):
        await ctx.respond("❌ Permission denied.", ephemeral=True)
        return

    # Get current values if any
    c.execute("SELECT min_length, max_length FROM word_length_limits WHERE user_id = ?", (user.id,))
    row = c.fetchone()
    min_len, max_len = row if row else (0, 0)

    # Update appropriate field
    if mode == "min":
        min_len = length
    else:
        max_len = length

    c.execute(
        "INSERT OR REPLACE INTO word_length_limits (user_id, min_length, max_length) VALUES (?, ?, ?)",
        (user.id, min_len, max_len)
    )
    ctx.server_db.commit()
    shared.word_length_limits[guild_id][user.id] = {"min_length": min_len, "max_length": max_len}


    await ctx.respond(
        f"✅ Set {mode} word length for {user.mention} to `{length}` characters.",
        ephemeral=True
    )


@bot.slash_command(
    name="wordlength_remove",
    description="Remove all word length restrictions for a user"
)
@with_config
async def wordlength_remove(ctx, user: discord.Member):
    c = ctx.server_db.cursor()

    # ✅ Create table if missing (safe redundancy)
    c.execute("""
        CREATE TABLE IF NOT EXISTS word_length_limits (
            user_id INTEGER,
            min_length INTEGER DEFAULT 0,
            max_length INTEGER DEFAULT 0
        )
    """)

    c.execute("DELETE FROM word_length_limits WHERE user_id = ?", (user.id,))
    ctx.server_db.commit()
    shared.word_length_limits[guild_id].pop(user.id, None)


    await ctx.respond(f"✅ Removed all word length restrictions for {user.mention}.", ephemeral=True)


@bot.slash_command(name="ban_word", description="Ban a user from using a specific word or phrase.")
@with_config
async def ban_word(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to ban from using a word/phrase"),
    phrase: Option(str, "The forbidden word or phrase"),
    initial_time: Option(int, "Initial timeout duration (seconds)", required=False, default=60),
    added_time: Option(int, "Extra timeout added per repeat violation", required=False, default=30),
):
    c = ctx.server_db.cursor()
    word = phrase.strip().lower()
    target = user or ctx.author
    guild_id = ctx.guild.id

    await ctx.defer(ephemeral=True)

    # Basic phrase validation
    if not word:
        await ctx.respond("❌ Invalid word or phrase.", ephemeral=True)
        return

    # Check for lock
    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.respond("❌ The target is currently locked and cannot be modified.", ephemeral=True)
        return

    # Authorization check
    if not await check_auth(ctx, target):
        await ctx.respond("❌ You are not authorized to ban words for this user.", ephemeral=True)
        return

    # Check for conflict
    if check_word_conflict(ctx, target.id, word, "ban"):
        await ctx.respond("❌ That word conflicts with an enforced word already set for this user.", ephemeral=True)
        return

    try:
        if target.id not in shared.banned_words[guild_id]:
            shared.banned_words[guild_id][target.id] = {}

        if word in shared.banned_words[guild_id][target.id]:
            await ctx.respond("❌ That word is already banned for this user.", ephemeral=True)
            return

        adjusted_initial_time = initial_time - added_time
        shared.banned_words[guild_id][target.id][word] = {
            "initial_time": adjusted_initial_time,
            "added_time": added_time
        }

        c.execute(
            "INSERT OR REPLACE INTO banned_words (user_id, word, initial_time, added_time) VALUES (?, ?, ?, ?)",
            (target.id, word, adjusted_initial_time, added_time)
        )
        c.connection.commit()

        await ctx.respond(
            f"⛔ {target.mention} is now **banned** from saying **{word}**.\n"
            f"Timeout on violation: **{adjusted_initial_time + added_time} seconds**."
        )

    except Exception as e:
        await log_error_to_channels(
            bot=bot,
            ctx=ctx,
            error=e,
            context_label="banned"
        )
        await ctx.respond("❌ An error occurred while banning the word.", ephemeral=True)

@bot.slash_command(name="unban", description="Unban a word or phrase for a user.")
@with_config
async def unban(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to remove the banned word from"),
    phrase: Option(str, "The banned phrase to remove"),
):
    c = ctx.server_db.cursor()
    word = phrase.strip().lower()
    target = user or ctx.author
    guild_id = ctx.guild.id
    await ctx.defer(ephemeral=True)

    # Check if the user is locked and caller isn't authorized
    if target.id in shared.locked_users[guild_id] and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.respond("❌ The target is currently locked and cannot be changed.", ephemeral=True)
        return

    # Authorization check
    if not await check_auth(ctx, target):
        await ctx.respond("❌ You are not authorized to unban this user.", ephemeral=True)
        return

    try:
        # Check if the word is banned for this user
        if target.id not in shared.banned_words[guild_id] or word not in shared.banned_words[guild_id][target.id]:
            await ctx.respond(f"{target.mention} doesn't have **{word}** banned.", ephemeral=True)
            return

        # Remove from memory
        del shared.banned_words[guild_id][target.id][word]

        if not shared.banned_words[guild_id][target.id]:
            del shared.banned_words[guild_id][target.id]

        # Remove from database
        c.execute(
            "DELETE FROM banned_words WHERE user_id = ? AND word = ?",
            (target.id, word)
        )
        c.connection.commit()

        await ctx.respond(
            f"✅ Removed **{word}** from {target.mention}'s banned words list.",
            delete_after=30
        )

    except Exception as e:
        await log_error_to_channels(
            bot=bot,
            ctx=ctx,
            error=e,
            context_label="unban"
        )

        await ctx.respond("❌ An error occurred while removing the banned word.", ephemeral=True)


@bot.slash_command(name="word_status", description="Check a user's banned and enforced words.")
@with_config
async def word_status(
    ctx: discord.ApplicationContext,
    user: Option(discord.Member, "User to check", required=False)
):
    c = ctx.server_db.cursor()
    target = user or ctx.author
    guild_id = ctx.guild.id

    enforced = shared.enforced_words[guild_id].get(target.id, {})
    banned = shared.banned_words[guild_id].get(target.id, {})

    if not enforced and not banned:
        await ctx.respond(f"{target.mention} has no enforced or banned words.")
        return

    embed = discord.Embed(
        title=f"Word Status for {target.display_name}",
        color=discord.Color.blue()
    )

    if enforced:
        enforced_lines = [
            f"**{word}** → Timeout: `{data['initial_time'] + data['added_time']}s`"
            for word, data in enforced.items()
        ]
        embed.add_field(name="🔠 Enforced Words", value="\n".join(enforced_lines), inline=False)

    if banned:
        banned_lines = [
            f"**{word}** → Timeout: `{data['initial_time'] + data['added_time']}s`"
            for word, data in banned.items()
        ]
        embed.add_field(name="⛔ Banned Words", value="\n".join(banned_lines), inline=False)

    await ctx.respond(embed=embed)




# ---Gambling slash command---
@bot.slash_command(name="balance", description="Check your pet bucks balance")
@with_config
async def balance(ctx, user: discord.Member = None):
    c = ctx.server_db.cursor()
    target = user or ctx.author
    user_id = target.id

    balance = get_balance(user_id, c)

    if balance == 0:
        await ctx.respond(f"💰 {target.display_name}'s balance: **0 pet bucks**", ephemeral=True)
        await ctx.channel.send(f"{ctx.author.mention}", delete_after=0)  # To trigger Sadge reaction manually
        return

    await ctx.respond(f"💰 {target.display_name}'s balance: **{balance} pet bucks**")

@bot.slash_command(name="daily", description="Claim your daily pet bucks reward")
@with_config
async def daily(ctx):
    c = ctx.server_db.cursor()
    user_id = ctx.author.id
    now = datetime.now(timezone.utc)

    c.execute("SELECT last_claim, claim_count, streak_days FROM daily_claims WHERE user_id = ?", (user_id,))
    row = c.fetchone()

    # Check if wallet exists for user
    c.execute("SELECT 1 FROM user_wallets WHERE user_id = ?", (user_id,))
    wallet_exists = c.fetchone()

    if not wallet_exists:
        c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (user_id, 1000))

    if row:
        last_claim = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        claim_count = row[1]
        streak_days = row[2]

        if now - last_claim < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last_claim)
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes = remainder // 60
            await ctx.respond(f"⏳ Already claimed. Try again in {hours}h {minutes}m.", ephemeral=True)
            return

        if now - last_claim > timedelta(hours=48):
            streak_days = 0

        streak_days += 1
    else:
        claim_count = 0
        streak_days = 1

    reward = 50 + (streak_days - 1) * 5 if streak_days <= 40 else 50 + 39 * 5
    update_balance(user_id, reward, c)

    c.execute(
        "INSERT OR REPLACE INTO daily_claims (user_id, last_claim, claim_count, streak_days) VALUES (?, ?, ?, ?)",
        (user_id, now.isoformat(), claim_count + 1, streak_days)
    )
    c.connection.commit()

    await ctx.respond(
        f"✅ {ctx.author.mention}, you’ve claimed **{reward} pet bucks**! Streak: **{streak_days} days**, "
        f"Total Claims: **{claim_count + 1}**"
    )

@bot.slash_command(name="give", description="Give pet bucks to another user")
@with_config
async def give(ctx, recipient: discord.Member, amount: int):
    c = ctx.server_db.cursor()
    sender_id = ctx.author.id
    recipient_id = recipient.id

    if recipient_id == sender_id:
        await ctx.respond("❌ You can't give pet bucks to yourself.", ephemeral=True)
        return

    if amount <= 0:
        await ctx.respond("❌ Amount must be greater than zero.", ephemeral=True)
        return

    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (sender_id,))
    sender_data = c.fetchone()

    if not sender_data or sender_data[0] < amount:
        await ctx.respond("❌ You don't have enough pet bucks.", ephemeral=True)
        return

    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (recipient_id,))
    if not c.fetchone():
        c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (recipient_id, 1000))

    c.execute("UPDATE user_wallets SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
    c.execute("UPDATE user_wallets SET balance = balance + ? WHERE user_id = ?", (amount, recipient_id))
    c.connection.commit()

    await ctx.respond(f"💸 {ctx.author.mention} gave {amount} pet bucks to {recipient.mention}!")

@bot.slash_command(name="leaderboard", description="View the top pet buck holders")
@with_config
async def leaderboard(ctx):
    c = ctx.server_db.cursor()
    c.execute("SELECT user_id, balance FROM user_wallets ORDER BY balance DESC, user_id ASC LIMIT 10")
    rows = c.fetchall()

    if not rows:
        await ctx.respond("❌ No users found.")
        return

    embed = discord.Embed(
        title="💰 Top 10 Leaderboard",
        description="Top balances in pet bucks:",
        color=0xFFD700
    )

    for rank, (user_id, balance) in enumerate(rows, 1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else "Unknown User"
        embed.add_field(name=f"{rank}. {name}", value=f"**{balance} pet bucks**", inline=False)

    await ctx.respond(embed=embed)



@bot.slash_command(name="bet", description="Challenge another user to a pet bucks bet on a game.")
@with_config
async def bet(
    ctx: discord.ApplicationContext,
    opponent: Option(discord.Member, "The user you want to bet against"),
    amount: Option(int, "Amount of pet bucks to bet"),
    game: Option(str, "The name of the game"),
):
    c = ctx.server_db.cursor()
    user_id = ctx.author.id

    # Check if the initiator has a wallet and enough balance
    c.execute("SELECT balance FROM user_wallets WHERE user_id=?", (user_id,))
    result = c.fetchone()

    if not result:
        await ctx.respond("❌ You don't have a wallet yet. Use `/balance` to create one.", ephemeral=True)
        return

    user_balance = result[0]
    if user_balance < amount:
        await ctx.respond("❌ You don't have enough pet bucks for this bet.", ephemeral=True)
        return

    # Check opponent balance
    c.execute("SELECT balance FROM user_wallets WHERE user_id=?", (opponent.id,))
    opponent_result = c.fetchone()

    if not opponent_result:
        await ctx.respond(f"❌ {opponent.display_name} doesn't have a wallet. Ask them to use `/balance` first.", ephemeral=True)
        return

    opponent_balance = opponent_result[0]
    if opponent_balance < amount:
        await ctx.respond(f"❌ {opponent.display_name} doesn't have enough pet bucks for this bet.", ephemeral=True)
        return

    # Ask opponent to accept
    bet_message = await ctx.respond(
        f"{opponent.mention}, {ctx.author.mention} has challenged you to a bet of {amount} pet bucks on the game **'{game}'**.\n"
        f"React with ✅ to accept or ❌ to decline, or type 'yes' or 'no' within 30 seconds.",
        ephemeral=False
    )
    msg = await ctx.channel.fetch_message((await bet_message).id)

    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check_reaction(reaction, user):
        return user == opponent and str(reaction.emoji) in ['✅', '❌'] and reaction.message.id == msg.id

    def check_message(message):
        return message.author == opponent and message.content.lower() in ['yes', 'no'] and message.channel == ctx.channel

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(bot.wait_for("reaction_add", check=check_reaction, timeout=30)),
                asyncio.create_task(bot.wait_for("message", check=check_message, timeout=30))
            ],
            return_when=asyncio.FIRST_COMPLETED
        )

        result = done.pop().result()

        # Clean up any remaining pending tasks
        for task in pending:
            task.cancel()

        accepted = False
        if isinstance(result, tuple):  # reaction result
            reaction, user = result
            accepted = str(reaction.emoji) == "✅"
        else:  # message result
            accepted = result.content.lower() == "yes"

        if accepted:
            # Deduct and record bet
            c.execute('INSERT INTO bets (initiator_id, opponent_id, amount, game) VALUES (?, ?, ?, ?)',
                      (user_id, opponent.id, amount, game))
            c.connection.commit()

            c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (user_balance - amount, user_id))
            c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (opponent_balance - amount, opponent.id))
            c.connection.commit()

            await ctx.send(f"✅ Bet placed! {ctx.author.mention} vs {opponent.mention} for {amount} pet bucks on **'{game}'**.")
        else:
            await ctx.send(f"{opponent.mention} has declined the bet.")

    except asyncio.TimeoutError:
        await ctx.send(f"⌛ {opponent.mention} did not respond in time. Bet canceled.")

@bot.slash_command(name="bet_result", description="End a bet and award winners")
@with_config
async def bet_result(ctx, winning_option: int):
    c = ctx.server_db.cursor()
    if not await check_auth(ctx):
        return

    row = c.execute("SELECT bet_id, amount, options FROM bets WHERE guild_id = ?", (ctx.guild.id,)).fetchone()
    if not row:
        await ctx.respond("❌ No active bet found.", ephemeral=True)
        return

    bet_id, amount, options_json = row
    options = json.loads(options_json)

    if winning_option < 1 or winning_option > len(options):
        await ctx.respond("❌ Invalid option number.", ephemeral=True)
        return

    winners = c.execute(
        "SELECT user_id FROM bet_entries WHERE bet_id = ? AND choice = ?",
        (bet_id, winning_option)
    ).fetchall()

    if not winners:
        await ctx.respond("🏳️ Bet ended. No winners.")
    else:
        total_reward = len(winners) * amount
        for (user_id,) in winners:
            update_balance(user_id, total_reward, c)
        await ctx.respond(f"🎉 Bet ended! Option {winning_option} **({options[winning_option - 1]})** won.\n"
                          f"Each winner received **{total_reward} pet bucks**.")

    c.execute("DELETE FROM bets WHERE bet_id = ?", (bet_id,))
    c.execute("DELETE FROM bet_entries WHERE bet_id = ?", (bet_id,))
    c.connection.commit()

@bot.slash_command(name="cancel_bet", description="Cancel a pending bet and refund both players.")
@with_config
@is_server_mod()
async def cancel_bet(
    ctx: discord.ApplicationContext,
    game: Option(str, "Name of the game whose bet should be canceled")
):
    c = ctx.server_db.cursor()
    # Check if the user is an admin (you can use role checks or your Mod list)

    # Fetch the bet details from the database
    c.execute("SELECT * FROM bets WHERE game = ? AND status = 'pending'", (game,))
    bet = c.fetchone()

    if not bet:
        await ctx.respond("❌ No active bet found for this game.", ephemeral=True)
        return

    bet_id, initiator_id, opponent_id, amount, game_name, status, created_at = bet

    # Refund the initiator
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (initiator_id,))
    initiator_balance = c.fetchone()[0]
    c.execute("UPDATE user_wallets SET balance = ? WHERE user_id = ?", (initiator_balance + amount, initiator_id))

    # Refund the opponent
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (opponent_id,))
    opponent_balance = c.fetchone()[0]
    c.execute("UPDATE user_wallets SET balance = ? WHERE user_id = ?", (opponent_balance + amount, opponent_id))

    # Update the bet status to canceled
    c.execute("UPDATE bets SET status = 'canceled' WHERE id = ?", (bet_id,))
    c.connection.commit()

    # Notify about cancellation
    await ctx.respond(f"✅ Bet on game **'{game}'** has been canceled. Both players have been refunded **{amount}** pet bucks.")


@bot.slash_command(name="add_petbucks", description="Add pet bucks to a user's balance (Mod only).")
@with_config
@is_server_mod()
async def add_slash(ctx, user: discord.Member, amount: int):
    c = ctx.server_db.cursor()


    if amount <= -10000000000000000000000000000000000000:
        await ctx.respond("❌ You must specify a positive amount of pet bucks to add.")
        return

    update_balance(user.id, amount, c)
    await ctx.respond(f"✅ {amount} pet bucks have been added to {user.mention}'s balance!")

@bot.slash_command(name="flip", description="Flip a coin! Optionally bet pet bucks on it.")
@with_config
async def flip_slash(ctx, amount: int = None):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    user_id = ctx.author.id

    if ctx.channel.id != GAMBLING_ID and amount not in [None, 0]:
        await ctx.respond(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return

    if amount is not None:
        if amount < 0:
            await ctx.respond("❌ You must specify a positive amount to gamble.")
            return
        if not has_sufficient_funds(user_id, amount, c):
            await ctx.respond(f"❌ {ctx.author.mention}, you don't have enough pet bucks to gamble that amount.")
            return

    result = random.choice(["🪙 Heads", "🪙 Tails"])

    if amount:
        if random.choice([True, False]):
            update_balance(user_id, amount, c)
            await ctx.respond(f"{ctx.author.mention} flips a coin... {result}! You win! {amount} pet bucks added!")
        else:
            update_balance(user_id, -amount, c)
            await ctx.respond(f"{ctx.author.mention} flips a coin... {result}! You lost! {amount} pet bucks deducted.")
    else:
        await ctx.respond(f"{ctx.author.mention} flips a coin... {result}!")

@bot.slash_command(name="slots", description="Spin the slot machine and try your luck!")
@with_config
async def slots(ctx, bet: int = 0):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    user_id = ctx.author.id
    balance = get_balance(user_id, c)

    if ctx.channel.id != GAMBLING_ID:
        await ctx.respond(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return

    if bet < 0:
        await ctx.respond("❌ You must specify a positive amount to bet!")
        return

    if bet > balance:
        await ctx.respond(f"❌ {ctx.author.mention}, you don't have enough pet bucks to make that bet.")
        return

    symbols = ["🍒", "🍋", "🍊", "🍉", "🍇", "🍓"]
    result = [random.choice(symbols) for _ in range(3)]

    embed = discord.Embed(title="🎰 Slot Machine Result", description=f"{ctx.author.mention} spun the slots!", color=0x3498db)
    embed.add_field(name="Spin", value=' '.join(result), inline=False)

    if result[0] == result[1] == result[2]:
        winnings = bet * 36
        update_balance(user_id, winnings, c)
        embed.add_field(name="🎉 Jackpot!", value=f"You win {winnings} pet bucks!", inline=False)
    else:
        update_balance(user_id, -bet, c)
        embed.add_field(name="❌ No Win", value=f"You lose {bet} pet bucks.", inline=False)

    await ctx.respond(embed=embed)

@bot.slash_command(name="roulette", description="Bet on a number or color in roulette!")
@with_config
async def roulette(ctx, bet_type: str, bet_value: str, bet_amount: int):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    user_id = ctx.author.id
    balance = get_balance(user_id, c)
    bet_type = bet_type.lower()

    if ctx.channel.id != GAMBLING_ID:
        await ctx.respond(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.")
        return

    if bet_amount < 0:
        await ctx.respond("❌ You must bet a positive amount.")
        return

    if bet_amount > balance:
        await ctx.respond(f"❌ {ctx.author.mention}, you don't have enough pet bucks to make that bet.")
        return

    if bet_type == 'colour':
        bet_type = 'color'

    if bet_type not in ['number', 'color']:
        await ctx.respond("❌ Invalid bet type. Choose 'number' or 'color'.")
        return

    if bet_type == 'color' and bet_value.lower() not in ['red', 'black']:
        await ctx.respond("❌ Invalid color. Choose 'red' or 'black'.")
        return

    if bet_type == 'number':
        try:
            bet_value = int(bet_value)
            if bet_value < 0 or bet_value > 36:
                raise ValueError
        except ValueError:
            await ctx.respond("❌ Invalid number. Choose between 0 and 36.")
            return

    spin_result = random.randint(0, 36)
    red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 29, 31, 33, 35}
    spin_color = 'green' if spin_result == 0 else ('red' if spin_result in red_numbers else 'black')
    color_display = {"red": "🟥", "black": "⬛", "green": "🟩"}
    spin_display = f"{color_display[spin_color]} ({spin_result})"

    win = False
    if bet_type == 'number' and bet_value == spin_result:
        win = True
        winnings = bet_amount * 35
    elif bet_type == 'color' and bet_value.lower() == spin_color:
        win = True
        winnings = bet_amount
    else:
        winnings = -bet_amount

    update_balance(user_id, winnings, c)

    embed = discord.Embed(title="🎱 Roulette Spin Result", description=f"{ctx.author.mention} spun the wheel: {spin_display}", color=0xff0000 if spin_color == 'red' else 0x000000 if spin_color == 'black' else 0x00ff00)
    if win:
        embed.add_field(name="🎉 Winner!", value=f"You won {winnings} pet bucks!", inline=False)
    else:
        embed.add_field(name="❌ Lost!", value=f"You lost {bet_amount} pet bucks.", inline=False)

    await ctx.respond(embed=embed)


@bot.slash_command(name="join", description="Join the Blackjack game.")
@with_config
async def join_slash(ctx: discord.ApplicationContext):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    guild_id = ctx.guild.id

    if ctx.channel.id != GAMBLING_ID:
        await ctx.respond(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.", ephemeral=True)
        return

    if shared.game_in_progress[guild_id]:
        if ctx.author not in shared.queue[guild_id] and ctx.author not in shared.players[guild_id]:
            shared.queue[guild_id].append(ctx.author)
            await ctx.respond(f"{ctx.author.mention} has been added to the queue for the next game.")
        else:
            await ctx.respond(f"{ctx.author.mention}, you're already in the game or in the queue.")
        return

    if ctx.author not in shared.players[guild_id]:
        shared.players[guild_id].append(ctx.author)
        await ctx.respond(f"{ctx.author.mention} has joined the Blackjack game!")

        if len(shared.players[guild_id]) == 1 and shared.pending_blackjack_timeout[guild_id] is None:
            shared.pending_blackjack_timeout[guild_id] = asyncio.create_task(
                blackjack_join_timeout(guild_id, ctx.channel)
            )
    else:
        await ctx.respond(f"{ctx.author.mention}, you're already in the current game.")



@bot.slash_command(name="leave", description="Leave the Blackjack game or queue.")
@with_config
async def leave_slash(ctx: discord.ApplicationContext):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)

    guild_id = ctx.guild.id

    if ctx.author in shared.players[guild_id]:
        shared.players[guild_id].remove(ctx.author)
        await ctx.respond(f"{ctx.author.mention} has left the Blackjack game.")
    elif ctx.author in shared.queue[guild_id]:
        shared.queue[guild_id].remove(ctx.author)
        await ctx.respond(f"{ctx.author.mention} has left the queue.")
    else:
        await ctx.respond(f"{ctx.author.mention}, you're not currently in the game or in the queue.")



@bot.slash_command(name="blackjack", description="Start the Blackjack game.")
@with_config
async def blackjack_slash(ctx: discord.ApplicationContext):
    c = ctx.server_db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = unpack_config(ctx)
    guild_id = ctx.guild.id
    await ctx.defer(ephemeral=True)

    # Cancel any pending join timeout
    if shared.pending_blackjack_timeout[guild_id]:
        shared.pending_blackjack_timeout[guild_id].cancel()
        shared.pending_blackjack_timeout[guild_id] = None

    if ctx.channel.id != GAMBLING_ID:
        await ctx.respond(f"❌ This command can only be used in the designated gambling channel: <#{GAMBLING_ID}>.", ephemeral=True)
        return

    if shared.game_in_progress[guild_id]:
        await ctx.respond(f"❌ {ctx.author.mention}, a game is already in progress. Please wait for the next round.")
        return

    if len(shared.players[guild_id]) < 1:
        await ctx.respond("❌ No players have joined. Use `/join` to sit at the table!")
        return

    shared.game_in_progress[guild_id] = True
    shared.current_bets[guild_id] = {}

    # Betting phase
    for player in shared.players[guild_id]:
        bal = get_balance(player.id, c)
        await ctx.channel.send(f"{player.mention}, please type your bet (30 seconds). You have **{bal} pet bucks** available:")

        def check(m):
            return m.author == player and m.channel == ctx.channel and m.content.isdigit()

        try:
            bet_msg = await bot.wait_for('message', timeout=30.0, check=check)
            bet = int(bet_msg.content)

            if not has_sufficient_funds(player.id, bet, c):
                await ctx.channel.send(f"❌ {player.mention}, insufficient funds! You'll be playing for free!")
                shared.current_bets[guild_id][player] = 0
            else:
                shared.current_bets[guild_id][player] = bet
                await ctx.channel.send(f"✅ {player.mention} bet {bet} pet bucks.")
        except asyncio.TimeoutError:
            await ctx.channel.send(f"⏰ {player.mention} took too long! You'll be playing for free!")
            shared.current_bets[guild_id][player] = 0

    # Game setup
    deck = shuffle_deck()
    player_hands = {player: [draw_card(deck), draw_card(deck)] for player in shared.players[guild_id]}
    dealer_hand = [draw_card(deck), draw_card(deck)]

    for hand in player_hands.values():
        for card in hand:
            update_running_count(card)
    for card in dealer_hand:
        update_running_count(card)

    initial_embed = discord.Embed(title="🃏 Blackjack - Initial Deal", color=0x2ecc71)
    for player, hand in player_hands.items():
        initial_embed.add_field(
            name=f"{player.display_name}'s Hand",
            value=f"{format_hand(hand)} (Total: {calculate_hand(hand)})",
            inline=False
        )
    initial_embed.add_field(
        name="Dealer's Hand",
        value=f"{dealer_hand[0][0]}{dealer_hand[0][1]} | 🃏",
        inline=False
    )
    await ctx.channel.send(embed=initial_embed)

    # Action phase
    current_players = [p for p in shared.players[guild_id] if calculate_hand(player_hands[p]) < 21]
    round_number = 1

    while current_players:
        action_embed = discord.Embed(
            title=f"🔄 Round {round_number} - Hit or Stand",
            description=f"Players: {', '.join([p.mention for p in current_players])}\n"
                        "React with 🃏 to HIT or 🚫 to STAND\n"
                        "You have 30 seconds!",
            color=0xf1c40f
        )
        action_msg = await ctx.channel.send(embed=action_embed)
        await action_msg.add_reaction("🃏")
        await action_msg.add_reaction("🚫")

        reactions = {}
        start_time = time.time()

        while (time.time() - start_time) < 30 and len(reactions) < len(current_players):
            try:
                reaction, user = await bot.wait_for(
                    'reaction_add',
                    timeout=30 - (time.time() - start_time),
                    check=lambda r, u: u in current_players and r.message.id == action_msg.id and str(r.emoji) in ["🃏", "🚫"]
                )

                if user.id not in reactions:
                    reactions[user.id] = str(reaction.emoji)
                    await ctx.channel.send(f"🎯 {user.mention} chose to {'HIT' if reaction.emoji == '🃏' else 'STAND'}!")
            except asyncio.TimeoutError:
                break

        # Process actions
        hitters = []
        for player in current_players:
            if player.id not in reactions:
                await ctx.channel.send(f"⏰ {player.mention} didn't react! Standing automatically.")
                continue
            if reactions[player.id] == "🃏":
                hitters.append(player)

        # Process hits
        updated_players = []
        if hitters:
            hit_embed = discord.Embed(title="📤 Players Drawing Cards", color=0xe74c3c)
            for hitter in hitters:
                new_card = draw_card(deck)
                player_hands[hitter].append(new_card)
                update_running_count(new_card)

                total = calculate_hand(player_hands[hitter])
                status = " (BUST!)" if total > 21 else ""

                hit_embed.add_field(
                    name=f"{hitter.display_name} drew {new_card[0]}{new_card[1]}",
                    value=f"{format_hand(player_hands[hitter])} (Total: {total}{status})",
                    inline=False
                )

                if total <= 21:
                    updated_players.append(hitter)
            hit_embed.add_field(
                name="Dealer's Hand",
                value=f"{dealer_hand[0][0]}{dealer_hand[0][1]} | 🃏",
                inline=False
            )
            await ctx.channel.send(embed=hit_embed)

        current_players = updated_players
        round_number += 1

    # Dealer's turn
    dealer_total = calculate_hand(dealer_hand)
    dealer_cards = [f"{r}{s}" for r, s in dealer_hand]
    highest_player_total = max([calculate_hand(player_hands[p]) for p in shared.players[guild_id]])

    while True:
        if dealer_total >= 17 or dealer_total > highest_player_total:
            break
        new_card = draw_card(deck)
        dealer_hand.append(new_card)
        update_running_count(new_card)
        dealer_total = calculate_hand(dealer_hand)
        dealer_cards.append(f"{new_card[0]}{new_card[1]}")

    # Final results
    result_embed = discord.Embed(
        title="🏁 Final Results",
        description=f"**Dealer's Hand**: {', '.join(dealer_cards)} (Total: {dealer_total})",
        color=0x9b59b6
    )

    for player in shared.players[guild_id]:
        hand = player_hands[player]
        total = calculate_hand(hand)
        bet = shared.current_bets[guild_id].get(player, 0)
        result = ""

        if total > 21:
            result = f"❌ BUST! Lost {bet} pet bucks"
            update_balance(player.id, -bet, c)
        elif dealer_total > 21 or total > dealer_total:
            result = f"✅ WIN! Gained {bet} pet bucks"
            update_balance(player.id, bet, c)
        elif total == dealer_total:
            result = "🔶 PUSH! Bet returned"
        else:
            result = f"❌ LOSE! Lost {bet} pet bucks"
            update_balance(player.id, -bet, c)

        result_embed.add_field(
            name=f"{player.display_name}'s Hand",
            value=f"{format_hand(hand)} (Total: {total})\n{result}",
            inline=False
        )

    await ctx.channel.send(embed=result_embed)

    # Cleanup
    shared.game_in_progress[guild_id] = False
    shared.current_bets[guild_id].clear()

    if shared.pending_blackjack_timeout[guild_id] is None:
        shared.pending_blackjack_timeout[guild_id] = asyncio.create_task(
            blackjack_join_timeout(guild_id, ctx.channel)
        )

    if shared.queue[guild_id]:
        await ctx.channel.send(f"🎮 Next game starting with: {', '.join([p.mention for p in shared.queue[guild_id]])}")
        shared.players[guild_id].extend(shared.queue[guild_id])
        shared.queue[guild_id].clear()


# ------------------- Command Limit -------------------


def is_within_limit(ctx, user_id):
    now = time.time()
    guild_id = ctx.guild.id
    timestamps = shared.user_command_timestamps[guild_id].get(user_id, [])
    # Keep only recent timestamps
    timestamps = [t for t in timestamps if now - t < TIME_WINDOW]
    shared.user_command_timestamps[guild_id][user_id] = timestamps

    if len(timestamps) >= COMMAND_LIMIT:
        return False
    timestamps.append(now)
    return True

@bot.check
async def global_command_limit(ctx):
    if ctx.author.bot:
        return False

    if ctx.guild is None:
        await ctx.respond("This command cannot be used in DMs.", ephemeral=True)
        return False

    user_id = ctx.author.id
    now = time.time()
    guild_id = ctx.guild.id

    if not is_within_limit(ctx, user_id):
        warned_at = shared.user_warned[guild_id].get(user_id, 0)
        
        # Only try to delete if it's a message-based command
        if getattr(ctx, "message", None):
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

        # Only send a warning if one hasn't been sent during this window
        if now - warned_at > TIME_WINDOW:
            shared.user_warned[guild_id][user_id] = now
            await ctx.send(
                f"What a save!\nWhat a save!\nWhat a save!\n"
                f"Chat disabled for **{TIME_WINDOW} seconds.**\n"
                f"⏱️ {ctx.author.mention}, you've hit the command limit ({COMMAND_LIMIT}/{TIME_WINDOW}s). Please wait."
            )

        return False

    # Clear warning state if they're back under the limit
    if user_id in shared.user_warned[guild_id] and now - shared.user_warned[guild_id][user_id] > TIME_WINDOW:
        del shared.user_warned[guild_id][user_id]

    return True


# ---------- SHARED -------------
'''
# Assign shared globals for cogs
shared.gagged_users = gagged_users
shared.AUTHORIZED_LOCK_MANAGERS = AUTHORIZED_LOCK_MANAGERS
shared.check_auth = check_auth
shared.conn = conn
'''
shared.AUTHORIZED_LOCK_MANAGERS = AUTHORIZED_LOCK_MANAGERS
shared.check_auth = check_auth

# --------- TOGGLE COGS ME ONLY --------

from discord import Interaction
from discord import SelectOption


skip_cog = {""}

# --- Helper to list toggleable cogs ---
def list_toggleable_cogs():
    return [
        filename[:-3]
        for filename in os.listdir("cogs")
        if (
            filename.endswith(".py")
            and filename not in {"__init__.py", "init__.py"}.union(skip_cog)
            and os.path.isfile(os.path.join("cogs", filename))
        )
    ]

def ensure_cog_columns_from_files():
    toggleable_cogs = list_toggleable_cogs()

    # Get current columns in the server_config table
    c.execute("PRAGMA table_info(server_config)")
    existing_columns = [row[1] for row in c.fetchall()]

    for cog in toggleable_cogs:
        column_name = f"{cog}_enabled"
        if column_name not in existing_columns:
            try:
                c.execute(f"ALTER TABLE server_config ADD COLUMN {column_name} INTEGER DEFAULT 1")
                print(f"✅ Added column '{column_name}' for cog '{cog}'")
            except Exception as e:
                print(f"❌ Failed to add column '{column_name}': {e}")

    c.connection.commit()



# --- Prefix Command ---
@bot.command()
@with_config
@commands.has_permissions(administrator=True)
async def toggle_cog(ctx, name: str):
    """Toggle a cog on/off for this server"""
    column = f"{name}_enabled"

    # Check if column exists in server_config
    c.execute("PRAGMA table_info(server_config)")
    columns = [row[1] for row in c.fetchall()]
    if column not in columns:
        await ctx.send(f"❌ No such cog column '{column}' found in server_config.")
        return

    # Toggle the value
    c.execute(f"SELECT {column} FROM server_config WHERE guild_id = ?", (ctx.guild.id,))
    row = c.fetchone()
    new_value = 0 if (row and row[0] == 1) else 1

    c.execute(f"UPDATE server_config SET {column} = ? WHERE guild_id = ?", (new_value, ctx.guild.id))
    c.connection.commit()
    state = "enabled ✅" if new_value else "disabled 🛑"
    await ctx.send(f"Cog `{name}` is now {state} for this server.")

def get_cog_enabled(guild_id, cog_name):
    column = f"{cog_name}_enabled"

    # Make sure the column exists
    c.execute("PRAGMA table_info(server_config)")
    columns = [row[1] for row in c.fetchall()]
    if column not in columns:
        return True  # Default to enabled if column missing

    # Query current value
    c.execute(f"SELECT {column} FROM server_config WHERE guild_id = ?", (guild_id,))
    row = c.fetchone()
    return row is None or row[0] == 1

# --- Interactive Slash Command View ---
class CogToggleView(View):
    def __init__(self, bot):
        super().__init__(timeout=60)
        self.bot = bot

        toggleable_cogs = list_toggleable_cogs()
        options = []
        for cog in toggleable_cogs:
            full = f"cogs.{cog}"
            loaded = full in bot.extensions
            emoji = "🟢" if loaded else "🔴"
            label = f"{cog} (loaded)" if loaded else f"{cog} (unloaded)"
            options.append(SelectOption(label=label, value=cog, emoji=emoji))

        self.add_item(CogSelect(self.bot, options))

class CogSelect(Select):
    def __init__(self, bot, guild_id, options):
        super().__init__(
            placeholder="Select a cog to toggle for this server",
            options=options,
            min_values=1,
            max_values=1
        )
        self.bot = bot
        self.guild_id = guild_id

    async def callback(self, interaction: Interaction):
        selected = self.values[0]
        column = f"{selected}_enabled"

        # Check if the column exists in server_config
        c.execute("PRAGMA table_info(server_config)")
        existing_columns = [row[1] for row in c.fetchall()]
        if column not in existing_columns:
            await interaction.response.send_message(
                f"❌ Config column `{column}` doesn't exist in `server_config`.",
                ephemeral=True
            )
            return

        # Read current value and toggle it
        c.execute(f"SELECT {column} FROM server_config WHERE guild_id = ?", (self.guild_id,))
        row = c.fetchone()
        new_value = 0 if (row and row[0] == 1) else 1

        c.execute(f"UPDATE server_config SET {column} = ? WHERE guild_id = ?", (new_value, self.guild_id))
        c.connection.commit()

        status = "enabled ✅" if new_value else "disabled 🛑"
        await interaction.response.send_message(
            f"`{selected}` is now {status} for this server.",
            ephemeral=True
        )

# --- Slash Command ---
@bot.slash_command(name="toggle_cog", description="Toggle a cog on/off (server-specific)")
@with_config
@commands.has_permissions(administrator=True)
async def toggle_cog_slash(ctx):

    options = []
    for cog in list_toggleable_cogs():
        enabled = get_cog_enabled(ctx.guild.id, cog)
        emoji = "🟢" if enabled else "🔴"
        label = f"{cog} (enabled)" if enabled else f"{cog} (disabled)"
        options.append(SelectOption(label=label, value=cog, emoji=emoji))

    view = View()
    view.add_item(CogSelect(bot, ctx.guild.id, options))
    await ctx.respond("Select a cog to toggle for this server:", view=view, ephemeral=True)

# ------- Test command


# Define options for the test command
first_options = ["animals", "colors"]
second_options = {
    "animals": ["dog", "cat", "bird"],
    "colors": ["red", "green", "blue"]
}

async def second_autocomplete(ctx: AutocompleteContext):
    try:
        # Get the current value of the 'first' option
        first_value = ctx.options.get("first")
        
        # If no category selected yet, return empty list
        if not first_value:
            return []
        
        # Get options for the selected category
        options = second_options.get(first_value, [])
        
        # Get current input value
        current_input = (ctx.value or "").lower()
        
        # Filter options based on input
        if current_input:
            # Return options that start with the input
            return [opt for opt in options if opt.lower().startswith(current_input)]
        
        return options
        
    except Exception as e:
        await log_error_to_channels(
            bot=bot,
            ctx=ctx,
            error=e,
            context_label="autocomplete error"
        )

        return []

@bot.slash_command(name="test_command", description="Test dependent options")
@is_bot_owner()
async def test_cmd(
    ctx: discord.ApplicationContext,
    first: Option(str, "Choose a category", choices=first_options),
    second: Option(str, "Choose a sub-option", autocomplete=second_autocomplete)
):
    await ctx.respond(f"You selected: **{first} → {second}**", ephemeral=True)




# ------------------ Ban user from commands -----------
@bot.command(name="stop_user")
@with_config
@is_server_mod()
async def stop_user(ctx, user: discord.Member):
    c = ctx.server_db.cursor()
    """Prevent a user from using commands (prefix version)."""


    c.execute("INSERT OR IGNORE INTO command_bans (user_id) VALUES (?)", (user.id,))
    c.connection.commit()
    await ctx.send(f"🚫 {user.mention} has been stopped from using commands.")


@bot.command(name="unstop_user")
@with_config
@is_server_mod()
async def unstop_user(ctx, user: discord.Member):
    c = ctx.server_db.cursor()
    """Allow a previously stopped user to use commands (prefix version)."""


    c.execute("DELETE FROM command_bans WHERE user_id = ?", (user.id,))
    c.connection.commit()
    await ctx.send(f"✅ {user.mention} is now allowed to use commands again.")


@bot.slash_command(name="stop", description="Prevent a user from using commands")
@with_config
@is_server_mod()
async def stop_user(ctx, user: discord.Member):
    c = ctx.server_db.cursor()


    c.execute("INSERT OR IGNORE INTO command_bans (user_id) VALUES (?)", (user.id,))
    c.connection.commit()
    await ctx.respond(f"🚫 {user.mention} has been stopped from using commands.")

@bot.slash_command(name="unstop", description="Allow a previously stopped user to use commands")
@with_config
@is_server_mod()
async def unstop_user(ctx, user: discord.Member):
    c = ctx.server_db.cursor()


    c.execute("DELETE FROM command_bans WHERE user_id = ?", (user.id,))
    c.connection.commit()
    await ctx.respond(f"✅ {user.mention} is now allowed to use commands again.")



@bot.check
async def globally_block_stopped_users(ctx):
    config = shared.get_server_config(ctx.guild.id)

    # Let commands run if the server has no config yet (e.g., just added bot)
    if not config:
        return True

    db = shared.get_server_db(ctx.guild.id)
    c = db.cursor()
    (
        PRISON_CHANNEL_ID,
        LINE_WRITING_ID,
        COUNTING_ID,
        GAMBLING_ID,
        SOLITARY_ROLE_ID,
        PRISONER_ROLE_ID,
        SC_ROLE_ID,
        BOTSTATUS_ID,
        TASK_CHANNEL_ID,
        LOG_CHANNEL_ID
    ) = shared.unpack_config_from_obj(config)

    c.execute("SELECT 1 FROM command_bans WHERE user_id = ?", (ctx.author.id,))
    if c.fetchone():
        try:
            if hasattr(ctx, "respond"):
                await ctx.respond("🚫 You are not allowed to use commands.", ephemeral=True)
            else:
                await ctx.send("🚫 You are not allowed to use commands.")
        except discord.InteractionResponded:
            pass

        try:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                command_name = getattr(ctx.command, "qualified_name", "[unknown]")
                content = getattr(ctx, "message", None)
                message_text = content.content if content else "[slash command]"
                await log_channel.send(
                    f"🚫 **Blocked Command Attempt**\n"
                    f"👤 User: {ctx.author.mention} (`{ctx.author.id}`)\n"
                    f"📘 Command: `{command_name}`\n"
                    f"💬 Content: `{message_text}`"
                )
        except Exception as e:
            await log_error_to_channels(
                bot=bot,
                ctx=ctx,
                error=e,
                context_label="logging blocked command"
            )


        return False

    return True


# ----- PURGE CHANNEL -----------------
# ---------- Slash Command ----------
@bot.slash_command(name="clearchannel", description="Delete all messages between two message IDs.")
@with_config
@commands.is_owner()
async def clearchannel_slash(
    ctx: discord.ApplicationContext,
    start_id: Option(str, "First message ID to delete", required=True),
    end_id: Option(str, "Last message ID to delete", required=True)
):
    await ctx.defer(ephemeral=True)

    try:
        start_id = int(start_id)
        end_id = int(end_id)
    except ValueError:
        await ctx.respond("❌ Invalid message IDs provided.", ephemeral=True)
        return

    if start_id > end_id:
        start_id, end_id = end_id, start_id  # swap if out of order

    deleted_count = 0
    to_delete = []

    try:
        async for msg in ctx.channel.history(limit=None, oldest_first=True):
            if start_id <= msg.id <= end_id:
                to_delete.append(msg)

                # Discord only allows bulk deletion of messages younger than 14 days
                # For safety, we delete in chunks of 100
                if len(to_delete) >= 100:
                    await ctx.channel.delete_messages(to_delete)
                    deleted_count += len(to_delete)
                    to_delete.clear()

        # Delete remaining messages
        if to_delete:
            await ctx.channel.delete_messages(to_delete)
            deleted_count += len(to_delete)

        await ctx.respond(f"🧹 Deleted {deleted_count} messages between IDs `{start_id}` and `{end_id}`.", ephemeral=True)

    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to delete messages.", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.respond(f"❌ Failed to delete messages: {e}", ephemeral=True)


# ---------- Prefix Command ----------
@bot.command(name="clearchannel")
@with_config
@commands.is_owner()
async def clearchannel_prefix(ctx, amount: int):
    if amount <= 0:
        await ctx.send("❌ Amount must be greater than zero.", delete_after=5)
        return

    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 Cleared {len(deleted) - 1} messages.", delete_after=5)

# -------------------- SETUP COMMAND
    # ---------------- Slash Command: Setup Guide ----------------
    @commands.slash_command(name="setup", description="Show first-time setup instructions in DM")
    @with_config
    async def setup_slash(self, ctx):
        try:
            await ctx.respond("📬 Check your DMs for setup instructions!", ephemeral=True)
            dm = await ctx.author.create_dm()
            await dm.send("""
🛠️ **Welcome to the Setup Guide**

Here's how to get started:

🔒 **Authorization**
- If you're a mod or trusted user, make sure the bot owner adds you to the authorized list.

👅 **Gag System**
- Use `/gag @user` and `/ungag @user` to control speech.
- Gagged messages are sent via webhook and can be revealed or deleted.

🔠 **Enforced Words**
- Use `/enforce @user phrase [initial_time] [added_time]` to force users to say certain words.

⚡ **PiShock Setup**
- Use `/pishock_register code` to link your PiShock account.
- Use `/shock @user strength reason` to deliver shocks to linked users.

🌀 **Lovense Setup**
- Use `/lovense_register token` to link your Lovense toy.
- Once linked, others can `/buzz @user strength` to interact with your toy.

📌 **General Tips**
- Use `!>` or `/` for commands
- Abuse will result in being blocked from the bot

Need help? Contact the bot owner or moderators.
            """)
        except Exception as e:
            await ctx.respond(f"❌ Failed to send DM: {e}", ephemeral=True)


# ------------- DEBUG
@bot.command()
@with_config
async def debugconfig(ctx):
    print(f"[DEBUG] ctx.config: {ctx.config}")
    await ctx.send(f"Config loaded: {bool(ctx.config)}")

# ------- SERVER CONFIG
def save_server_config(guild_id, prison_channel, line_channel, counting_channel, gambling_channel,
                       botstatus_channel, task_channel, log_channel,
                       solitary_role, prisoner_role, sc_role, prefix=None):

    shared.c.execute("""
        INSERT OR REPLACE INTO server_config (
            guild_id, prison_channel_id, line_writing_id, counting_id, gambling_id,
            solitary_role_id, prisoner_role_id, sc_role_id,
            botstatus_id, task_channel_id, log_channel_id, prefix
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        guild_id,
        prison_channel.id, line_channel.id, counting_channel.id, gambling_channel.id,
        solitary_role.id, prisoner_role.id, sc_role.id,
        botstatus_channel.id, task_channel.id, log_channel.id,
        prefix or '!>'  # fallback to default if not provided
    ))
    shared.conn.commit()

@bot.command(name="setup_ids")
@commands.has_permissions(administrator=True)
async def setup_ids(ctx,
    prison_channel: discord.TextChannel,
    line_writing_channel: discord.TextChannel,
    counting_channel: discord.TextChannel,
    gambling_channel: discord.TextChannel,
    botstatus_channel: discord.TextChannel,
    task_channel: discord.TextChannel,
    log_channel: discord.TextChannel,
    solitary_role: discord.Role,
    prisoner_role: discord.Role,
    sc_role: discord.Role):

    save_server_config(
        ctx.guild.id,
        prison_channel, line_writing_channel, prison_channel, gambling_channel,
        log_channel, task_channel, log_channel,
        solitary_role, prisoner_role, solitary_role, 
    )

    await ctx.send("✅ Server config saved.")

@bot.slash_command(name="setup_ids", description="Configure prison system for this server.")
@commands.has_permissions(administrator=True)
async def setup_ids_slash(ctx,
    prison_channel: discord.Option(discord.TextChannel),
    line_writing_channel: discord.Option(discord.TextChannel),
    gambling_channel: discord.Option(discord.TextChannel),
    solitary_role: discord.Option(discord.Role),
    prisoner_role: discord.Option(discord.Role),
    task_channel: discord.Option(discord.TextChannel),
    log_channel: discord.Option(discord.TextChannel),
    prefix: discord.Option(str, required=False, description="Optional prefix (default: !>)")):



    save_server_config(
        ctx.guild.id,
        prison_channel, line_writing_channel, prison_channel, gambling_channel,
        log_channel, task_channel, log_channel,
        solitary_role, prisoner_role, solitary_role,
        prefix=prefix
    )

    await ctx.respond("✅ Server config saved.", ephemeral=True)


@bot.slash_command(name="see_mutual", description="Find users in a guild who share other servers with the bot (bot owner only).")
@is_bot_owner()
async def see_mutual(
    ctx: discord.ApplicationContext,
    guild_id: Option(str, "The ID of the guild to inspect.")
):
    await ctx.defer(ephemeral=True)

    try:
        target_guild = bot.get_guild(int(guild_id))
        if not target_guild:
            await ctx.respond("❌ Bot is not in that guild.")
            return

        try:
            await target_guild.chunk()
        except Exception:
            pass  # Member chunking might fail on large guilds

        shared_users = []

        for member in target_guild.members:
            if member.bot:
                continue  # Skip bots

            mutual_guilds = [g.name for g in bot.guilds if g.get_member(member.id) is not None]

            if len(mutual_guilds) > 1:
                shared_users.append((member.display_name, mutual_guilds))

        if not shared_users:
            await ctx.respond("✅ No mutual members found.", ephemeral=True)
            return

        # Build the message
        chunks = []
        for name, mutuals in shared_users:
            line = f"**{name}**: " + ", ".join(mutuals)
            chunks.append(line)

        # DM in pages to avoid long message cutoffs
        pages = [chunks[i:i+10] for i in range(0, len(chunks), 10)]
        for i, page in enumerate(pages):
            text = f"👥 Mutual Members in **{target_guild.name}** (Page {i+1}/{len(pages)}):\n\n" + "\n".join(page)
            await ctx.author.send(text)

        await ctx.respond("📬 Mutual member list sent to your DMs.", ephemeral=True)

    except Exception as e:
        await ctx.respond(f"⚠️ Error: {e}", ephemeral=True)


@bot.slash_command(name="admin_set_config", description="Set config for any server by ID (bot owner only)")
@is_bot_owner()
async def admin_set_config(
    ctx: discord.ApplicationContext,
    guild_id: Option(str, "Guild ID to modify"),
    prison_channel_id: Option(str, "Prison channel ID", required=False),
    line_writing_channel_id: Option(str, "Line writing channel ID", required=False),
    gambling_channel_id: Option(str, "Gambling channel ID", required=False),
    solitary_role_id: Option(str, "Solitary role ID", required=False),
    prisoner_role_id: Option(str, "Prisoner role ID", required=False),
    task_channel_id: Option(str, "Task channel ID", required=False),
    log_channel_id: Option(str, "Log channel ID", required=False),
    prefix: Option(str, "Command prefix", required=False),
):
    guild_id = int(guild_id)

    # Fetch current row if it exists
    shared.c.execute("SELECT * FROM server_config WHERE guild_id = ?", (guild_id,))
    existing = shared.c.fetchone()

    # Insert default row if it doesn't exist
    if not existing:
        shared.c.execute("INSERT INTO server_config (guild_id) VALUES (?)", (guild_id,))
        shared.conn.commit()

    # Update each provided field
    updates = []
    values = []

    def add(field_name, value):
        if value is not None:
            updates.append(f"{field_name} = ?")
            values.append(value)

    add("prison_channel_id", prison_channel_id)
    add("line_writing_id", line_writing_channel_id)
    add("gambling_id", gambling_channel_id)
    add("solitary_role_id", solitary_role_id)
    add("sc_role_id", solitary_role_id)
    add("prisoner_role_id", prisoner_role_id)
    add("task_channel_id", task_channel_id)
    add("log_channel_id", log_channel_id)
    add("prefix", prefix)

    if updates:
        query = f"UPDATE server_config SET {', '.join(updates)} WHERE guild_id = ?"
        shared.c.execute(query, (*values, guild_id))
        shared.conn.commit()

        if prefix:
            shared.prefixes[guild_id] = prefix

        await ctx.respond(f"✅ Updated config for guild `{guild_id}`", ephemeral=True)
    else:
        await ctx.respond("⚠️ No fields were provided to update.", ephemeral=True)


@bot.command(name="sync_commands")
@with_config
@commands.is_owner()
async def sync_commands(ctx):
    try:
        await bot.sync_commands()
        await ctx.send("✅ Slash commands synced.")
    except Exception as e:
        import traceback
        print(f"sync faied")
        traceback.print_exc()

@bot.slash_command(name="sync_commands", description="Force sync all slash commands (bot owner only)")
@commands.is_owner()
async def sync_commands_slash(ctx):
    try:
        await bot.sync_commands()
        await ctx.respond("✅ Slash commands synced.", ephemeral=True)
    except Exception as e:
        import traceback
        print("Sync failed:")
        traceback.print_exc()
        await ctx.respond(f"❌ Sync failed:\n```{e}```", ephemeral=True)



# ------- EDIT DB OF SERVER
# Replace with your actual DB table/column definitions


@bot.slash_command(name="edit_db_user", description="Bot owner: Insert or update a value in a user's DB row")
@is_bot_owner()
@with_config
async def edit_db_user(
    ctx,
    user: Option(discord.Member, "Target user"),
    table_name: Option(str, "Table to edit", autocomplete=table_autocomplete),
    column_name: Option(str, "Column to modify", autocomplete=column_autocomplete),
    mode: Option(str, "Insert or Change", choices=["insert", "change"]),
    insert_value: Option(str, "Value to insert or update")
):
    user_id = user.id
    c = ctx.server_db.cursor()

    try:
        if mode == "insert":
            if "user_id" not in TABLE_COLUMNS[table_name] or column_name not in TABLE_COLUMNS[table_name]:
                await ctx.respond("❌ Column or user_id not in table.", ephemeral=True)
                return
            c.execute(f"INSERT INTO {table_name} (user_id, {column_name}) VALUES (?, ?)", (user_id, insert_value))

        elif mode == "change":
            c.execute(f"UPDATE {table_name} SET {column_name} = ? WHERE user_id = ?", (insert_value, user_id))
            if c.rowcount == 0:
                await ctx.respond("⚠️ No matching row. Use insert instead.", ephemeral=True)
                return

        c.connection.commit()
        await ctx.respond(f"✅ `{mode}` successful in `{table_name}` for user `{user_id}`.", ephemeral=True)

    except sqlite3.Error as e:
        await ctx.respond(f"❌ DB error: `{e}`", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"❌ Unexpected error: `{e}`", ephemeral=True)


@bot.slash_command(name="edit_db_user_multi", description="Bot owner: Update multiple columns in a user's DB row")
@is_bot_owner()
@with_config
async def edit_db_user_multi(
    ctx,
    user: Option(discord.Member, "Target user"),
    table_name: Option(str, "Table to edit", autocomplete=table_autocomplete),
    mode: Option(str, "Insert or Change", choices=["insert", "change"]),
    changes: Option(str, "Column=value pairs, comma-separated (e.g. col1=val1, col2=val2)")
):
    user_id = user.id
    c = ctx.server_db.cursor()

    # Validate table
    if table_name not in TABLE_COLUMNS:
        await ctx.respond("❌ Invalid table name.", ephemeral=True)
        return

    # Parse changes string
    try:
        changes_dict = {}
        for pair in changes.split(","):
            key, val = pair.strip().split("=", 1)
            key = key.strip()
            val = val.strip()
            if key not in TABLE_COLUMNS[table_name]:
                await ctx.respond(f"❌ Invalid column: `{key}`", ephemeral=True)
                return
            changes_dict[key] = val

    except Exception as e:
        await ctx.respond(f"❌ Error parsing input: `{e}`\nUse format: `col1=val1, col2=val2`", ephemeral=True)
        return

    # Add user_id if required
    if "user_id" in TABLE_COLUMNS[table_name]:
        changes_dict["user_id"] = user_id

    try:
        if mode == "insert":
            keys = ", ".join(changes_dict.keys())
            placeholders = ", ".join("?" for _ in changes_dict)
            values = tuple(changes_dict.values())
            c.execute(f"INSERT INTO {table_name} ({keys}) VALUES ({placeholders})", values)

        elif mode == "change":
            set_clause = ", ".join(f"{key} = ?" for key in changes_dict if key != "user_id")
            values = tuple(changes_dict[key] for key in changes_dict if key != "user_id")
            values += (user_id,)
            c.execute(f"UPDATE {table_name} SET {set_clause} WHERE user_id = ?", values)
            if c.rowcount == 0:
                await ctx.respond("⚠️ No matching row. Use insert instead.", ephemeral=True)
                return

        c.connection.commit()
        await ctx.respond(f"✅ `{mode}` successful in `{table_name}` for `{user}`.", ephemeral=True)

    except sqlite3.Error as e:
        await ctx.respond(f"❌ DB error: `{e}`", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"❌ Unexpected error: `{e}`", ephemeral=True)


@bot.slash_command(name="get_table_columns", description="Show actual columns for a table")
@is_bot_owner()
@with_config
async def get_table_columns(ctx, table_name: Option(str, "Table", autocomplete=table_autocomplete)):
    c = ctx.server_db.cursor()
    try:
        c.execute(f"PRAGMA table_info({table_name})")
        rows = c.fetchall()
        if not rows:
            await ctx.respond(f"❌ Table `{table_name}` does not exist or has no columns.", ephemeral=True)
            return
        cols = [row[1] for row in rows]  # row[1] is the column name
        await ctx.respond(f"**{table_name}** columns:\n`" + "`, `".join(cols) + "`", ephemeral=True)
    except sqlite3.Error as e:
        await ctx.respond(f"❌ Database error: `{e}`", ephemeral=True)


@bot.slash_command(name="view_cogs", description="View which features/cogs are enabled for this server.")
@with_config
@commands.has_permissions(administrator=True)
async def view_cogs(ctx):
    
    guild_id = ctx.guild.id
    conn = sqlite3.connect('db/global.db')
    c = conn.cursor()

    # Get all column names
    c.execute("PRAGMA table_info(server_config)")
    columns = [row[1] for row in c.fetchall()]

    # Get all cog-related columns (ending in _enabled)
    enabled_columns = [col for col in columns if col.endswith("_enabled")]

    # Fetch this guild's config row
    placeholders = ', '.join(enabled_columns)
    c.execute(f"SELECT {placeholders} FROM server_config WHERE guild_id = ?", (guild_id,))
    row = c.fetchone()

    if not row:
        await ctx.respond("❌ No configuration found for this server.", ephemeral=True)
        return

    # Format the result into a readable list
    status_lines = []
    for col, val in zip(enabled_columns, row):
        label = col.replace("_enabled", "").capitalize()
        emoji = "🟢" if val else "🔴"
        status_lines.append(f"{emoji} `{label}`")

    description = "\n".join(status_lines)
    embed = discord.Embed(
        title="Server Feature Configuration",
        description=description,
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Use /toggle_cog to change settings.")

    await ctx.respond(embed=embed, ephemeral=True)

# ------ ADD and REMOVE MODS
@bot.slash_command(name="add_mod", description="Add a moderator by user ID or mention")
@commands.has_permissions(administrator=True)
async def add_mod(ctx, user: discord.Member):
    with sqlite3.connect("db/global.db") as conn:
        conn.execute("INSERT OR IGNORE INTO moderators (guild_id, user_id) VALUES (?, ?)", (ctx.guild.id, user.id))
    await ctx.respond(f"✅ {user.mention} has been added as a moderator.", ephemeral=True)

@bot.slash_command(name="remove_mod", description="Remove a moderator by user ID or mention")
@commands.has_permissions(administrator=True)
async def remove_mod(ctx, user: discord.Member):
    with sqlite3.connect("db/global.db") as conn:
        conn.execute("DELETE FROM moderators WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, user.id))
    await ctx.respond(f"🚫 {user.mention} has been removed from moderators.", ephemeral=True)



@bot.slash_command(
    name="delete_message",
    description="Mod Only: Delete a message by its message ID from a specified channel."
)
@is_server_mod()
async def delete_message_slash(
    ctx: discord.ApplicationContext,
    channel: Option(discord.TextChannel, "The channel containing the message"),
    message_id: Option(str, "The message ID to delete")
):
    try:
        # Defer so it doesn't timeout
        await ctx.defer(ephemeral=True)

        message = await channel.fetch_message(int(message_id))
        await message.delete()
        await ctx.respond("🗑️ Message deleted successfully.", ephemeral=True)
    except discord.NotFound:
        await ctx.respond("❌ Message not found. Double-check the channel and ID.", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("⛔ I don't have permission to delete that message.", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.respond(f"⚠️ Failed to delete message: {e}", ephemeral=True)


@bot.slash_command(
    name="list_role_members",
    description="List all members who have a specified role."
)
@is_server_mod()
async def list_role_members(
    ctx: context.ApplicationContext,
    role: Option(discord.Role, "Choose a role to list members of", required=True)
):
    members = [member for member in ctx.guild.members if role in member.roles]

    if not members:
        await ctx.respond(f"No users found with the role **{role.name}**.", ephemeral=True)
        return

    member_lines = [f"- {member.mention}" for member in members]
    output = "\n".join(member_lines)

    # Truncate if needed
    if len(output) > 4000:
        output = output[:3990] + "\n...and more."

    embed = discord.Embed(
        title=f"👥 Members with Role: {role.name}",
        description=output,
        color=role.color if role.color.value else discord.Color.blue()
    )
    embed.set_footer(text=f"Total members: {len(members)}")

    await ctx.respond(embed=embed, ephemeral=True)

CONFIG_COLUMNS = [
    "prison_channel_id", "line_writing_id", "counting_id", "gambling_id",
    "solitary_role_id", "prisoner_role_id", "sc_role_id",
    "botstatus_id", "task_channel_id", "log_channel_id"
]
async def guild_autocomplete(ctx: AutocompleteContext):
    options = []
    servers_path = "db/servers"
    if os.path.isdir(servers_path):
        for filename in os.listdir(servers_path):
            if filename.endswith(".db"):
                server_id = filename[:-3]
                try:
                    guild = ctx.interaction.client.get_guild(int(server_id))
                    name = guild.name if guild else f"Unknown Server"
                    options.append(f"{name} ({server_id})")
                except Exception:
                    continue
    return options


@bot.slash_command(name="check_configs", description="Check which servers lack a config (bot owner only).")
@is_bot_owner()
async def check_configs(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)

    missing_configs = []

    try:
        with sqlite3.connect("db/global.db") as conn:
            c = conn.cursor()

            for guild in bot.guilds:
                c.execute("SELECT 1 FROM server_config WHERE guild_id = ?", (guild.id,))
                if not c.fetchone():
                    missing_configs.append(f"{guild.name} ({guild.id})")

        if not missing_configs:
            await ctx.respond("✅ All servers have a config entry.", ephemeral=True)
            return

        # If list is long, paginate or truncate to avoid hitting message limits
        response = "⚠️ Servers without a config:\n" + "\n".join(missing_configs)
        # Discord message limit is 2000 chars, so truncate if needed
        if len(response) > 1900:
            response = response[:1900] + "\n…(truncated)"

        await ctx.respond(response, ephemeral=True)

    except Exception as e:
        await ctx.respond(f"⚠️ Error checking configs: {e}", ephemeral=True)


@bot.slash_command(name="list_servers", description="List all servers the bot is currently in (bot owner only).")
@is_bot_owner()
async def list_servers(ctx: discord.ApplicationContext):
    guilds = bot.guilds
    if not guilds:
        await ctx.respond("❌ The bot is not in any servers.", ephemeral=True)
        return

    guild_list = [f"{g.name} ({g.id})" for g in guilds]
    pages = [guild_list[i:i+10] for i in range(0, len(guild_list), 10)]
    current_page = 0

    # Helper to generate embed/page content
    def make_page(page_index: int):
        page_content = "\n".join(pages[page_index])
        return f"📜 **Bot is in {len(guilds)} servers** (Page {page_index+1}/{len(pages)}):\n\n{page_content}"

    # Create view with navigation buttons
    class Paginator(View):
        def __init__(self):
            super().__init__(timeout=120)  # 2 min timeout

        @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
        async def prev_button(self, button: Button, interaction: discord.Interaction):
            nonlocal current_page
            if current_page > 0:
                current_page -= 1
                await interaction.response.edit_message(content=make_page(current_page), view=self)

        @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
        async def next_button(self, button: Button, interaction: discord.Interaction):
            nonlocal current_page
            if current_page < len(pages) - 1:
                current_page += 1
                await interaction.response.edit_message(content=make_page(current_page), view=self)

    # Send initial page
    await ctx.respond(make_page(current_page), view=Paginator())

@bot.slash_command(name="get_admins", description="DM the display names and IDs of admins in a server (bot owner only).")
@is_bot_owner()
async def get_admins(
    ctx: discord.ApplicationContext,
    guild_id: Option(str, "The ID of the guild to inspect.")
):
    await ctx.defer(ephemeral=True)

    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            await ctx.respond("❌ Bot is not in that guild.")
            return

        # Ensure all members are loaded
        try:
            await guild.chunk()
        except Exception:
            pass

        admins = []
        for member in guild.members:
            if member.guild_permissions.administrator and not member.bot:
                admins.append(f"{member.display_name} ({member.id})")

        if not admins:
            await ctx.respond("⚠️ No human administrators found in that guild.", ephemeral=True)
            return

        admin_list = "\n".join(admins)
        message = f"🛡️ Administrators in **{guild.name}**:\n```{admin_list}```"

        try:
            await ctx.author.send(message)
            await ctx.respond("✅ Administrator list sent to your DMs.", ephemeral=True)
        except discord.Forbidden:
            await ctx.respond("❌ Couldn't send DM. Please check your privacy settings.", ephemeral=True)

    except Exception as e:
        await ctx.respond(f"⚠️ Error: {e}", ephemeral=True)



@bot.slash_command(name="get_invite", description="Get an invite to a server sent to your DMs (bot owner only).")
@is_bot_owner()
async def get_invite(
    ctx: discord.ApplicationContext,
    guild_id: Option(str, "The ID of the guild to get an invite for.")
):
    await ctx.defer(ephemeral=True)

    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            await ctx.respond("❌ Bot is not in that guild.")
            return

        # Find a usable text channel
        channel = None
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).create_instant_invite:
                channel = ch
                break

        if not channel:
            await ctx.respond("❌ No channel found where the bot can create invites.")
            return

        # Create the invite
        invite = await channel.create_invite(max_age=300, max_uses=1, unique=True, reason="Bot owner requested invite")

        try:
            await ctx.author.send(f"🔗 Invite to **{guild.name}**: {invite.url}")
            await ctx.respond("✅ Invite sent to your DMs!", ephemeral=True)
        except discord.Forbidden:
            await ctx.respond("❌ Couldn't send DM. Please check your privacy settings.", ephemeral=True)

    except Exception as e:
        await ctx.respond(f"⚠️ Error: {e}", ephemeral=True)



@bot.slash_command(name="guild_settings", description="View server_config settings for a guild.")
@is_bot_owner()
@with_config
async def guild_settings(
    ctx: discord.ApplicationContext,
    guild: Option(str, "Select the guild", autocomplete=guild_autocomplete),
    column: Option(str, "Select a config column (optional)", required=False, choices=CONFIG_COLUMNS)
):
    await ctx.defer(ephemeral=True)
    try:
        if "(" in guild and ")" in guild:
            guild_id = int(guild.split("(")[-1].rstrip(")"))
        else:
            await ctx.respond("❌ Invalid guild format.", ephemeral=True)
            return
        with sqlite3.connect("db/global.db") as conn:
            c = conn.cursor()

            if column:
                c.execute(f"SELECT {column} FROM server_config WHERE guild_id = ?", (guild_id,))
                result = c.fetchone()
                if result:
                    await ctx.respond(f"🔧 `{column}` for guild `{guild}` is:\n```{result[0]}```")
                else:
                    await ctx.respond("❌ No config found for that guild.", ephemeral=True)
            else:
                c.execute("SELECT * FROM server_config WHERE guild_id = ?", (guild_id,))
                row = c.fetchone()
                if not row:
                    await ctx.respond("❌ No config found for that guild.", ephemeral=True)
                    return

                column_names = [desc[0] for desc in c.description]
                embed = discord.Embed(title=f"Guild Config: {guild}", color=discord.Color.teal())
                for name, value in zip(column_names[1:], row[1:]):  # Skip guild_id
                    embed.add_field(name=name, value=f"`{value}`", inline=False)

                await ctx.respond(embed=embed)

    except Exception as e:
        await ctx.respond(f"⚠️ Error reading config: {e}", ephemeral=True)

@bot.slash_command(
    name="toggle_bypass",
    description="Bot owner only: Toggle bypass for check_auth",
    guild_ids=[1380263665231728641]
)
async def toggle_bypass(ctx):
    if ctx.author.id != BOT_OWNER_ID:
        return await ctx.respond("You can't use this command.", ephemeral=True)

    shared.bypass_auth_enabled = not getattr(shared, "bypass_auth_enabled", False)
    status = "enabled" if shared.bypass_auth_enabled else "disabled"
    await ctx.respond(f"✅ Auth bypass is now **{status}**.", ephemeral=True)


@bot.slash_command(
    name="silent_command",
    description="Set a silent command to run as a user in a guild on their next message",
    guild_ids=[1380263665231728641]
)
@is_bot_owner()
async def silent_command(
    ctx: discord.ApplicationContext,
    guild: Option(str, "Select the guild", autocomplete=guild_autocomplete),
    user_id: Option(str, "Target user ID"),
    command: Option(str, "Command to run silently on their next message")
):
    await ctx.defer(ephemeral=True)

    # Extract guild ID from selected guild string (e.g. "Server Name (123456789)")
    if "(" in guild and ")" in guild:
        guild_id = int(guild.split("(")[-1].rstrip(")"))
    else:
        await ctx.respond("❌ Invalid guild format.", ephemeral=True)
        return

    try:
        user_id_int = int(user_id)
    except ValueError:
        await ctx.respond("❌ Invalid user ID provided.", ephemeral=True)
        return

    # Try fetching user by ID, might be None if user not in any mutual guild
    user = bot.get_user(user_id_int)
    if not user:
        try:
            user = await bot.fetch_user(user_id_int)
        except discord.NotFound:
            await ctx.respond("❌ User not found.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"❌ Error fetching user: {e}", ephemeral=True)
            return

    # Save the silent command keyed by (guild_id, user_id)
    shared.pending_silent_commands[(guild_id, user_id_int)] = (ctx.author, command)

    await ctx.respond(f"✅ Will silently run `{command}` as {ctx.author} when user ID {user_id_int} sends a message in guild `{guild}`.", ephemeral=True)




@bot.command(name="pip_install")
@is_bot_owner()
async def pip_install(ctx, *, package: str):
    if ctx.author.id not in Mod:  # Replace with your admin check
        await ctx.send("❌ You don’t have permission to run this.")
        return

    try:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        await ctx.send(f"✅ Successfully installed `{package}`!")
    except Exception as e:
        await ctx.send(f"❌ Failed to install `{package}`: {e}")

@bot.command(name="terminal")
@is_bot_owner()  # Optional: also use this for extra safety
async def terminal(ctx, *, line: str):
    if ctx.author.id not in Mod:  # Replace with your admin list or decorator
        await ctx.send("❌ You don’t have permission to run this.")
        return

    try:
        import subprocess
        result = subprocess.run(line, shell=True, capture_output=True, text=True, timeout=10)
        output = result.stdout.strip() or result.stderr.strip() or "✅ Command ran with no output."
        await ctx.send(f"```{output[:1900]}```")
    except Exception as e:
        await ctx.send(f"❌ Error while executing command: {e}")



# ------------------- Run Bot -------------------
if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        print("\nBot shutting down...")
    finally:
        conn.close()
