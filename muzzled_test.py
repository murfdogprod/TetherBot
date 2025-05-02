import discord
from discord.ext import commands, tasks
import sqlite3
import time
import asyncio
from datetime import datetime, timedelta, UTC, timezone
import re
import random
import pronouncing
import base64
import os
import sys
import subprocess
from gtts import gTTS
from collections import deque
from dotenv import load_dotenv
import requests
import string

# ------------------- Configuration -------------------
load_dotenv(dotenv_path="tetherbot.env")

# Read values from the environment
BOT_TOKEN = os.getenv('BOT_TOKEN')
PRISON_CHANNEL_ID = int(os.getenv('PRISON_CHANNEL_ID'))
LINE_WRITING_ID = int(os.getenv('LINE_WRITING_ID'))
COUNTING_ID = 1366615223221485621  # Channel ID for counting
GAMBLING_ID = 1366615223221485621 # Channel ID for gambling
PRISON_CATEGORY_ID = int(os.getenv('PRISON_CATEGORY_ID'))
ARCHIVE_CATEGORY_ID = int(os.getenv('ARCHIVE_CATEGORY_ID'))
SOLITARY_ROLE_ID = int(os.getenv('SOLITARY_ROLE_ID'))
PRISONER_ROLE_ID = int(os.getenv('PRISONER_ROLE_ID'))
SC_ROLE_ID = int(os.getenv('SC_ROLE_ID'))
DEFAULT_AUTH_MODE = os.getenv('DEFAULT_AUTH_MODE')

# Configuration (USE ENVIRONMENT VARIABLES IN PRODUCTION!)
PISHOCK_USERNAME = (os.getenv('PISHOCK_USERNAME')) # PiShock username
PISHOCK_API_KEY = (os.getenv('PISHOCK_API_KEY'))  # PiShock API key


# Locked Users
locked_users = set()
locked_by_map = {}  # user_id -> locked_by
AUTHORIZED_LOCK_MANAGERS = {1146469921270792326}  # Add more IDs if needed




# ------------------- Database Setup -------------------
conn = sqlite3.connect('abot_data.db')
c = conn.cursor()

# Create tables
c.execute('''CREATE TABLE IF NOT EXISTS gagged_users (
            user_id INTEGER PRIMARY KEY,
            type TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS prison_users (
            user_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            entered_balance INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_auth (
            user_id INTEGER PRIMARY KEY,
            auth_level TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS cooldown_users (
            user_id INTEGER PRIMARY KEY,
            cooldown INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS solitary_confinement (
            user_id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            archive_date TIMESTAMP)''')
c.execute('''CREATE TABLE IF NOT EXISTS enforced_words (
            user_id INTEGER,
            word TEXT,
            initial_time INTEGER,
            added_time INTEGER,
            PRIMARY KEY(user_id, word))''')
c.execute('''CREATE TABLE IF NOT EXISTS enforcement_offenses (
            user_id INTEGER PRIMARY KEY,
            count INTEGER DEFAULT 0)''')
c.execute('''CREATE TABLE IF NOT EXISTS banned_words (
            user_id INTEGER,
            word TEXT,
            initial_time INTEGER,
            added_time INTEGER,
            PRIMARY KEY(user_id, word))''')
c.execute('''CREATE TABLE IF NOT EXISTS ignored_users (
            user_id INTEGER PRIMARY KEY)''')
c.execute('''
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        enforcement_action TEXT DEFAULT 'timeout'
    )
''')
c.execute('''
CREATE TABLE IF NOT EXISTS line_assignments (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    line TEXT NOT NULL,
    lines_required INTEGER NOT NULL,
    penalty_lines INTEGER DEFAULT 0,
    last_submission TEXT,
    assigned_by INTEGER NOT NULL
)
''')
c.execute('''
CREATE TABLE IF NOT EXISTS active_line_writers (
    user_id INTEGER PRIMARY KEY,
    line TEXT NOT NULL,
    lines_required INTEGER NOT NULL,
    lines_written INTEGER NOT NULL DEFAULT 0,
    penalty_lines INTEGER NOT NULL DEFAULT 0  -- Added column to track penalty lines
)
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS user_wallets (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER DEFAULT 1000  -- Starting balance of 1000
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        initiator_id INTEGER NOT NULL,
        opponent_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        game TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS daily_claims (
        user_id INTEGER PRIMARY KEY,
        last_claim TIMESTAMP
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS timer_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        timer_name TEXT NOT NULL,
        start_time TEXT NOT NULL
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS command_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        command TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')
c.execute('''
CREATE TABLE IF NOT EXISTS allowed_users (
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, channel_id)
)
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS locked_users (
        user_id INTEGER PRIMARY KEY,
        locked_by INTEGER
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS pishock_users (
        user_id INTEGER PRIMARY KEY,
        code STRING NOT NULL
    )
''')
c.execute('''
CREATE TABLE IF NOT EXISTS solitary_pings (
    thread_id INTEGER PRIMARY KEY,
    last_ping TEXT
)
''')




conn.commit()


# ------------------- Global Stores -------------------
gagged_users = {}
prison_users = {}
cooldown_users = {}
last_message_times = {}
gagged_messages = {}
solitary_confinement = {}  # {user_id: channel_id}
enforced_words = {}  # {user_id: set(words)}
enforcement_offenses = {}  # {user_id: offense_count}
banned_words = {}
ignored_users = set()
line_writing_sessions = {}
clicker_users = {824747106370584599, 169981533187211264, 380625660277948426, 1146469921270792326}
user_pishock_codes = {}  # {user_id: pishock_code}
current_number = 0
last_user = None
# Track usage per user
user_command_timestamps = {}
user_warned = {}

COMMAND_LIMIT = 5
TIME_WINDOW = 10  # seconds




# ------------------- Bot Setup -------------------
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(
    command_prefix="!>",
    intents=intents,
    help_command=None,
    case_insensitive=True
)

URL_REGEX = re.compile(r'https?://\S+')
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="the chat"))
    

    # Load existing data
    c.execute("SELECT user_id, type FROM gagged_users")
    gagged_users.update({row[0]: row[1] for row in c.fetchall()})

    c.execute("SELECT user_id, code FROM pishock_users")
    user_pishock_codes.update({row[0]: row[1] for row in c.fetchall()})

    c.execute("SELECT user_id, channel_id FROM prison_users")
    prison_users.update({row[0]: row[1] for row in c.fetchall()})

    c.execute("SELECT user_id, cooldown FROM cooldown_users")
    for row in c.fetchall():
        cooldown_users[row[0]] = row[1]
        last_message_times[row[0]] = 0

    c.execute("SELECT user_id, thread_id FROM solitary_confinement")
    for row in c.fetchall():
        solitary_confinement[row[0]] = row[1]

    # Load enforced words, now storing initial_time and added_time
    c.execute("SELECT user_id, word, initial_time, added_time FROM enforced_words")
    for row in c.fetchall():
        user_id, word, initial_time, added_time = row
        if user_id not in enforced_words:
            enforced_words[user_id] = {}
        enforced_words[user_id][word.lower()] = {"initial_time": initial_time, "added_time": added_time}

    # Load enforcement offenses
    c.execute("SELECT user_id, count FROM enforcement_offenses")
    for row in c.fetchall():
        user_id, offense_count = row
        enforcement_offenses[user_id] = offense_count

    # Load banned words, now storing initial_time and added_time
    c.execute("SELECT user_id, word, initial_time, added_time FROM banned_words")
    for row in c.fetchall():
        user_id, word, initial_time, added_time = row
        if user_id not in banned_words:
            banned_words[user_id] = {}
        banned_words[user_id][word.lower()] = {"initial_time": initial_time, "added_time": added_time}

    c.execute("SELECT user_id FROM ignored_users")
    ignored_users.update(row[0] for row in c.fetchall())

    c.execute("SELECT user_id, line, lines_required, lines_written FROM active_line_writers")
    rows = c.fetchall()

    # Populate the line_writing_sessions dictionary with data from the database
    for row in rows:
        user_id, line, lines_required, lines_written = row
        line_writing_sessions[user_id] = {
            "line": line,
            "lines_required": lines_required,
            "lines_written": lines_written
        }



    c.execute("SELECT user_id, locked_by FROM locked_users")
    rows = c.fetchall()
    locked_users.clear()
    locked_by_map.clear()
    for user_id, locked_by in rows:
        locked_users.add(user_id)
        locked_by_map[user_id] = locked_by


    # Now line_writing_sessions is populated with the active sessions
    print(line_writing_sessions)
            
    print(f"Loaded: {len(gagged_users)} gagged, {len(prison_users)} prison, {len(cooldown_users)} cooldown users")
    print(f"Loaded {len(solitary_confinement)} solitary confinement records")




@bot.event
async def on_message(message):
    '''
    global current_number, last_user

    # Check if the message is in the counting channel and is not from the bot
    if message.channel.id == COUNTING_ID and not message.author.bot:
        print("yes")
        # Extract the number from the message
        match = re.match(r'^(\d+)$', message.content.strip())
        if match:
            user_number = int(match.group(1))

            # Ensure the number is the next in the sequence
            if user_number == current_number + 1:
                # Check if the same user sent the previous number
                if message.author == last_user:
                    await message.delete()
                    await message.author.send("You can't send two consecutive numbers. Please let someone else go next.")
                else:
                    # Update the current number and last user
                    current_number = user_number
                    last_user = message.author
                    await message.add_reaction('✅')
            else:
                await message.delete()
                await message.author.send(f"Please send the next number: {current_number + 1}.")
        else:
            await message.delete()
            await message.author.send("Please send a valid number.")
    await bot.process_commands(message)
    '''

    if message.author.bot:
        return
    if message.content.startswith("(("):
        if message.author.id == 169981533187211264:
            shock_result = send_pishock_command(message.author.id, op=0, intensity=80, duration=2)
            return
        return  # Completely ignore this message

    if message.attachments:
        return

    if not message.guild:
        return

    if message.stickers:
        return  # Ignore messages with stickers
    

    author = message.author  # guaranteed to be a Member now


    user_id = message.author.id
    is_command = message.content.startswith("!>")
    content = message.content.lower()
    con_line = message.content.strip()
    channel_id = message.channel.id

    if not is_command and URL_REGEX.search(content):
        await bot.process_commands(message)
        return
    
    if not is_command:
        if message.channel.id == GAMBLING_ID and (author in players or author in queue):
            return

        c.execute("SELECT 1 FROM allowed_users WHERE user_id = ? AND channel_id = ?", 
            (message.author.id, message.channel.id))
        if c.fetchone():
            return


        if message.author.id in user_pishock_codes and not message.author.bot:
            if random.random() < 0.2:  # 20% chance
                try:
                    await message.add_reaction('⚡')
                except discord.errors.Forbidden:
                    print(f"Missing permissions to add reactions in {message.channel}")
                except Exception as e:
                    print(f"Error adding reaction: {e}")

    user = message.author
    # Penalty check for users who started but typed in the wrong channel
    # Fetch the user's current line assignment from active_line_writers
    if not is_command and user_id in line_writing_sessions and channel_id != LINE_WRITING_ID:
        c.execute("SELECT line, lines_required, lines_written, penalty_lines FROM active_line_writers WHERE user_id = ?", (user_id,))
        row = c.fetchone()

        if row:
            expected_line, lines_required, lines_written, penalty = row
            session = line_writing_sessions[user_id]  # Retrieve the session data
            session["lines_required"] += penalty
            new_count = lines_required + penalty  # Add the penalty to the remaining lines
    

            await message.channel.send(
                f"⚠️ {user.mention}, you typed outside the line-writing channel. "
                f"You've been penalized with **{penalty} more lines**!"
            )


             # Add PiShock penalty if user is registered
            if user_id in user_pishock_codes:
                shock_result = send_pishock_command(user_id, op=0, intensity=80, duration=2)
                if shock_result.get("Success"):
                    await message.channel.send(f"⚡ Added 80% shock penalty for {user.display_name}!")
                else:
                    print(f"PiShock failed for {user_id}: {shock_result.get('Message')}")

            # Update the session in memory
            writing_channel = message.guild.get_channel(LINE_WRITING_ID)
            left = session["lines_required"]
            if writing_channel:
                await writing_channel.send(
                    f"{user.mention}, now you must write **{left} lines**."
                )

            c.execute("UPDATE active_line_writers SET lines_required = ? WHERE user_id = ?", (new_count, user_id))
            conn.commit()
            

    # Line writing progress logic
    # Line writing progress logic
    if not is_command and channel_id == LINE_WRITING_ID and user_id in line_writing_sessions:
        # Fetch the user's current line assignment from active_line_writers
        c.execute("SELECT line, lines_required, lines_written, penalty_lines FROM active_line_writers WHERE user_id = ?", (user_id,))
        row = c.fetchone()

        if row:
            expected_line, lines_required, lines_written, penalty = row
            expected_line = expected_line.strip()  # Clean up the line text
            session = line_writing_sessions[user_id]  # Retrieve the session data

            # Check if the user typed the correct line
            if con_line == expected_line:
                # Increment the count of lines written
                session["lines_written"] += 1
                new_count = lines_required - 1  # Decrease lines_left as they wrote a correct line
                await message.add_reaction("✅")  # Add a check mark for correct entry
                print(session["lines_written"])

                # Update lines_written in the active_line_writers table
                c.execute("UPDATE active_line_writers SET lines_written = ? WHERE user_id = ?", (session["lines_written"], user_id))
                conn.commit()

                # MP3 playback (1 in 10 chance) if user is in clicker_users and in VC
                if user_id in clicker_users and random.randint(1, 10) == 1:
                    voice_state = message.author.voice
                    if voice_state and voice_state.channel:
                        voice_client = discord.utils.get(bot.voice_clients, guild=message.guild)

                        try:
                            # Only play if not already playing
                            if not voice_client.is_playing():
                                audio = discord.FFmpegPCMAudio("non-suspicious sound.mp3")
                                voice_client.play(audio)

                                while voice_client.is_playing():
                                    await asyncio.sleep(1)

                                # Do not auto-disconnect — let !>vc control that
                        except Exception as e:
                            print(f"Voice playback error: {e}")
                    else:
                        # If not in VC, fallback to sending the MP3 in text channel
                        mp3_file_path = "non-suspicious sound.mp3"
                        if os.path.exists(mp3_file_path):
                            await message.channel.send(file=discord.File(mp3_file_path))

            else:
                # Incorrect line, apply penalty
                session["lines_required"] += penalty
                new_count = lines_required + penalty  # Add the penalty to the remaining lines
                await message.add_reaction("❌")  # Add an "X" mark for incorrect entry

                # Add PiShock penalty if user is registered
                if user_id in user_pishock_codes:
                    shock_result = send_pishock_command(user_id, op=0, intensity=80, duration=2)
                    if shock_result.get("Success"):
                        await message.channel.send(f"⚡ Added 80% shock penalty for {user.display_name}!")
                    else:
                        print(f"PiShock failed for {user_id}: {shock_result.get('Message')}")

                # Update lines_required with the penalty in active_line_writers table
                c.execute("UPDATE active_line_writers SET lines_required = ? WHERE user_id = ?", (new_count, user_id))
                conn.commit()

            # Check if the user has completed the assignment
            if new_count <= 0:
                # Delete the user's entry from both active_line_writers and line_assignments
                c.execute("DELETE FROM active_line_writers WHERE user_id = ?", (user_id,))
                conn.commit()

                # Remove the assignment from the active line writing sessions
                del line_writing_sessions[user_id]

                await message.channel.send(f"🎉 {user.mention} has completed their assigned lines!")

            else:
                # Update the remaining lines for the assignment in active_line_writers
                c.execute("UPDATE active_line_writers SET lines_required = ? WHERE user_id = ?", (new_count, user_id))
                conn.commit()

        return


    if user_id in prison_users and message.channel.id == LINE_WRITING_ID and user_id in [row[0] for row in c.execute("SELECT user_id FROM line_assignments").fetchall()] and user_id in line_writing_sessions:
        await bot.process_commands(message)
        return


    # Prison check
    if not is_command and user_id in prison_users:
        if message.channel.id != prison_users[user_id]:
            try:
                await message.delete()
                prison_channel = bot.get_channel(prison_users[user_id])
                if user_id in user_pishock_codes:
                    shock_result = send_pishock_command(user_id, op=0, intensity=80, duration=2)
                    if shock_result.get("Success"):
                        await message.channel.send(
                            f"⚡ Added 80% shock penalty for {user.display_name}!",
                            delete_after=30)
                    else:
                        print(f"PiShock failed for {user_id}: {shock_result.get('Message')}")
                await message.channel.send(
                    f"🔒 You're restricted to {prison_channel.mention if prison_channel else '#prison'}",
                    delete_after=10
                )
            except:
                pass
            return

        
    # Enforcement check
    if not is_command and user_id in enforced_words:
        content = message.content.lower()

        # Get the required words for this user
        required_words = set(enforced_words[user_id].keys())
        found_words = {w for w in required_words if w in content}

        # If any required words are missing
        if found_words != required_words:
            try:
                await message.delete()
            except:
                pass

            missing_words = required_words - found_words
            total_timeout = 0

            # Calculate total timeout from missing words
            for word in missing_words:
                word_data = enforced_words[user_id].get(word, {})
                initial = word_data.get("initial_time", 60)
                added = word_data.get("added_time", 30)
                new_initial = initial + added
                total_timeout += new_initial

                # Update in-memory
                enforced_words[user_id][word]["initial_time"] = new_initial

                # Update DB
                c.execute("""
                    UPDATE enforced_words
                    SET initial_time = ?
                    WHERE user_id = ? AND word = ?
                """, (new_initial, user_id, word))

            # Get and update offense count
            offense_count = enforcement_offenses.get(user_id, 0) + 1
            enforcement_offenses[user_id] = offense_count

            c.execute("INSERT OR REPLACE INTO enforcement_offenses (user_id, count) VALUES (?, ?)",
                      (user_id, offense_count))
            conn.commit()

            # Determine enforcement action(s) from user_settings
            actions = get_user_enforcement_action(user_id)
            timeout_applied = False
            warning_msg = f"⚠️ {message.author.mention} Missing required words: {', '.join(missing_words)}. "

            if "timeout" in actions:
                timeout_duration = total_timeout
                timeout_until = datetime.now(UTC) + timedelta(seconds=timeout_duration)

                try:
                    await message.author.timeout(
                        timeout_until,
                        reason=f"Missing enforced words: {', '.join(missing_words)} (Offense #{offense_count})"
                    )
                    timeout_applied = True
                    warning_msg += f"Timed out for {timeout_duration}s (Offense #{offense_count})"
                except (discord.Forbidden, discord.HTTPException):
                    warning_msg += "Could not apply timeout!"

            # Apply "cooldown" logic — just add time to the user's cooldown
            if "cooldown" in actions:
                total_added = sum(enforced_words[user_id][word].get("added_time", 30) for word in missing_words)

                # Add or update cooldown
                cooldown_users[user_id] = cooldown_users.get(user_id, 0) + total_added
                last_message_times[user_id] = time.time()

                # Optionally, update cooldown in DB if you store it
                c.execute("""
                    INSERT OR REPLACE INTO cooldown_users (user_id, cooldown)
                    VALUES (?, ?)
                """, (user_id, cooldown_users[user_id]))


            if "gag" in actions:
                gag_type = gagged_users.get(user_id, "loose")
                gag_func = gag_functions.get(gag_type)
                gagged_text = gag_func(message.content) if gag_func else "[🤐 gagged]"

                gag_msg = await message.channel.send(
                    content=f"{message.author.mention}: {gagged_text}",
                    allowed_mentions=discord.AllowedMentions(users=False)
                )

                gagged_messages[gag_msg.id] = (user_id, message.content)
                await gag_msg.add_reaction("💣")
                await gag_msg.add_reaction("👁️")
                if user_id in user_pishock_codes:
                    try:
                        await message.add_reaction('⚡')
                    except discord.errors.Forbidden:
                        print(f"Missing permissions to add reactions in {message.channel}")
                    except Exception as e:
                        print(f"Error adding reaction: {e}")
            else:
                gag_msg = await message.channel.send(
                    content=f"{warning_msg}",
                    allowed_mentions=discord.AllowedMentions(users=False)
                )

                gagged_messages[gag_msg.id] = (user_id, message.content)
                await gag_msg.add_reaction("💣")
                await gag_msg.add_reaction("👁️")
                return

        
            await message.channel.send(warning_msg, delete_after=30)
            return



    # Banned words check
    # Banned words check
    if not is_command and user_id in banned_words:
        content = message.content.lower()
        
        triggered_words = {word for word in banned_words[user_id] if word in content}
        
        if triggered_words:
            try:
                await message.delete()
            except:
                pass

            total_timeout = 0
            for word in triggered_words:
                word_data = banned_words[user_id].get(word, {})
                initial = word_data.get("initial_time", 60)
                added = word_data.get("added_time", 30)
                new_initial = initial + added
                total_timeout += new_initial

                # Update in-memory
                banned_words[user_id][word]["initial_time"] = new_initial

                # Update DB
                c.execute("""
                    UPDATE banned_words
                    SET initial_time = ?
                    WHERE user_id = ? AND word = ?
                """, (new_initial, user_id, word))

            # Get user's configured actions
            actions = get_user_enforcement_action(user_id)

            # Handle offenses
            offense_count = enforcement_offenses.get(user_id, 0) + 1
            enforcement_offenses[user_id] = offense_count

            c.execute("INSERT OR REPLACE INTO enforcement_offenses (user_id, count) VALUES (?, ?)",
                      (user_id, offense_count))
            conn.commit()

            warning_msg = f"⛔ {message.author.mention} Used banned words: {', '.join(triggered_words)}. "
            timeout_applied = False

            if "timeout" in actions:
                timeout_duration = total_timeout
                timeout_until = datetime.now(UTC) + timedelta(seconds=timeout_duration)

                try:
                    await message.author.timeout(
                        timeout_until,
                        reason=f"Banned words used: {', '.join(triggered_words)} (Offense #{offense_count})"
                    )
                    timeout_applied = True
                    warning_msg += f"Timed out for {timeout_duration}s. "
                except (discord.Forbidden, discord.HTTPException):
                    warning_msg += "Could not apply timeout. "

            # Apply "cooldown" logic — add time to cooldown counter
            if "cooldown" in actions:
                word_data = banned_words[user_id].get(word, {})
                added_time = word_data.get("added_time", 30)

                cooldown_users[user_id] = cooldown_users.get(user_id, 0) + added_time
                last_message_times[user_id] = time.time()

                c.execute("""
                    INSERT OR REPLACE INTO cooldown_users (user_id, cooldown)
                    VALUES (?, ?)
                """, (user_id, cooldown_users[user_id]))



            if "gag" in actions:
                gag_type = gagged_users.get(user_id, "loose")
                gag_func = gag_functions.get(gag_type)
                gagged_text = gag_func(message.content) if gag_func else "[🤐 gagged]"

                gag_msg = await message.channel.send(
                    content=f"{message.author.mention}: {gagged_text}",
                    allowed_mentions=discord.AllowedMentions(users=False)
                )
                gagged_messages[gag_msg.id] = (user_id, message.content)
                await gag_msg.add_reaction("💣")
                await gag_msg.add_reaction("👁️")
            else:
                gag_msg = await message.channel.send(
                    content=f"{warning_msg}",
                    allowed_mentions=discord.AllowedMentions(users=False)
                )

                gagged_messages[gag_msg.id] = (user_id, message.content)
                await gag_msg.add_reaction("💣")
                await gag_msg.add_reaction("👁️")
                return

            await message.channel.send(warning_msg, delete_after=30)
            return
        






    # Cooldown check
    if not is_command and user_id in cooldown_users:
        now = time.time()
        elapsed = now - last_message_times.get(user_id, 0)
        if elapsed < cooldown_users[user_id]:
            try:
                await message.delete()
                remaining = int(cooldown_users[user_id] - elapsed)
                warning = await message.channel.send(
                    f"⏳ {message.author.mention} Wait {remaining}s before messaging again",
                    delete_after=30
                )
            except:
                pass
            return
        last_message_times[user_id] = now



    # Gag check
    if not is_command and user_id in gagged_users:
        gag_type = gagged_users[user_id]
        print(f"Gag type for user {user_id}: {gag_type}")

        try:
            await message.delete()

            # Apply the gag using the function map
            gag_func = gag_functions.get(gag_type)
            if gag_func:
                gagged_text = gag_func(message.content)
            else:
                gagged_text = "🤐 [Unknown gag style]"

            # Send the gagged version
            gag_msg = await message.channel.send(
                content=f"{message.author.mention}: {gagged_text}",
                allowed_mentions=discord.AllowedMentions(users=False)
            )

            gagged_messages[gag_msg.id] = (user_id, message.content)
            await gag_msg.add_reaction("💣")
            await gag_msg.add_reaction("👁️")



        except Exception as e:
            print(f"Gag handling failed for user {user_id}: {e}")
        return



    await bot.process_commands(message)

@bot.event
async def on_reaction_add(reaction, user):
    target_user = reaction.message.author
    if user.bot or user.id in ignored_users:
        return
 

    if (target_user.id in user_pishock_codes and reaction.message.guild):
        
        # Send 45% shock
        result = send_pishock_command(target_user.id, op=0, intensity=55, duration=2)
        
        # Add confirmation reaction
        try:
            if result.get("Success"):
                await reaction.message.add_reaction('✅')
            else:
                await reaction.message.add_reaction('❌')
                print(f"Shock failed for {target_user}: {result.get('Message')}")
        except Exception as e:
            print(f"Error adding confirmation reaction: {e}")


    msg_id = reaction.message.id
    if msg_id not in gagged_messages:
        return

    author_id, original_content = gagged_messages[msg_id]

    if reaction.message.author != bot.user:
        return
    

    try:
        if str(reaction.emoji) == "💣":
            await reaction.message.delete()
            del gagged_messages[msg_id]

        elif str(reaction.emoji) == "👁️":
            author = reaction.message.guild.get_member(author_id)

            # Get gag type + color
            gag_type = gagged_users.get(author_id, "loose")
            color = gag_colors.get(gag_type, discord.Color.default())

            # Build and send embed
            embed = discord.Embed(description=original_content, color=color)
            embed.set_author(name=f"Message from {author.display_name}", icon_url=author.display_avatar.url)
            #embed.set_footer(text=f"Gag type: {gag_type}")

            await reaction.message.edit(embed=embed)
            await reaction.message.clear_reactions()
            await reaction.message.add_reaction("💣")
            await reaction.message.add_reaction("⬆️")

            async def remove_reactions_after_delay():
                await asyncio.sleep(300)  # 5 minutes
                await reaction.message.clear_reactions()

            # Start the cleanup task
            asyncio.create_task(remove_reactions_after_delay())

        elif str(reaction.emoji) == "⬆️":
            await reaction.message.edit(embed=None)
            await reaction.message.clear_reactions()
            await reaction.message.add_reaction("💣")
            await reaction.message.add_reaction("👁️")

    except Exception as e:
        print(f"Reaction handling failed: {e}")




async def check_auth(ctx, target):
    c.execute("SELECT auth_level FROM user_auth WHERE user_id = ?", (target.id,))
    result = c.fetchone()
    auth_level = result[0] if result else DEFAULT_AUTH_MODE

    if auth_level == "public" and target.id == ctx.author.id:
        return False
    if auth_level == "off" and target.id != ctx.author.id:
        return False
    if auth_level == "ask" and target.id != ctx.author.id:
        try:
            requestor = ctx.author
            dm_channel = await target.create_dm()

            # Notify the target
            await dm_channel.send(
                f"🔐 {requestor.mention} is requesting to modify your status.\n"
                f"Reply with **`yes`** to allow or **`no`** to deny. You have 5 minutes."
            )

            # Notify the requestor
            requestor_dm = await requestor.create_dm()
            await requestor_dm.send(f"🕓 Waiting for {target.display_name} to respond to your request...")

            def check(m):
                return m.author == target and m.channel == dm_channel and m.content.lower() in ["yes", "no"]

            reply = await bot.wait_for('message', timeout=300.0, check=check)

            if reply.content.lower() == "yes":
                await requestor_dm.send(f"✅ {target.display_name} **approved** your request.")
                return True
            else:
                await requestor_dm.send(f"❌ {target.display_name} **denied** your request.")
                return False

        except asyncio.TimeoutError:
            await requestor_dm.send(f"⌛ {target.display_name} did not respond in time. Request timed out.")
            return False

    return True

# ------------------- Commands -------------------
@bot.command()
# @commands.has_permissions(manage_messages=True)
async def enforce(ctx, user: discord.Member, word: str = None, initial_time: int = 60, added_time: int = 30):
    """Force a user to include specific words in their messages
    Usage: !>enforced @user "required_word" [initial_time] [added_time]"""

    target = user or ctx.author

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # Auth check for others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    # Clean and validate input
    if word is None or len(word.strip()) < 1:
        await ctx.message.add_reaction("❌")
        return

    word = word.strip().lower()

    if check_word_conflict(user.id, word, "enforce"):
        await ctx.send("❌ That word conflicts with a banned word already set for this user.")
        return

    # Ensure initial_time and added_time are integers and have valid defaults
    try:
        initial_time = int(initial_time) if initial_time is not None else 60
        added_time = int(added_time) if added_time is not None else 30
    except ValueError:
        await ctx.send("❌ Initial time and added time must be numbers.")
        return

    try:
        if user.id not in enforced_words:
            enforced_words[user.id] = {}

        if word in enforced_words[user.id]:
            await ctx.message.add_reaction("❌")
            return

        # Add word to in-memory dictionary with timing
        enforced_words[user.id][word] = {
            "initial_time": initial_time,
            "added_time": added_time
        }

        # Update database
        c.execute(
            "INSERT OR REPLACE INTO enforced_words (user_id, word, initial_time, added_time) VALUES (?, ?, ?, ?)",
            (user.id, word, initial_time, added_time)
        )
        conn.commit()

        await ctx.message.add_reaction("🔠")
        await ctx.send(
            f"{user.mention} must now include **{word}** in all messages.\n"
            f"Timeout on violation: {initial_time + added_time} seconds.",
            delete_after=30
        )

    except Exception as e:
        print(f"Enforced error: {e}")
        await ctx.message.add_reaction("❌")


@bot.command()
async def unenforce(ctx, user: discord.Member, word: str = None):
    """Remove enforced word requirement for a user
    Usage: !>unenforce @user "required_word"""
    
    target = user or ctx.author

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # Auth check for others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if word is None or len(word.strip()) < 1:
        await ctx.message.add_reaction("❌")
        return

    word = word.strip().lower()

    try:
        # Check if enforcement exists
        if user.id not in enforced_words or word not in enforced_words[user.id]:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"{user.mention} doesn't have **{word}** enforced.", delete_after=10)
            return

        # Remove from memory
        del enforced_words[user.id][word]
        
        # Clean up empty entries
        if not enforced_words[user.id]:
            del enforced_words[user.id]

        # Remove from database
        c.execute(
            "DELETE FROM enforced_words WHERE user_id = ? AND word = ?",
            (user.id, word)
        )
        conn.commit()

        await ctx.message.add_reaction("✅")
        await ctx.send(
            f"Removed **{word}** from {user.mention}'s enforced words list.",
            delete_after=30
        )

    except Exception as e:
        print(f"Unenforce error: {e}")
        await ctx.message.add_reaction("❌")


@bot.command()
async def ban(ctx, user: discord.Member, word: str = None, initial_time: int = 60, added_time: int = 30):
    """Ban a user from using specific words
    Usage: !>ban @user "forbidden word" [initial_time] [added_time]"""

    target = user or ctx.author

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # Auth check for others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if word is None or len(word.strip()) < 1:
        await ctx.message.add_reaction("❌")
        return

    word = word.strip().lower()

    if check_word_conflict(user.id, word, "ban"):
        await ctx.send("❌ That word conflicts with an enforced word already set for this user.")
        return


    # Ensure initial_time and added_time are integers and have valid defaults
    try:
        initial_time = int(initial_time) if initial_time is not None else 60
        added_time = int(added_time) if added_time is not None else 30
    except ValueError:
        await ctx.send("❌ Initial time and added time must be numbers.")
        return

    try:
        if user.id not in banned_words:
            banned_words[user.id] = {}

        if word in banned_words[user.id]:
            await ctx.message.add_reaction("❌")
            return

        # Add to in-memory dict
        banned_words[user.id][word] = {
            "initial_time": initial_time,
            "added_time": added_time
        }

        # Add to DB
        c.execute(
            "INSERT OR REPLACE INTO banned_words (user_id, word, initial_time, added_time) VALUES (?, ?, ?, ?)",
            (user.id, word, initial_time, added_time)
        )
        conn.commit()

        await ctx.message.add_reaction("⛔")
        await ctx.send(
            f"{user.mention} is now banned from saying **{word}**.\n"
            f"Timeout on violation: {initial_time + added_time} seconds.",
            delete_after=30
        )

    except Exception as e:
        print(f"Banned error: {e}")
        await ctx.message.add_reaction("❌")

@bot.command()
async def unban(ctx, user: discord.Member, word: str = None):
    """Unban a word for a user
    Usage: !>unban @user "forbidden word"""
    
    target = user or ctx.author

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # Auth check for others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if word is None or len(word.strip()) < 1:
        await ctx.message.add_reaction("❌")
        return

    word = word.strip().lower()

    try:
        # Check if user has any banned words
        if user.id not in banned_words or word not in banned_words[user.id]:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"{user.mention} doesn't have **{word}** banned.", delete_after=10)
            return

        # Remove from memory
        del banned_words[user.id][word]
        
        # Clean up empty entries
        if not banned_words[user.id]:
            del banned_words[user.id]

        # Remove from database
        c.execute(
            "DELETE FROM banned_words WHERE user_id = ? AND word = ?",
            (user.id, word)
        )
        conn.commit()

        await ctx.message.add_reaction("✅")
        await ctx.send(
            f"Removed **{word}** from {user.mention}'s banned words list.",
            delete_after=30
        )

    except Exception as e:
        print(f"Unban error: {e}")
        await ctx.message.add_reaction("❌")

def check_word_conflict(user_id: int, new_word: str, mode: str):
    new_word = new_word.lower()

    # Check against banned words if adding to enforce
    if mode == "enforce":
        for banned in banned_words.get(user_id, {}):
            if banned in new_word or new_word in banned:
                return True

    # Check against enforced words if adding to ban
    elif mode == "ban":
        for enforced in enforced_words.get(user_id, {}):
            if enforced in new_word or new_word in enforced:
                return True

    return False


@bot.command(name="settings", aliases=["modes", "prefs"])
async def settings(ctx, *args):
    """
    Manage your enforcement actions.
    Usage:
      !>settings                - Show your current settings
      !>settings add gag        - Add an action
      !>settings remove timeout - Remove an action
      !>settings help           - Show help
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
            "`!>settings` – Show your settings\n"
            "`!>settings add gag` – Add an action\n"
            "`!>settings remove timeout` – Remove an action"
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
        conn.commit()

        await ctx.send(f"✅ Updated settings: `{updated_value or 'none'}`")
        return

    await ctx.send("❌ Invalid usage. Try `!>settings help` for instructions.")

        
@bot.command()
#@commands.has_permissions(administrator=True)
async def auth(ctx, mode: str):
    valid_modes = ["ask", "public", "exposed", "off"]
    mode = mode.lower()
    
    if mode not in valid_modes:
        await ctx.message.add_reaction("❌")
        return
    
    c.execute("INSERT OR REPLACE INTO user_auth VALUES (?, ?)", (ctx.author.id, mode))
    conn.commit()
    await ctx.message.add_reaction("✅")

@bot.command()
async def ignore(ctx, user: discord.Member = None):
    target = user or ctx.author  # If no user is mentioned, apply to the message author
    user_id = target.id

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # Auth check for ignoring others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    try:
        with sqlite3.connect('abot_data.db') as conn:
            c = conn.cursor()

            # Check if the user is already ignored
            c.execute("SELECT 1 FROM ignored_users WHERE user_id = ?", (user_id,))
            row = c.fetchone()

            if row:
                # Unignore
                c.execute("DELETE FROM ignored_users WHERE user_id = ?", (user_id,))
                conn.commit()
                ignored_users.discard(user_id)
                await ctx.send(f"🔓 {target.mention} has been removed from the ignored users list.", delete_after=30)
            else:
                # Ignore
                c.execute("INSERT INTO ignored_users (user_id) VALUES (?)", (user_id,))
                conn.commit()
                ignored_users.add(user_id)
                await ctx.send(f"✅ {target.mention} has been added to the ignored users list.", delete_after=30)

    except Exception as e:
        await ctx.send(f"❌ Failed to toggle ignore status for {target.mention}: {e}")



@bot.command()
async def red(ctx):
    """Erase all your data from the bot's database (irreversible)"""
    user_id = ctx.author.id
    
    try:
        # Delete from all database tables
        c.execute("DELETE FROM gagged_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM prison_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM user_auth WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM cooldown_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM solitary_confinement WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM enforced_words WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM enforced_offenses WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM banned_words WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM ignored_users WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        conn.commit()
        
        # Remove from memory caches
        gagged_users.discard(user_id)
        prison_users.pop(user_id, None)
        cooldown_users.pop(user_id, None)
        solitary_confinement.pop(user_id, None)
        
        # Send confirmation
        await ctx.message.add_reaction("♻️")
        try:
            await ctx.author.send("✅ All your data has been erased from the system")
        except discord.Forbidden:
            pass
            
    except Exception as e:
        print(f"Data erase error: {e}")
        await ctx.message.add_reaction("❌")


# ------------------- Modified Commands -------------------
@bot.command()
async def gag(ctx, user: discord.Member = None, gag_type: str = "loose"):
    """Apply a gag type to yourself or another user"""
    
    # If the first argument is 'help', show the help message
    if gag_type.lower() == "help":
        await gag_help(ctx)  # Call the help command to list the available gags
        return
    
    valid_gags = ["loose", "medium", "harsh", "puppy", "kitty", "toy", "base64", "zalgo", "piglatin", "ungag"]
    
    # If no user is provided, set the target to the author
    target = user or ctx.author  # If no user is mentioned, apply to the message author

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # If the target is a bot, reject the command
    if target.bot:
        await ctx.message.add_reaction("❌")
        return

    # Check if the gag type is valid
    if gag_type.lower() not in valid_gags:
        await ctx.send(f"❌ Invalid gag type. Valid types: `{', '.join(valid_gags)}`")
        return

    # Auth check for others (if the target is not the message author)
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    user_id = target.id

    # Handle the 'ungag' command
    if gag_type == "ungag":
        c.execute("DELETE FROM gagged_users WHERE user_id = ?", (user_id,))
        gagged_users.pop(user_id, None)
        await ctx.message.add_reaction("🗣️")
    else:
        # Insert/Update the gag for the user in the database
        c.execute("INSERT OR REPLACE INTO gagged_users (user_id, type) VALUES (?, ?)", (user_id, gag_type))
        gagged_users[user_id] = gag_type
        await ctx.message.add_reaction("<:emoji:1367662060862705734>")

    conn.commit()



@bot.command()
async def prison(ctx, user: discord.Member = None, action: str = None):
    """Toggle prison status or manage another user"""
    target = user or ctx.author

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SC_ROLE_ID)

    # Auth Check
    if not await check_auth(ctx, target) and target != ctx.author:
        await ctx.message.add_reaction("❌")
        return

    # Determine action for self
    if user is None:
        action = "remove" if target.id in prison_users else "add"

    try:
        if action.lower() == "add":
            if prisoner_role not in target.roles:
                await target.add_roles(prisoner_role, reason="Entered prison")
            prison_users[target.id] = ctx.channel.id
            balance = get_balance(user.id)
            c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)", 
                     (target.id, ctx.channel.id, balance))
            reaction = "🔒"
        elif action.lower() == "remove":
            del prison_users[target.id]
            c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
            await target.remove_roles(prisoner_role, reason="Released from prison")
            reaction = "🔓"
        else:
            await ctx.message.add_reaction("❌")
            return
        
        conn.commit()
        await ctx.message.add_reaction(reaction)
    except:
        await ctx.message.add_reaction("❌")


@bot.command()
async def ungag(ctx, user: discord.Member = None):
    """Remove a user's gag status"""
    target = user or ctx.author

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return

    # Auth Check
    if not await check_auth(ctx, target) and target != ctx.author:
        await ctx.message.add_reaction("❌")
        return

    user_id = target.id

    if user_id in gagged_users:
        gagged_users.pop(user_id, None)
        c.execute("DELETE FROM gagged_users WHERE user_id = ?", (user_id,))
        conn.commit()
        await ctx.message.add_reaction("🗣️")
    else:
        await ctx.message.add_reaction("❌")



@bot.command()
async def cooldown(ctx, user: discord.Member = None, seconds: int = None):
    """Toggle cooldown or set for another user"""
    target = user or ctx.author

    # Auth Check
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return
    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return
    if seconds > 9223372036854775808:
        await ctx.message.add_reaction("❌")
        return

    # Toggle logic for self
    if user is None:
        seconds = 0 if target.id in cooldown_users else 30

    if seconds < 0:
        await ctx.message.add_reaction("❌")
        return

    if seconds == 0:
        if target.id in cooldown_users:
            del cooldown_users[target.id]
            c.execute("DELETE FROM cooldown_users WHERE user_id = ?", (target.id,))
    else:
        cooldown_users[target.id] = seconds
        last_message_times[target.id] = 0
        c.execute("INSERT OR REPLACE INTO cooldown_users VALUES (?, ?)", 
                 (target.id, seconds))
    
    conn.commit()
    await ctx.message.add_reaction("⏱️")


@bot.command(name="solitary")
# @commands.has_permissions(manage_roles=True, manage_channels=True)
async def solitary(ctx, user: discord.Member = None, action: str = None):
    """Toggle solitary confinement or manage another user
    Usage: !>solitary [@user] [add/remove]"""

    # Get required objects
    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SC_ROLE_ID)

    # Validate configuration
    if not all([prison_channel, solitary_role, prisoner_role, sc_role]):
        await ctx.message.add_reaction("❌")
        return

    # Determine target and action
    target = user or ctx.author
    if user is None:
        action = "remove" if target.id in solitary_confinement else "add"
    else:
        action = action.lower() if action else "add"

    if target.id in locked_users and ctx.author.id not in AUTHORIZED_LOCK_MANAGERS:
        await ctx.send("❌ The Target is currently locked and cannot use this command.")
        return


    # Authorization check for modifying others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return
    
    try:
        if action == "add":

            if prisoner_role not in target.roles:
                await target.add_roles(prisoner_role, reason="Entered solitary")
            if sc_role not in target.roles:
                await target.add_roles(sc_role, reason="Entered solitary")
            # Check for existing thread in the DB
            c.execute("SELECT thread_id FROM solitary_confinement WHERE user_id = ?", (target.id,))
            result = c.fetchone()

            existing_thread = None
            if result:
                try:
                    existing_thread = await ctx.guild.fetch_channel(result[0])
                except discord.NotFound:
                    existing_thread = None  # Thread was deleted or can't be accessed

            if existing_thread:
                if existing_thread.archived:
                    await existing_thread.edit(archived=False)
                if existing_thread.locked:
                    await existing_thread.edit(locked=False)
                solitary_confinement[target.id] = existing_thread.id
                prison_users[target.id] = existing_thread.id
                c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)", 
                     (target.id, existing_thread.id, get_balance(target.id)))
                conn.commit()
                await existing_thread.send(f"{target.mention} has re-entered solitary confinement.")
            else:
                # Create new thread
                new_thread = await prison_channel.create_thread(
                    name=f"solitary-{target.name}",
                    type=discord.ChannelType.public_thread,
                    reason=f"Solitary confinement for {target.display_name}"
                )

                # Save to DB
                c.execute("INSERT OR REPLACE INTO solitary_confinement (user_id, thread_id, archive_date) VALUES (?, ?, ?)",
                        (target.id, new_thread.id, None))
                c.execute("INSERT OR REPLACE INTO prison_users VALUES (?, ?, ?)", 
                     (target.id, new_thread.id, get_balance(target.id)))
                conn.commit()

                solitary_confinement[target.id] = new_thread.id
                prison_users[target.id] = new_thread.id
                await new_thread.send(f"{target.mention} has entered solitary confinement.")

            await ctx.message.add_reaction("🔒")

        elif action == "remove":
            if target.id not in solitary_confinement:
                await ctx.message.add_reaction("❌")
                return

            thread_id = solitary_confinement[target.id]
            thread = await ctx.guild.fetch_channel(thread_id)

            if thread:
                await thread.send(f"{target.name} has been released from solitary.")
                await thread.send(f"It's mightly lonely in here.")
                await thread.send(f"Don't chat here, the silly prisoner won't see it!")
                await thread.edit(archived=True, locked=True, reason="Released from solitary confinement.")  # Lock and close the thread
                c.execute("UPDATE solitary_confinement SET archive_date = ? WHERE user_id = ?", (datetime.now(), target.id))
                c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
                conn.commit()
            else:
                await ctx.message.add_reaction("❌")

            del solitary_confinement[target.id]
            del prison_users[target.id]
            await target.remove_roles(prisoner_role, sc_role, reason="Released from solitary")
            await ctx.message.add_reaction("🔓")

        else:
            await ctx.message.add_reaction("❌")

    except discord.Forbidden:
        await ctx.send("❌ Missing required permissions!")
        await ctx.message.add_reaction("❌")
    except discord.HTTPException as e:
        print(f"Solitary command error: {e}")
        await ctx.message.add_reaction("❌")



@bot.command(aliases=["lines"])
async def assign_lines(ctx, user: discord.Member = None, lines: str = None, *, args: str = None):
    channel_id = ctx.channel.id
    target = user or ctx.author

    # Use the global LINE_WRITING_ID variable for the designated channel
    global LINE_WRITING_ID

    # Progress check if used in line-writing channel
    if channel_id == LINE_WRITING_ID and lines is None and args is None:
        c.execute("SELECT line, lines_required FROM active_line_writers WHERE user_id = ?", (target.id,))
        result = c.fetchone()

        if result:
            line_text, lines_left = result
            await ctx.send(f"📝 {ctx.author.mention}, {target.mention} has **{lines_left}** lines left.\n"
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
            await ctx.send("❌ No such assignment found.")
            await ctx.message.add_reaction("❌")
            return

        assigned_by = result[0]

        # Only allow if author is assigner or has manage_messages permission
        if ctx.author.id != assigned_by and not ctx.author.guild_permissions.manage_messages and not ctx.author.id != 1146469921270792326:
            await ctx.message.add_reaction("❌")
            await ctx.send("❌ You can only clear lines you assigned, unless you have Manage Messages permission.")
            return

        c.execute("DELETE FROM line_assignments WHERE assignment_id = ?", (assignment_id,))
        c.execute("DELETE FROM active_line_writers WHERE user_id = ?", (target.id,))
        conn.commit()
        line_writing_sessions.pop(target.id, None)
        await ctx.send(f"✅ Cleared line assignment with ID {assignment_id} for {target.mention}.")
        return

    # Validate base inputs for assignment creation
    if not user or lines is None or not args:
        await ctx.message.add_reaction("❌")
        await ctx.send("❌ Usage: `!>lines @user #lines \"line text\" [penalty]`")
        return

    # Auth check for others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    try:
        # Split the `args` to get line text and penalty
        parts = args.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            raw_text = parts[0].strip()
            penalty = int(parts[1])
        else:
            raw_text = args.strip()
            penalty = 0  # Default to 0 penalty if not provided

        text = ''.join(c for c in raw_text if c in string.printable)

        if text != raw_text:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"❌ Don't use non-printable characters in the line text.")
            await ctx.send(f"clean text: {text}")
            return
        # Insert line assignment into the database
        c.execute("""
            INSERT OR REPLACE INTO line_assignments (user_id, line, lines_required, penalty_lines, assigned_by)
            VALUES (?, ?, ?, ?, ?)
        """, (user.id, text, lines, penalty, ctx.author.id))
        conn.commit()

        # Fetch the newly created assignment_id
        assignment_id = c.lastrowid

        # Respond with a success reaction
        await ctx.message.add_reaction("✅")

        # Fetch the writing channel using the global LINE_WRITING_ID
        writing_channel = ctx.guild.get_channel(LINE_WRITING_ID)

        # Check if the writing_channel is valid
        if not writing_channel:
            await ctx.message.add_reaction("❌")
            await ctx.send(f"❌ The designated line writing channel does not exist or is not accessible.")
            return

        # Send a message to the designated writing channel
        await writing_channel.send(
            f"✍️ {user.mention} has been assigned to write: **{text}**\n"
            f"**{lines} times**. Penalty for mistakes: **+{penalty}** lines.\n"
            f"Use '!>start {assignment_id}' to begin! Remember don't speak anywhere else until you are done!"
        )

    except Exception as e:
        await ctx.message.add_reaction("❌")
        await ctx.send(f"❌ Failed to assign lines: {e}")



@bot.command()
async def start(ctx, assignment_id: int):
    user = ctx.author

    # Check if the command is being used in the correct channel
    if ctx.channel.id != LINE_WRITING_ID:
        await ctx.send(f"{user.mention}, this command can only be used in the designated line writing channel.")
        return

    # Check if already writing a line
    c.execute("SELECT * FROM active_line_writers WHERE user_id = ?", (user.id,))
    if c.fetchone():
        await ctx.send(f"{user.mention}, you are already writing an assignment! Finish it before starting another.")
        return

    # Get the specific assignment by assignment_id
    c.execute("SELECT line, lines_required, penalty_lines FROM line_assignments WHERE assignment_id = ? AND user_id = ?", (assignment_id, user.id))
    result = c.fetchone()

    if not result:
        await ctx.send(f"{user.mention}, no active assignment found for assignment ID {assignment_id}.")
        return

    line_text, lines_required, penalty_lines = result

    print(result)
    print(f"Inserting into active_line_writers: user_id={user.id}, line_text={line_text}, lines_required={lines_required}, lines_written=0")
    # Start line writing session
    c.execute("INSERT OR IGNORE INTO active_line_writers (user_id, line, lines_required, lines_written, penalty_lines) VALUES (?, ?, ?, ?, ?) ",
            (user.id, line_text, lines_required, 0, penalty_lines))  # Added penalty_lines here
    conn.commit()

    line_writing_sessions[user.id] = {
        "line": line_text,
        "lines_required": lines_required,
        "lines_written": 0
    }

    await ctx.send(f"📝 {user.mention}, your line writing has started!\n"
                   f"Write this line **{lines_required} times**:\n> `{line_text}`")




@bot.command(name="my_assignments")
async def my_assignments(ctx):
    author_id = ctx.author.id

    # Get line assignments created by the user
    c.execute("""
        SELECT la.assignment_id, la.user_id, la.line, la.lines_required, alw.user_id IS NOT NULL AS has_started
        FROM line_assignments la
        LEFT JOIN active_line_writers alw ON la.user_id = alw.user_id
        WHERE la.assigned_by = ?
    """, (author_id,))

    rows = c.fetchall()

    if not rows:
        await ctx.send("✅ You haven't assigned any active lines.")
        return

    embed = discord.Embed(
        title="📝 Your Assigned Lines",
        description="Here are the users you’ve assigned lines to:",
        color=0xffcc00
    )

    for assignment_id, user_id, line_text, lines_left, has_started in rows:
        user = ctx.guild.get_member(user_id)
        status = "✅ Completed" if lines_left <= 0 else "⌛ Pending"
        name = user.display_name if user else f"<Unknown User {user_id}>"
        embed.add_field(
            name=f"Assignment ID: {assignment_id} — {name} — {status}",
            value=f"Lines left: **{lines_left}**\nLine: `{line_text}`",
            inline=False
        )

    await ctx.send(embed=embed)



# ------------------- Bypass User Command -------------------
@bot.command(name="allow", aliases=["bypass"])
async def allow(ctx, user: discord.Member):
    # Check mod role or specific allowed user ID
    if not ctx.author.guild_permissions.manage_messages and ctx.author.id != 1146469921270792326:
        await ctx.send("You do not have the required permissions to cancel a bet.")
        return

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

    conn.commit()




# ------------------- Gambling -------------------

# Global variable for card counting and deck simulation
# Players list and other necessary variables
players = []  # List of players currently in the game
queue = []  # Queue for players waiting to join the next round
game_in_progress = False
running_count = 0
current_bets = {}  # Store bets for each player

SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
VALUES = {str(i): i for i in range(2, 11)}
VALUES.update({'J': 10, 'Q': 10, 'K': 10, 'A': 11})

# Global variable to track the remaining cards in the deck
total_deck_size = 8 * 52  # 8 decks, each with 52 cards
remaining_cards = total_deck_size  # Start with the full deck
deck = []  # Initialize deck as an empty list


@bot.command(name="leaderboard")
async def leaderboard(ctx):
    # Get the top 10 users with the highest balance
    c.execute("SELECT user_id, balance FROM user_wallets ORDER BY balance DESC LIMIT 10")
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
async def bet(ctx, opponent: discord.Member, amount: int, game: str):
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
        conn.commit()

        # Deduct the pet bucks from the initiator
        new_balance = user_balance - amount
        c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_balance, user_id))
        conn.commit()

        # Deduct pet bucks from the opponent
        new_opponent_balance = opponent_balance - amount
        c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_opponent_balance, opponent.id))
        conn.commit()

        await ctx.send(f"Bet placed! {ctx.author.display_name} has wagered {amount} pet bucks against {opponent.display_name} on the game '{game}'.")
    else:
        await ctx.send(f"{opponent.display_name} has declined the bet.")

@bot.command(name='bet_result')
async def bet_result(ctx, game: str, winner: discord.Member):
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
        conn.commit()

        # Credit the winner
        c.execute('SELECT balance FROM user_wallets WHERE user_id = ?', (winner.id,))
        winner_balance = c.fetchone()[0]
        new_winner_balance = winner_balance + (2 * amount)
        c.execute('UPDATE user_wallets SET balance = ? WHERE user_id = ?', (new_winner_balance, winner.id))
        conn.commit()

        await ctx.send(f"✅ Bet settled! {winner.mention} vs {loser.mention}. {winner.mention} wins {amount} pet bucks!")

    except asyncio.TimeoutError:
        await ctx.send("⌛ Confirmation timeout. Bet result not recorded.")


@bot.command(name='cancel_bet')
async def cancel_bet(ctx, game: str):
    """Cancel a pending bet and refund both players their bet amount."""
    
    # Check if the user is an admin (can be adjusted based on your roles)
    if not ctx.author.guild_permissions.manage_messages and ctx.author.id != 1146469921270792326:
        await ctx.send("You do not have the required permissions to cancel a bet.")
        return
    
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
    conn.commit()
    
    # Notify users about the canceled bet and the refund
    await ctx.send(f"Bet on game '{game}' has been canceled. Both players have been refunded {amount} pet bucks each.")



@bot.command(name='give')
async def give(ctx, recipient: discord.Member, amount: int):
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
        conn.commit()

    # Update balances
    c.execute("UPDATE user_wallets SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
    c.execute("UPDATE user_wallets SET balance = balance + ? WHERE user_id = ?", (amount, recipient_id))
    conn.commit()

    await ctx.send(f"💸 {ctx.author.mention} gave {amount} pet bucks to {recipient.mention}!")





@bot.command(aliases=["wallet", "coins", "petbucks", "bucks"])
async def balance(ctx, user: discord.Member = None):
    target = user or ctx.author
    user_id = target.id

    balance = get_balance(user_id)

    await ctx.send(f"💰 {target.display_name}'s balance: **{balance} pet bucks**")


@bot.command(aliases=["streak", "daily_claim", "claim"])
async def daily(ctx):
    user_id = ctx.author.id
    now = datetime.now(timezone.utc)

    # Check last claim time
    c.execute("SELECT last_claim, claim_count, streak_days FROM daily_claims WHERE user_id = ?", (user_id,))
    row = c.fetchone()

    if row:
        last_claim = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        claim_count = row[1]
        streak_days = row[2]
        
        # Check if the streak is within 24-48 hours since the last claim
        if now - last_claim < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last_claim)
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes = remainder // 60
            await ctx.send(f"⏳ You’ve already claimed your daily reward. Try again in {remaining.days}d {hours}h {minutes}m.")
            return

        # If more than 24 hours have passed, check streak
        if now - last_claim > timedelta(hours=48):
            streak_days = 0  # Reset streak if more than 48 hours have passed
        
        # Increment streak if within streak limit
        streak_days += 1
    else:
        # If no record exists, it's the user's first claim
        claim_count = 0
        streak_days = 1

    # Calculate the reward based on the streak
    reward = 50 + (streak_days - 1) * 5

    # Update balance and claim time
    update_balance(user_id, reward)
    c.execute("INSERT OR REPLACE INTO daily_claims (user_id, last_claim, claim_count, streak_days) VALUES (?, ?, ?, ?)", 
              (user_id, now.isoformat(), claim_count + 1, streak_days))
    conn.commit()

    # Send the message with total claims and streak
    await ctx.send(f"✅ {ctx.author.mention}, you've claimed your daily **{reward} pet bucks**, on a streak of **{streak_days} days**! You've claimed **{claim_count + 1}** times in total.")


def get_balance(user_id):
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row is None:
        # Insert with default 1000 if user not in table yet
        c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (user_id, 1000))
        conn.commit()
        return 1000
    return row[0]


@bot.command(name='add')
@commands.check(lambda ctx: ctx.author.id == 1146469921270792326 or ctx.author.guild_permissions.administrator)
async def add_coins(ctx, user: discord.Member, amount: int):
    # Ensure the amount is valid (positive)
    if amount <= -10000000000000000000000000000000000000:
        await ctx.send("❌ You must specify a positive amount of pet bucks to add.")
        return

    # Call the existing update_balance function to add pet bucks to the user
    update_balance(user.id, amount)

    # Send a confirmation message
    await ctx.send(f"✅ {amount} pet bucks have been added to {user.mention}'s balance!")

def update_balance(user_id, amount):
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    row = c.fetchone()

    if row is None:
        # Not found, start with default balance + amount
        new_balance = max(0, 1000 + amount)
        c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, ?)", (user_id, new_balance))
    else:
        new_balance = max(0, row[0] + amount)  # Prevent negative balance
        c.execute("UPDATE user_wallets SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    conn.commit()


def has_sufficient_funds(user_id, amount):
    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    return row and row[0] >= amount


@bot.command()
async def flip(ctx, amount: int = None):
    user_id = ctx.author.id

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send("❌ This command can only be used in the designated gambling channel.")
        return

    # Ensure the user has sufficient funds for gambling
    if amount is not None:
        if amount < 0:
            await ctx.send("❌ You must specify a positive amount to gamble.")
            return

        if not has_sufficient_funds(user_id, amount):
            await ctx.send(f"❌ {ctx.author.mention}, you don't have enough pet bucks to gamble that amount.")
            return

    result = random.choice(["🪙 Heads", "🪙 Tails"])

    # Random win/loss logic for gambling
    if amount:
        if random.choice([True, False]):
            # Win: Double the bet
            update_balance(user_id, amount)
            await ctx.send(f"{ctx.author.mention} flips a coin... {result}! You win! {amount} pet bucks have been added to your balance!")
        else:
            # Lose: Deduct the bet
            update_balance(user_id, -amount)
            await ctx.send(f"{ctx.author.mention} flips a coin... {result}! You lost! {amount} pet bucks have been deducted from your balance.")
    else:
        # Normal flip without gambling
        await ctx.send(f"{ctx.author.mention} flips a coin... {result}!")



@bot.command(aliases=["slot", "sluts"])
async def slots(ctx, bet: int = 0):
    user_id = ctx.author.id
    balance = get_balance(user_id)

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send("❌ This command can only be used in the designated gambling channel.")
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
        update_balance(user_id, winnings)
        slot_embed.add_field(
            name="Congratulations!",
            value=f"**You win!** {winnings} pet bucks have been added to your balance!",
            inline=False
        )
    else:
        update_balance(user_id, -bet)
        slot_embed.add_field(
            name="Better Luck Next Time!",
            value=f"**You lose!** {bet} pet bucks have been deducted from your balance.",
            inline=False
        )

    # Send the embed with the result
    await ctx.send(embed=slot_embed)


@bot.command(aliases=["wheel", "spin"])
async def roulette(ctx, bet_type: str, bet_value: str, bet_amount: int):
    user_id = ctx.author.id
    balance = get_balance(user_id)

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send("❌ This command can only be used in the designated gambling channel.")
        return

    # Validate bet amount
    if bet_amount < 0:
        await ctx.send("❌ You must bet a positive amount.")
        return

    if bet_amount > balance:
        await ctx.send(f"❌ {ctx.author.mention}, you don't have enough pet bucks to make that bet.")
        return

    # Validate bet type and value
    if bet_type not in ['number', 'color']:
        await ctx.send("❌ Invalid bet type. Please choose either 'number' or 'color'.")
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
    print(str(bet_value), str(spin_result), spin_color)
    if bet_type == 'number' and str(bet_value) == str(spin_result):
        win = True
        winnings = bet_amount * 35  # 35:1 payout for numbers
    elif bet_type == 'color' and bet_value.lower() == spin_color:
        win = True
        winnings = bet_amount * 2  # 2:1 payout for colors
    else:
        winnings = -bet_amount  # Loss

    # Update balance
    update_balance(user_id, winnings)

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
async def join(ctx):
    global players, queue, game_in_progress

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send("❌ This command can only be used in the designated gambling channel.")
        return

    if game_in_progress:
        if ctx.author not in queue and ctx.author not in players:
            queue.append(ctx.author)
            await ctx.send(f"{ctx.author.mention} has been added to the queue for the next game.")
        else:
            await ctx.send(f"{ctx.author.mention}, you're already in the game or in the queue.")
        return

    if ctx.author not in players:
        players.append(ctx.author)
        await ctx.send(f"{ctx.author.mention} has joined the Blackjack game!")
    else:
        await ctx.send(f"{ctx.author.mention}, you're already in the current game.")

# Player Leave Command
@bot.command()
async def leave(ctx):
    global players, queue, game_in_progress

    if ctx.author in players:
        players.remove(ctx.author)
        await ctx.send(f"{ctx.author.mention} has left the Blackjack game.")
    elif ctx.author in queue:
        queue.remove(ctx.author)
        await ctx.send(f"{ctx.author.mention} has left the queue.")
    else:
        await ctx.send(f"{ctx.author.mention}, you're not currently in the game or in the queue.")


# The blackjack command
@bot.command()
async def blackjack(ctx):
    global players, queue, game_in_progress, current_bets

    if ctx.channel.id != GAMBLING_ID:
        await ctx.send("❌ This command can only be used in the designated gambling channel.")
        return

    if game_in_progress:
        await ctx.send(f"❌ {ctx.author.mention}, a game is already in progress. Please wait for the next round.")
        return

    if len(players) < 1:
        await ctx.send("❌ No players have joined. Use `!>join` to sit at the table!")
        return

    game_in_progress = True
    current_bets = {}

    # Betting phase
    for player in players:
        bal = get_balance(player.id)  # Ensure player has a wallet
        await ctx.send(f"{player.mention}, please type your bet (30 seconds). You have **{bal} pet bucks** available:")
        
        def check(m):
            return m.author == player and m.content.isdigit()
        
        try:
            bet_msg = await bot.wait_for('message', timeout=30.0, check=check)
            bet = int(bet_msg.content)
            
            if not has_sufficient_funds(player.id, bet):
                await ctx.send(f"❌ {player.mention}, insufficient funds! Standing automatically.")
                current_bets[player] = 0
            else:
                current_bets[player] = bet
                await ctx.send(f"✅ {player.mention} bet {bet} pet bucks.")
        except asyncio.TimeoutError:
            await ctx.send(f"⏰ {player.mention} took too long! Standing automatically.")
            current_bets[player] = 0

    # Game setup
    deck = shuffle_deck()
    player_hands = {player: [deck.pop(), deck.pop()] for player in players}
    dealer_hand = [deck.pop(), deck.pop()]

    # Update running count
    for hand in player_hands.values():
        for card in hand:
            update_running_count(card)
    for card in dealer_hand:
        update_running_count(card)

    # Initial hands embed
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
    await ctx.send(embed=initial_embed)

    # Action phase
    current_players = [p for p in players if calculate_hand(player_hands[p]) < 21]
    round_number = 1

    while current_players:
        # Send reaction message
        action_embed = discord.Embed(
            title=f"🔄 Round {round_number} - Hit or Stand",
            description=f"Players: {', '.join([p.mention for p in current_players])}\n"
                      "React with 🃏 to HIT or 🚫 to STAND\n"
                      "You have 30 seconds!",
            color=0xf1c40f
        )
        action_msg = await ctx.send(embed=action_embed)
        await action_msg.add_reaction("🃏")
        await action_msg.add_reaction("🚫")

        # Collect reactions
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

        # Process actions
        hitters = []
        for player in current_players:
            if player.id not in reactions:
                await ctx.send(f"⏰ {player.mention} didn't react! Standing automatically.")
                continue
                
            if reactions[player.id] == "🃏":
                hitters.append(player)


        # Process hits
        updated_players = []
        if hitters:
            hit_embed = discord.Embed(title="📤 Players Drawing Cards", color=0xe74c3c)
            
            for hitter in hitters:
                new_card = deck.pop()
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
            await ctx.send(embed=hit_embed)

        # Prepare next round
        current_players = updated_players
        round_number += 1

    # Dealer's turn
    dealer_total = calculate_hand(dealer_hand)
    dealer_cards = [f"{r}{s}" for r, s in dealer_hand]

    # Evaluate if the dealer's hand is better than the highest player hand
    highest_player_total = max([calculate_hand(player_hands[p]) for p in players])
    
    while True:
        if dealer_total >= 17 or dealer_total > highest_player_total:
            # Stop drawing if either condition is met
            break

        # Draw a new card and update dealer's total
        new_card = deck.pop()
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

    for player in players:
        hand = player_hands[player]
        total = calculate_hand(hand)
        bet = current_bets[player]
        result = ""
        
        if total > 21:
            result = f"❌ BUST! Lost {bet} pet bucks"
            update_balance(player.id, -bet)
        elif dealer_total > 21 or total > dealer_total:
            result = f"✅ WIN! Gained {bet} pet bucks"
            update_balance(player.id, bet)
        elif total == dealer_total:
            result = "🔶 PUSH! Bet returned"
        else:
            result = f"❌ LOSE! Lost {bet} pet bucks"
            update_balance(player.id, -bet)

        result_embed.add_field(
            name=f"{player.display_name}'s Hand",
            value=f"{format_hand(hand)} (Total: {total})\n{result}",
            inline=False
        )

    await ctx.send(embed=result_embed)

    # Cleanup
    game_in_progress = False
    current_bets.clear()
    if queue:
        await ctx.send(f"🎮 Next game starting with: {', '.join([p.mention for p in queue])}")
        players.extend(queue)
        queue.clear()


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
    card = deck.pop()  # Draw the top card from the deck
    remaining_cards -= 1  # Decrease the number of remaining cards
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

# ------------------- Gambling Help -------------------
@bot.command(aliases=["gamble", "games"])
async def play(ctx):
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



# ------------------- Voice Channel Commands -------------------

@bot.command()
async def vc(ctx):
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
async def click(ctx):
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
@commands.has_permissions(administrator=True)
async def ping(ctx):
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
@commands.has_permissions(administrator=True)
async def windows(ctx):
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
@commands.has_permissions(administrator=True)
async def iphone(ctx):
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
#@commands.has_permissions(administrator=True)
async def speak(ctx, *, message: str):
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
@commands.has_permissions(administrator=True)
async def shrek(ctx):
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
@commands.has_permissions(administrator=True)
async def mute(ctx, user: discord.Member):
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
@commands.has_permissions(administrator=True)
async def deafen(ctx, user: discord.Member):
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
async def freedom(ctx):
    user = ctx.author
    user_id = user.id

    if user_id not in solitary_confinement:
        await ctx.send("❌ You're not in solitary confinement.")
        return

    c.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
    result = c.fetchone()

    if not result or result[0] < 2000:
        await ctx.send("❌ You need 2000 pet bucks to buy your freedom.")
        return

    try:
        c.execute("UPDATE user_wallets SET balance = balance - 2000 WHERE user_id = ?", (user_id,))
        conn.commit()
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
    prison_channel = ctx.guild.get_channel(PRISON_CHANNEL_ID)
    solitary_role = ctx.guild.get_role(SOLITARY_ROLE_ID)
    prisoner_role = ctx.guild.get_role(PRISONER_ROLE_ID)
    sc_role = ctx.guild.get_role(SC_ROLE_ID)

    # Validate essential roles/channels
    if not all([prison_channel, solitary_role, prisoner_role, sc_role]):
        await ctx.send("❌ Missing configuration for solitary system.")
        return False

    if target.id not in solitary_confinement:
        await ctx.send("❌ You're not currently in solitary.")
        return False

    try:
        thread_id = solitary_confinement[target.id]
        thread = await ctx.guild.fetch_channel(thread_id)

        if thread:
            await thread.send(f"{target.name} has been released from solitary.")
            await thread.send("It's mighty lonely in here.")
            await thread.send("Don't chat here, the silly prisoner won't see it!")
            await thread.edit(archived=True, locked=True, reason="Released from solitary confinement.")
            c.execute("UPDATE solitary_confinement SET archive_date = ? WHERE user_id = ?", (datetime.now(), target.id))
            c.execute("DELETE FROM prison_users WHERE user_id = ?", (target.id,))
            conn.commit()
        else:
            await ctx.send("❌ Could not fetch the solitary thread.")
            return False

        del solitary_confinement[target.id]
        del prison_users[target.id]

        await target.remove_roles(prisoner_role, sc_role, reason="Released from solitary")
        return True

    except discord.Forbidden:
        await ctx.send("❌ Missing permissions to modify roles or threads.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Error during solitary release: {e}")

    return False


@bot.command()
async def whois(ctx, user_id: int):
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
        
@bot.command()
async def status(ctx, user: discord.Member = None):
    target = user or ctx.author
    embed = discord.Embed(title=f"Status for {target.display_name}", color=0x00ff00)

    # Gag status
    gag_status = "🔇 Gagged" if target.id in gagged_users else "🟢 Not gagged"
    embed.add_field(name="Gag Status", value=gag_status, inline=False)

    # Prison status
    prison_status = "🟢 Not in prison"
    if target.id in prison_users:
        try:
            channel = await ctx.guild.fetch_channel(prison_users[target.id])
            prison_status = f"🔒 In prison ({channel.mention})"
        except:
            prison_status = "🔒 In prison (unknown channel)"
    embed.add_field(name="Prison Status", value=prison_status, inline=False)

    # Solitary status
    solitary_status = "🔒 Owns a Cell" if target.id in solitary_confinement else "🟢 Not in solitary"
    embed.add_field(name="Solitary Status", value=solitary_status, inline=False)

    # Cooldown status
    cooldown = cooldown_users.get(target.id, 0)
    embed.add_field(name="Cooldown", value=f"{cooldown}s" if cooldown else "None", inline=False)

    # Auth status
    c.execute("SELECT auth_level FROM user_auth WHERE user_id = ?", (target.id,))
    result = c.fetchone()
    auth_level = result[0] if result else DEFAULT_AUTH_MODE
    embed.add_field(name="Auth Mode", value=auth_level.capitalize(), inline=False)

    # Enforcement settings
    c.execute("SELECT enforcement_action FROM user_settings WHERE user_id = ?", (target.id,))
    row = c.fetchone()
    if row and row[0]:
        enforcement_action = row[0].lower()
        auto_tag = ""
    else:
        enforcement_action = "timeout"
        auto_tag = " [auto]"

    embed.add_field(
        name="Enforcement Action",
        value=f"{enforcement_action.capitalize()}{auto_tag}",
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command()
async def enforce_list(ctx, user: discord.Member = None):
    """List the enforced words for a user.
    Usage: !>enforced_list @user (or no @user for author)
    """
    # Default to ctx.author if no user is mentioned
    target = user or ctx.author

    # Check if the user has any enforced words
    if target.id not in enforced_words or not enforced_words[target.id]:
        await ctx.send(f"{target.mention} has no enforced words.")
        return

    # Retrieve and format the enforced words for the user
    enforced_list = enforced_words[target.id]
    formatted_words = [f"**{word}** - Initial Time: {data['initial_time']}s, Added Time: {data['added_time']}s"
                       for word, data in enforced_list.items()]

    # If the list is not empty, send the formatted message
    enforced_words_message = "\n".join(formatted_words)
    await ctx.send(f"Enforced words for {target.mention}:\n{enforced_words_message}")

@bot.command()
async def ban_list(ctx, user: discord.Member = None):
    """List the enforced words for a user.
    Usage: !>ban_list @user (or no @user for author)
    """
    # Default to ctx.author if no user is mentioned
    target = user or ctx.author

    # Check if the user has any enforced words
    if target.id not in banned_words or not banned_words[target.id]:
        await ctx.send(f"{target.mention} has no banned words.")
        return

    # Retrieve and format the banned words for the user
    banned_list = banned_words[target.id]
    formatted_words = [f"**{word}** - Initial Time: {data['initial_time']}s, Added Time: {data['added_time']}s"
                       for word, data in banned_list.items()]

    # If the list is not empty, send the formatted message
    banned_words_message = "\n".join(formatted_words)
    await ctx.send(f"Banned words for {target.mention}:\n{banned_words_message}")



@bot.command()
@commands.has_permissions(administrator=True)
async def resetdb(ctx):
    """Reset all database records (admin only)"""
    c.execute("DELETE FROM gagged_users")
    c.execute("DELETE FROM prison_users")
    c.execute("DELETE FROM user_auth")
    c.execute("DELETE FROM cooldown_users")
    conn.commit()
    
    gagged_users.clear()
    prison_users.clear()
    cooldown_users.clear()
    gagged_messages.clear()
    
    await ctx.message.add_reaction("♻️")


@bot.check
async def ignore_commands_targeting_bot(ctx):
    # Skip if the message mentions the bot explicitly
    if ctx.guild and ctx.bot.user in ctx.message.mentions:
        await ctx.send("🚫 You can't target me with commands.")
        return False
    return True



@bot.command(name="untimeout")
async def untimeout(ctx, user: discord.Member):
    """Un-timeout a user by timing them out for 1 second or apply timeout for missing required words."""
    
    # Check if the user has permission (moderators or user with ID 1146469921270792326)
    if not any(role.name.lower() == 'moderator' for role in ctx.author.roles) and ctx.author.id != 1146469921270792326:
        await ctx.send("❌ You do not have permission to use this command.")
        return

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
async def timeout(ctx, user: discord.Member):
    """timeout a user by timing them out for 1 second or apply timeout for missing required words."""
    
    # Check if the user has permission (moderators or user with ID 1146469921270792326)
    if not any(role.name.lower() == 'moderator' for role in ctx.author.roles) and ctx.author.id != 1146469921270792326:
        await ctx.send("❌ You do not have permission to use this command.")
        return

    try:
        # Apply 1-second timeout to simulate 'untimeout'
        timeout_until = datetime.now(timezone.utc) + timedelta(seconds=31557600)  # Maximum timeout duration
        
        await user.timeout(timeout_until, reason="Temporary timeout for untimeout command.")

        # Now clear the timeout (this is technically an 'untimeout')
        await user.timeout(None, reason="Clearing timeout.")
        await ctx.send(f"✅ {user.mention}'s timeout has been cleared.")

    except discord.Forbidden:
        await ctx.send("❌ I do not have permission to timeout this user.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ An error occurred: {e}")


# ------------------- Word Setup ----------------
def get_user_enforcement_action(user_id):
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
async def close(ctx):
    # Check if the user has permission to manage messages or if the user has the specific ID
    if ctx.author.guild_permissions.manage_messages or ctx.author.id == 1146469921270792326:
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
async def timer(ctx, *, timer_name: str):
    user_id = ctx.author.id
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Insert or replace the timer
    c.execute('''
        INSERT OR REPLACE INTO timer_logs (user_id, timer_name, start_time)
        VALUES (?, ?, ?)
    ''', (user_id, timer_name, start_time))
    conn.commit()

    await ctx.send(f"⏰ Timer '{timer_name}' started for {ctx.author.mention} at {start_time}.")

@bot.command(name='timers', aliases=['view_timers'])
async def view_timers(ctx):
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
async def stop_timer(ctx, *, timer_name: str):
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
    conn.commit()

    # Format time
    minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    await ctx.send(f"🛑 Timer '{timer_name}' stopped. Total time: **{hours}h {minutes}m {seconds}s**.")


# ------------------- Pishock COMMAND -------------------
@bot.command(name='pishock')
async def pishock(ctx):
    """Set your PiShock friend share code: !>pishock <code>"""
    if not ctx.message.content.strip().startswith('!>pishock'):
        return

    # Extract the code from the message
    parts = ctx.message.content.split()
    if len(parts) != 2:
        return await ctx.send("❌ Usage: !>pishock <code>")

    code = parts[1].strip()


    # Store the user's PiShock code
    user_pishock_codes[ctx.author.id] = code
    c.execute("INSERT OR REPLACE INTO pishock_users (user_id, code) VALUES (?, ?)", (ctx.author.id, code))
    conn.commit()
    await ctx.send(f"✅ Your PiShock friend share code has been set.")



def send_pishock_command(user_id: int, op: int, intensity: int, duration: int = 3):
    """Send command to PiShock API with user-specific friend share code"""
    if user_id not in user_pishock_codes:
        return {"Success": False, "Message": "User does not have a PiShock friend share code."}


    share_code = user_pishock_codes[user_id]

    try:
        # Make the request to the PiShock API
        response = requests.post(
            "https://do.pishock.com/api/apioperate/",
            json={
                "Username": PISHOCK_USERNAME,
                "Apikey": PISHOCK_API_KEY,
                "Code": share_code,
                "Name": "TetherBot",
                "Op": op,  # 0=Shock, 1=Vibrate, 2=Beep
                "Duration": duration,
                "Intensity": intensity
            }
        )

        # Debugging: Print raw response details
        print("Status Code:", response.status_code)
        print("Response Headers:", response.headers)
        print("Response Text:", response.text)

        # Check HTTP status
        if response.status_code != 200:
            return {"Success": False, "Message": f"HTTP Error: {response.status_code}"}

        # Check for known success message in plain text
        if response.text.strip() == "Operation Attempted.":
            return {"Success": True, "Message": response.text.strip()}

        # Attempt JSON parsing for other responses
        try:
            json_response = response.json()
            return json_response
        except ValueError:
            return {"Success": False, "Message": f"Invalid response: {response.text}"}

    except Exception as e:
        print(f"Error sending PiShock command: {e}")
        return {"Success": False, "Message": str(e)}


@bot.command(name='shock')
async def shock(ctx, user: discord.Member, intensity: int):
    """Shock a user: !shock @User 50"""
    target = user

    # Authorization check for modifying others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return
    if not 1 <= intensity <= 80:
        return await ctx.send("Intensity must be between 1 and 100!")

    result = send_pishock_command(user.id, op=0, intensity=intensity)

    if result.get("Success"):
        await ctx.send(f"⚡ Shocked {user.display_name} at {intensity}%!")
    else:
        await ctx.send(f"❌ Failed: {result.get('Message')}")

@bot.command(name='vibrate')
async def vibrate(ctx, user: discord.Member, intensity: int):
    """Vibrate a user: !vibrate @User 50"""
    target = user
    # Authorization check for modifying others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return

    if not 1 <= intensity <= 100:
        return await ctx.send("Intensity must be between 1 and 100!")

    result = send_pishock_command(user.id, op=1, intensity=intensity)

    if result.get("Success"):
        await ctx.send(f"🔋 Vibrated {user.display_name} at {intensity}%!")
    else:
        await ctx.send(f"❌ Failed: {result.get('Message')}")


@bot.command(name='beep')
async def beep(ctx, user: discord.Member, duration: int):
    """Beep a user: !beep @User 5"""
    target = user

    # Authorization check for modifying others
    if not await check_auth(ctx, target):
        await ctx.message.add_reaction("❌")
        return
    
    if not 1 <= duration <= 15:
        return await ctx.send("Duration must be between 1 and 15 seconds!")

    # Intensity is set to 1 (not used for beep but required by API)
    result = send_pishock_command(user.id, op=2, intensity=1, duration=duration)

    if result.get("Success"):
        await ctx.send(f"🔔 Beeped {user.display_name} for {duration} seconds!")
    else:
        await ctx.send(f"❌ Failed: {result.get('Message')}")


# ------------------ MISC Variables ----------------------
VALID_ENFORCEMENT_ACTIONS = {"timeout", "gag", "cooldown"}



# ------------------- HELP SECTION -----------------------
# Help Command to List Available Gag Types
@bot.command()
async def gag_help(ctx):
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
async def gag_types(ctx):
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

# ------------------- LOCK COMMAND -------------------



@bot.command(name='lock')
async def lock(ctx, user: discord.Member = None):
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

    if target_id in locked_users:
        # Unlock
        c.execute("DELETE FROM locked_users WHERE user_id = ?", (target_id,))
        locked_users.remove(target_id)
        locked_by_map.pop(target_id, None)
        await ctx.send(f"🔓 {user.mention} is now unlocked.")
    else:
        # Lock
        c.execute("INSERT OR REPLACE INTO locked_users (user_id, locked_by) VALUES (?, ?)", (target_id, locker_id))
        locked_users.add(target_id)
        locked_by_map[target_id] = locker_id
        await ctx.send(f"🔒 {user.mention} is now locked and cannot use restricted commands.")

    conn.commit()




# ------------------- POWER BOT ---------------------

@bot.command()
async def restart(ctx):
    # Check if user is an admin or the specific allowed user
    if ctx.author.guild_permissions.administrator or ctx.author.id == 1146469921270792326:
        await ctx.send("🔄 Restarting bot...")
        subprocess.Popen([sys.executable, os.path.realpath(__file__)])
        sys.exit(0)
    else:
        await ctx.send("❌ You don’t have permission to restart the bot.")

@bot.command()
async def shutdown(ctx):
    # Check if user is an admin or the specific allowed user
    if ctx.author.guild_permissions.administrator or ctx.author.id == 1146469921270792326:
        await ctx.send("# OWIIIEEEE!! BOT IS OFFLINE... BEFORE YOU SAY 'THIS IS BROKEN PLEASE FIX'")
        await bot.close()
    else:
        await ctx.send("❌ You don’t have permission to shut down the bot.")


# ------------------- Error Handling -------------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ You don't have permission to use this command!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Missing required arguments!")
    else:
        print(f"Command error: {type(error).__name__} - {str(error)}")



# ------------------- Log all Commands -------------------
@bot.listen('on_command')
async def log_command(ctx):
    # Full message content
    full_message = ctx.message.content

    # Extract command and arguments
    command = ctx.command.name
    arguments = full_message[len(ctx.prefix) + len(command):].strip()  # Everything after the command

    # Insert into the database
    c.execute('''
        INSERT INTO command_logs (user_id, command, arguments)
        VALUES (?, ?, ?)
    ''', (ctx.author.id, command, arguments))
    conn.commit()


@tasks.loop(hours=24)
async def cleanup_old_logs():
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)  # Make it timezone-aware
    c.execute('DELETE FROM command_logs WHERE timestamp < ?', (seven_days_ago,))
    conn.commit()

@cleanup_old_logs.before_loop
async def before_cleanup():
    await bot.wait_until_ready()

cleanup_old_logs.start()

# Register a custom adapter for datetime objects
def adapt_datetime(dt):
    return dt.isoformat()  # Convert datetime to ISO 8601 format string

# Register the adapter with SQLite
sqlite3.register_adapter(datetime, adapt_datetime)


# ------------------- Command Limit -------------------


def is_within_limit(user_id):
    now = time.time()
    timestamps = user_command_timestamps.get(user_id, [])
    # Keep only recent timestamps
    timestamps = [t for t in timestamps if now - t < TIME_WINDOW]
    user_command_timestamps[user_id] = timestamps

    if len(timestamps) >= COMMAND_LIMIT:
        return False
    timestamps.append(now)
    return True

@bot.check
async def global_command_limit(ctx):
    if ctx.author.bot:
        return False

    user_id = ctx.author.id
    now = time.time()

    if not is_within_limit(user_id):
        warned_at = user_warned.get(user_id, 0)

        # Only send a warning if one hasn't been sent during this window
        if now - warned_at > TIME_WINDOW:
            user_warned[user_id] = now
            await ctx.send(f"⏱️ {ctx.author.mention}, you've hit the command limit ({COMMAND_LIMIT}/{TIME_WINDOW}s). Please wait.")

        return False

    # Clear warning state if they're back under the limit
    if user_id in user_warned:
        if now - user_warned[user_id] > TIME_WINDOW:
            del user_warned[user_id]

    return True


# ------------------- Run Bot -------------------
if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        print("\nBot shutting down...")
    finally:
        conn.close()
