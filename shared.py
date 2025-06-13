import os
import sqlite3
from dataclasses import dataclass
from functools import wraps
from types import SimpleNamespace

import discord
from discord.ext import commands
from discord.ext.commands import check

# --- Runtime Shared State ---


gagged_messages = {}
gag_functions = {}

AUTHORIZED_LOCK_MANAGERS = set()

check_auth = None
conn = None
c = None  # Global DB cursor (deprecated in favor of server DBs)

pishock_command = None


handle_gagged_message = None
enforcement_gag_send = None
handle_gag_reaction = None

enforce_prison_restrictions = None
temporarily_freed = {}

returnvar = False

# --- Global DB connection ---

os.makedirs("db", exist_ok=True)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
global_db_path = os.path.join(BASE_DIR, "db", "global.db")
global_conn = sqlite3.connect(global_db_path)
global_cursor = global_conn.cursor()


# Ensure table exists
global_cursor.execute('''
CREATE TABLE IF NOT EXISTS server_config (
    guild_id INTEGER PRIMARY KEY,
    prison_channel_id INTEGER,
    line_writing_id INTEGER,
    counting_id INTEGER,
    gambling_id INTEGER,
    solitary_role_id INTEGER,
    prisoner_role_id INTEGER,
    sc_role_id INTEGER,
    botstatus_id INTEGER,
    task_channel_id INTEGER,
    log_channel_id INTEGER,
    gags_enabled INTEGER DEFAULT 1,
    prison_enabled INTEGER DEFAULT 1,
    pishock_enabled INTEGER DEFAULT 1
)
''')
global_conn.commit()

# --- ServerConfig Dataclass ---

@dataclass
class ServerConfig:
    PRISON_CHANNEL_ID: int = None
    LINE_WRITING_ID: int = None
    COUNTING_ID: int = None
    GAMBLING_ID: int = None
    SOLITARY_ROLE_ID: int = None
    PRISONER_ROLE_ID: int = None
    SC_ROLE_ID: int = None
    BOTSTATUS_ID: int = None
    TASK_CHANNEL_ID: int = None
    LOG_CHANNEL_ID: int = None

CONFIG_KEYS = {
    "prison_channel_id": "PRISON_CHANNEL_ID",
    "line_writing_id": "LINE_WRITING_ID",
    "counting_id": "COUNTING_ID",
    "gambling_id": "GAMBLING_ID",
    "solitary_role_id": "SOLITARY_ROLE_ID",
    "prisoner_role_id": "PRISONER_ROLE_ID",
    "sc_role_id": "SC_ROLE_ID",
    "botstatus_id": "BOTSTATUS_ID",
    "task_channel_id": "TASK_CHANNEL_ID",
    "log_channel_id": "LOG_CHANNEL_ID"
}

def unpack_config(ctx):
    cfg = ctx.config
    return (
        cfg.PRISON_CHANNEL_ID,
        cfg.LINE_WRITING_ID,
        cfg.COUNTING_ID,
        cfg.GAMBLING_ID,
        cfg.SOLITARY_ROLE_ID,
        cfg.PRISONER_ROLE_ID,
        cfg.SC_ROLE_ID,
        cfg.BOTSTATUS_ID,
        cfg.TASK_CHANNEL_ID,
        cfg.LOG_CHANNEL_ID
    )
def unpack_config_for_guild(guild):
    cfg = get_server_config(guild.id)
    if not cfg:
        raise ValueError(f"No config found for guild {guild.id}")
    return (
        cfg.PRISON_CHANNEL_ID,
        cfg.LINE_WRITING_ID,
        cfg.COUNTING_ID,
        cfg.GAMBLING_ID,
        cfg.SOLITARY_ROLE_ID,
        cfg.PRISONER_ROLE_ID,
        cfg.SC_ROLE_ID,
        cfg.BOTSTATUS_ID,
        cfg.TASK_CHANNEL_ID,
        cfg.LOG_CHANNEL_ID,
    )


def unpack_config_from_obj(config):
    return unpack_config(SimpleNamespace(config=config))

# --- Server Config Access ---

def get_server_config(guild_id) -> ServerConfig | None:
    try:
        cur = global_conn.cursor()
        cur.execute("SELECT * FROM server_config WHERE guild_id = ?", (guild_id,))
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        raw_config = dict(zip(columns, row))
        kwargs = {
            CONFIG_KEYS[col]: raw_config.get(col)
            for col in CONFIG_KEYS
            if col in raw_config
        }
        return ServerConfig(**kwargs)
    except Exception as e:
        print(f"[shared] Failed to load server config: {e}")
        return None

# --- Cog Control ---

def is_cog_enabled(guild_id, cog_column):
    try:
        global_cursor.execute("PRAGMA table_info(server_config)")
        columns = [row[1] for row in global_cursor.fetchall()]
        if cog_column not in columns:
            return True
        global_cursor.execute(f"SELECT {cog_column} FROM server_config WHERE guild_id = ?", (guild_id,))
        row = global_cursor.fetchone()
        return row is None or row[0] == 1
    except Exception as e:
        print(f"[shared] is_cog_enabled failed: {e}")
        return True

def cog_enabled(cog_column):
    def predicate(ctx):
        return is_cog_enabled(ctx.guild.id, cog_column)
    return check(predicate)

# --- Per-Server Database Access ---

def get_server_db(guild_id: int) -> sqlite3.Connection:
    os.makedirs("db/servers", exist_ok=True)
    path = f"db/servers/{guild_id}.db"
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")  # 🔧 Add this
    initialize_server_db(conn)
    return conn


def initialize_server_db(conn: sqlite3.Connection):
    c = conn.cursor()

    # (Put all your CREATE TABLE statements here)
    c.execute('''CREATE TABLE IF NOT EXISTS gagged_users (
        user_id INTEGER PRIMARY KEY, type TEXT, status TEXT DEFAULT 'active')''')
    c.execute('''CREATE TABLE IF NOT EXISTS prison_users (
        user_id INTEGER PRIMARY KEY, channel_id INTEGER, entered_balance INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_auth (
        user_id INTEGER PRIMARY KEY, auth_level TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cooldown_users (
        user_id INTEGER PRIMARY KEY, cooldown INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS solitary_confinement (
        user_id INTEGER PRIMARY KEY, thread_id INTEGER, archive_date TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS enforced_words (
        user_id INTEGER, word TEXT, initial_time INTEGER, added_time INTEGER,
        PRIMARY KEY(user_id, word))''')
    c.execute('''CREATE TABLE IF NOT EXISTS enforcement_offenses (
        user_id INTEGER PRIMARY KEY, count INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS banned_words (
        user_id INTEGER, word TEXT, initial_time INTEGER, added_time INTEGER,
        PRIMARY KEY(user_id, word))''')
    c.execute('''CREATE TABLE IF NOT EXISTS ignored_users (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY, enforcement_action TEXT DEFAULT 'timeout')''')
    c.execute('''CREATE TABLE IF NOT EXISTS line_assignments (
        assignment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        line TEXT, lines_required INTEGER, penalty_lines INTEGER DEFAULT 0,
        last_submission TEXT, assigned_by INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS active_line_writers (
        user_id INTEGER, line TEXT, lines_required INTEGER,
        lines_written INTEGER DEFAULT 0, penalty_lines INTEGER DEFAULT 0,
        assignment_id INTEGER, assigned_by INTEGER,
        PRIMARY KEY (user_id, assignment_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_wallets (
        user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 1000)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, initiator_id INTEGER, opponent_id INTEGER,
        amount INTEGER, game TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_claims (
        user_id INTEGER PRIMARY KEY, last_claim TIMESTAMP, claim_count INTEGER DEFAULT 0, streak_days INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS timer_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        timer_name TEXT, start_time TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS command_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        command TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, arguments TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS allowed_users (
        user_id INTEGER, channel_id INTEGER, PRIMARY KEY (user_id, channel_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS locked_users (
        user_id INTEGER PRIMARY KEY, locked_by INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pishock_users (
        user_id INTEGER PRIMARY KEY, code TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS lovense_users (
        user_id INTEGER PRIMARY KEY, token TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS solitary_pings (
        thread_id INTEGER PRIMARY KEY, last_ping TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bot_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT, restart_status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pot (
        id INTEGER PRIMARY KEY CHECK(id = 1), pot INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS command_bans (
        user_id INTEGER PRIMARY KEY)''')

    conn.commit()

# --- Command Decorator ---

def with_config(func):  # for @bot.command
    @wraps(func)
    async def wrapper(ctx, *args, **kwargs):
        guild_id = ctx.guild.id if ctx.guild else None
        if not guild_id:
            await ctx.send("❌ This command must be used in a server.")
            return

        ctx.config = get_server_config(guild_id)
        ctx.server_db = get_server_db(guild_id)
        if not ctx.config:
            await ctx.send("⚠️ No config found for this server.")
            return

        return await func(ctx, *args, **kwargs)

    return wrapper

def with_config_cog(func):  # for methods in Cogs
    @wraps(func)
    async def wrapper(self_or_ctx, ctx=None, *args, **kwargs):
        if ctx is None:
            ctx = self_or_ctx
        guild_id = ctx.guild.id if ctx.guild else None
        if not guild_id:
            await ctx.send("❌ This command must be used in a server.")
            return

        ctx.config = get_server_config(guild_id)
        ctx.server_db = get_server_db(guild_id)
        if not ctx.config:
            await ctx.send("⚠️ No config found for this server.")
            return

        if ctx != self_or_ctx:
            return await func(self_or_ctx, ctx, *args, **kwargs)
        else:
            return await func(ctx, *args, **kwargs)

    return wrapper

class PerGuildDict:
    def __init__(self, default_factory=dict):
        self._store = {}
        self._default_factory = default_factory
        
    def __getitem__(self, guild_id):
        """Enable dictionary-style access with []"""
        if guild_id not in self._store:
            self._store[guild_id] = self._default_factory()
        return self._store[guild_id]
    
    def __setitem__(self, guild_id, value):
        """Enable dictionary-style assignment with []"""
        self._store[guild_id] = value
        
    def __contains__(self, guild_id):
        """Enable 'in' operator checks"""
        return guild_id in self._store
    def update(self, *args, **kwargs):
        return

        
    def get(self, guild_id, default=None):
        """Get the value for a guild_id, with optional default"""
        if guild_id in self._store:
            return self._store[guild_id]
        return default if default is not None else self._default_factory()

    def clear(self, guild_id):
        """Clear data for a specific guild"""
        self._store.pop(guild_id, None)

    def raw(self):
        """Return the raw storage dictionary"""
        return self._store


gagged_users = PerGuildDict(dict)              # {guild_id: {user_id: gag_type}}
prison_users = PerGuildDict(dict)              # {guild_id: {user_id: channel_id}}
cooldown_users = PerGuildDict(dict)
last_message_times = PerGuildDict(dict)
gagged_messages = PerGuildDict(dict)
solitary_confinement = PerGuildDict(dict)
enforced_words = PerGuildDict(lambda: {})
enforcement_offenses = PerGuildDict(dict)
banned_words = PerGuildDict(lambda: {})
ignored_users = PerGuildDict(lambda: set())
line_writing_sessions = PerGuildDict(dict)
locked_users = PerGuildDict(lambda: set())
user_pishock_codes = PerGuildDict(dict)

current_number = PerGuildDict(lambda: 0)
last_user = PerGuildDict(lambda: None)
user_command_timestamps = PerGuildDict(dict)
user_warned = PerGuildDict(lambda: set())
locked_by_map = PerGuildDict(dict)
counting_game = PerGuildDict(lambda: {
    "active": False,
    "count": 0,
    "last_user_id": None,
    "participants": set()
})



pending_blackjack_timeout = PerGuildDict(lambda: None)
bot_paused = PerGuildDict(lambda: False)


def list_toggleable_cogs():
    return [
        filename[:-3]
        for filename in os.listdir("cogs")
        if filename.endswith(".py") and filename not in {"__init__.py", "init__.py"}
    ]

def ensure_cog_columns_from_files():
    try:
        cur = conn.cursor()
        toggleable_cogs = list_toggleable_cogs()

        # Get current columns
        cur.execute("PRAGMA table_info(server_config)")
        existing_columns = [row[1] for row in cur.fetchall()]

        for cog in toggleable_cogs:
            column_name = f"{cog}_enabled"
            if column_name not in existing_columns:
                cur.execute(f"ALTER TABLE server_config ADD COLUMN {column_name} INTEGER DEFAULT 1")
                print(f"✅ Added column '{column_name}' for cog '{cog}'")

        conn.commit()
    except Exception as e:
        print(f"[ensure_cog_columns_from_files] Error: {e}")
