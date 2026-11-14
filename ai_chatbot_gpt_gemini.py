"""
AI Chat Bot - Reverse Engineered Client (Telegram Bot Edition for Railway)
==========================================================================
Replicates the Android app's chat functionality inside a Telegram Bot.
Connects to Firebase Cloud Function endpoint.
Maintains chat history with SQLite persistence.
Supports multiple OpenAI, Gemini, Claude, and DeepSeek models.

Reverse engineered from: com.open.ai.chat.bot.ask.questions v1.5.6
"""

import json
import uuid
import sqlite3
import signal
import sys
import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("[!] 'requests' library not found. Installing...")
    os.system(f"{sys.executable} -m pip install requests")
    import requests

# ============================================================================
# ENVIRONMENT & CONFIGURATION
# ============================================================================


def load_env_token():
    """Load API_BOT from environment or local .env file if present."""
    token = os.environ.get("API_BOT", "").strip()
    if token:
        return token
    env_path = Path(".env")
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "API_BOT":
                    return v.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


API_BOT = load_env_token()

DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant. Be concise and helpful."

CONFIG = {
    # Telegram Bot Token from environment variable API_BOT
    "api_bot": API_BOT,

    # Default model (best model selected by default: GPT-4o)
    "default_model": "gpt-4o",

    # Firebase Cloud Function endpoint (from HAR analysis)
    "chat_endpoint": "https://us-central1-ai-chat-bot-bf47e.cloudfunctions.net/chat_function_android",

    # Firebase identifiers (extracted from HAR)
    "firebase_project_number": "887316553772",
    "firebase_project_id": "ai-chat-bot-bf47e",
    "firebase_app_id": "1:887316553772:android:079f99ea92c4115e837189",

    # Device fingerprint (mimics Samsung SM-X910N from HAR)
    "device": {
        "model": "SM-X910N",
        "brand": "samsung",
        "android_version": "14",
        "sdk_version": "34",
        "build": "UQ1A.240205.07241537",
    },

    # App info
    "app_package": "com.open.ai.chat.bot.ask.questions",
    "app_version": "1.5.6",
    "build_version": "60",

    # User-Agent string (from HAR)
    "user_agent": "Dalvik/2.1.0 (Linux; U; Android 14; SM-X910N Build/UQ1A.240205.07241537)",

    # Available models (extracted from APK decompilation - with correct API IDs & fallbacks)
    "models": {
        "1": {
            "id": "gpt-4o-mini",
            "aliases": ["gpt-4o-mini", "gpt-4o-mini-2024-07-18"],
            "name": "GPT-4o Mini",
            "description": "Fast & cheap",
            "tier": "FREE",
            "supports_system_role": True,
        },
        "2": {
            "id": "gpt-4o",
            "aliases": ["gpt-4o", "gpt-4o-2024-08-06", "chatgpt-4o-latest"],
            "name": "GPT-4o",
            "description": "Most capable (Best)",
            "tier": "PRO",
            "supports_system_role": True,
        },
        "3": {
            "id": "o1",
            "aliases": ["o1", "o1-preview", "o1-2024-12-17"],
            "name": "GPT-01",
            "description": "Reasoning model",
            "tier": "PRO",
            "supports_system_role": False,
        },
        "4": {
            "id": "o1-mini",
            "aliases": ["o1-mini", "o1-mini-2024-09-12"],
            "name": "GPT-01 Mini",
            "description": "Fast reasoning",
            "tier": "PRO",
            "supports_system_role": False,
        },
        "5": {
            "id": "gemini-1.5-flash",
            "aliases": ["gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-1.5-pro", "gemini-pro"],
            "name": "Gemini 1.5 Flash",
            "description": "Google fast",
            "tier": "PRO",
            "supports_system_role": False,
        },
        "6": {
            "id": "claude-3-5-haiku-latest",
            "aliases": ["claude-3-5-haiku-latest", "claude-3-5-haiku-20241022", "claude-3-haiku-20240307"],
            "name": "Claude 3.5 Haiku",
            "description": "Anthropic fast",
            "tier": "PRO",
            "supports_system_role": False,
        },
        "7": {
            "id": "claude-3-5-sonnet-latest",
            "aliases": ["claude-3-5-sonnet-latest", "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620"],
            "name": "Claude 3.5 Sonnet",
            "description": "Anthropic smart",
            "tier": "PRO",
            "supports_system_role": False,
        },
        "8": {
            "id": "deepseek-r1",
            "aliases": ["deepseek-r1", "deepseek-reasoner"],
            "name": "DeepSeek R1",
            "description": "Deep reasoning",
            "tier": "PRO",
            "supports_system_role": False,
        },
        "9": {
            "id": "deepseek-v3",
            "aliases": ["deepseek-v3", "deepseek-chat"],
            "name": "DeepSeek V3",
            "description": "Deep fast",
            "tier": "PRO",
            "supports_system_role": True,
        },
    },

    # Admin/Owner privileges
    "admin_mode": True,
    "bypass_subscription": True,
    "unlimited_messages": True,

    # Content filter (disabled for admin)
    "content_filter_enabled": False,

    # Translation API (from APK)
    "translation_endpoint": "https://www.cardscanner.co/api/translate",

    # Notification API (from APK)
    "notification_endpoint": "https://aso.eclixtech.com/api/register-device",

    # Developer domain
    "developer_domain": "eclixtech.com",

    # Database path
    "db_path": os.environ.get("DB_PATH", "chat_history.db"),

    # Debug mode
    "debug_mode": False,

    # Colors for terminal logs
    "colors": {
        "user": "\033[96m",      # Cyan
        "assistant": "\033[92m",  # Green
        "system": "\033[93m",     # Yellow
        "error": "\033[91m",      # Red
        "reset": "\033[0m",       # Reset
        "bold": "\033[1m",        # Bold
    }
}


def resolve_model_choice(choice_str, models_config=None):
    """
    Resolve a user's model selection string (key '1'-'9', model ID, model name,
    callback data, or partial keyword) to the canonical model dict.
    Returns the model dict or None if no match.
    """
    if not choice_str:
        return None
    models = models_config or CONFIG["models"]
    raw = str(choice_str).strip()
    if raw.lower().startswith("model:"):
        raw = raw.split(":", 1)[1].strip()
    if raw.startswith("/"):
        raw = raw[1:].strip()

    # 1. Direct key match ("1" .. "9")
    if raw in models:
        return models[raw]

    low = raw.lower()

    # 2. Exact ID, alias, or name match
    for key, info in models.items():
        if info["id"].lower() == low or info["name"].lower() == low:
            return info
        for alias in info.get("aliases", []):
            if alias.lower() == low:
                return info

    # 3. Normalized match (ignoring spaces, hyphens, underscores)
    norm = low.replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
    for key, info in models.items():
        id_norm = info["id"].lower().replace("-", "").replace("_", "").replace(".", "")
        name_norm = info["name"].lower().replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
        if norm in (id_norm, name_norm, f"model{key}"):
            return info

    # 4. Unambiguous substring match
    substring_matches = []
    for key, info in models.items():
        haystack = f"{info['id']} {info['name']} {' '.join(info.get('aliases', []))}".lower()
        if low in haystack:
            substring_matches.append(info)
    if len(substring_matches) == 1:
        return substring_matches[0]

    return None


# ============================================================================
# DATABASE MANAGER
# ============================================================================

class ChatDatabase:
    """SQLite database for chat history and per-user Telegram state persistence."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        default_model = CONFIG.get("default_model", "gpt-4o")
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id INTEGER DEFAULT 0,
                    model TEXT NOT NULL,
                    system_prompt TEXT DEFAULT 'You are a helpful AI assistant. Be concise and helpful.',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens_used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES chats(id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER DEFAULT 0,
                    model TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP
                )
            """)
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    current_chat_id INTEGER,
                    current_session_id TEXT,
                    current_model TEXT DEFAULT '{default_model}',
                    system_prompt TEXT DEFAULT 'You are a helpful AI assistant. Be concise and helpful.',
                    debug_mode INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Ensure columns exist if upgrading from an older schema
            cursor.execute("PRAGMA table_info(chats)")
            chat_cols = [row[1] for row in cursor.fetchall()]
            if "user_id" not in chat_cols:
                cursor.execute("ALTER TABLE chats ADD COLUMN user_id INTEGER DEFAULT 0")
            if "system_prompt" not in chat_cols:
                cursor.execute("ALTER TABLE chats ADD COLUMN system_prompt TEXT DEFAULT 'You are a helpful AI assistant. Be concise and helpful.'")

            cursor.execute("PRAGMA table_info(sessions)")
            session_cols = [row[1] for row in cursor.fetchall()]
            if "user_id" not in session_cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER DEFAULT 0")

            cursor.execute("PRAGMA table_info(user_state)")
            state_cols = [row[1] for row in cursor.fetchall()]
            if "current_chat_id" not in state_cols:
                cursor.execute("ALTER TABLE user_state ADD COLUMN current_chat_id INTEGER")
            if "current_session_id" not in state_cols:
                cursor.execute("ALTER TABLE user_state ADD COLUMN current_session_id TEXT")
            if "current_model" not in state_cols:
                cursor.execute(f"ALTER TABLE user_state ADD COLUMN current_model TEXT DEFAULT '{default_model}'")
            if "system_prompt" not in state_cols:
                cursor.execute("ALTER TABLE user_state ADD COLUMN system_prompt TEXT DEFAULT 'You are a helpful AI assistant. Be concise and helpful.'")
            if "debug_mode" not in state_cols:
                cursor.execute("ALTER TABLE user_state ADD COLUMN debug_mode INTEGER DEFAULT 0")
            if "updated_at" not in state_cols:
                cursor.execute("ALTER TABLE user_state ADD COLUMN updated_at TIMESTAMP")

            self.conn.commit()

    def get_or_create_user_state(self, user_id, default_model=None):
        if default_model is None:
            default_model = CONFIG.get("default_model", "gpt-4o")
        user_id = int(user_id)
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT user_id, current_chat_id, current_session_id, current_model, system_prompt, debug_mode FROM user_state WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                chat_id = row[1]
                session_id = row[2] or str(uuid.uuid4())[:8]
                current_model = row[3] or default_model
                system_prompt = row[4] or DEFAULT_SYSTEM_PROMPT
                debug_mode = bool(row[5])

                # Ensure chat_id points to a valid chat row
                cursor.execute("SELECT id FROM chats WHERE id = ?", (chat_id,))
                if not chat_id or not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO chats (session_id, user_id, model, system_prompt) VALUES (?, ?, ?, ?)",
                        (session_id, user_id, current_model, system_prompt)
                    )
                    chat_id = cursor.lastrowid
                    cursor.execute(
                        "UPDATE user_state SET current_chat_id = ?, current_session_id = ? WHERE user_id = ?",
                        (chat_id, session_id, user_id)
                    )
                    self.conn.commit()

                return {
                    "user_id": row[0],
                    "current_chat_id": chat_id,
                    "current_session_id": session_id,
                    "current_model": current_model,
                    "system_prompt": system_prompt,
                    "debug_mode": debug_mode,
                }

            session_id = str(uuid.uuid4())[:8]
            cursor.execute(
                "INSERT OR REPLACE INTO sessions (id, user_id, model) VALUES (?, ?, ?)",
                (session_id, user_id, default_model)
            )
            cursor.execute(
                "INSERT INTO chats (session_id, user_id, model, system_prompt) VALUES (?, ?, ?, ?)",
                (session_id, user_id, default_model, DEFAULT_SYSTEM_PROMPT)
            )
            chat_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO user_state (user_id, current_chat_id, current_session_id, current_model, system_prompt, debug_mode)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (user_id, chat_id, session_id, default_model, DEFAULT_SYSTEM_PROMPT)
            )
            self.conn.commit()
            return {
                "user_id": user_id,
                "current_chat_id": chat_id,
                "current_session_id": session_id,
                "current_model": default_model,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
                "debug_mode": False,
            }

    def update_user_state(self, user_id, **kwargs):
        if not kwargs:
            return
        user_id = int(user_id)
        with self.lock:
            # Ensure the user_state row exists first
            self.get_or_create_user_state(user_id)
            fields = []
            values = []
            for k, v in kwargs.items():
                fields.append(f"{k} = ?")
                values.append(v)
            fields.append("updated_at = CURRENT_TIMESTAMP")
            values.append(user_id)
            sql = f"UPDATE user_state SET {', '.join(fields)} WHERE user_id = ?"
            cursor = self.conn.cursor()
            cursor.execute(sql, tuple(values))
            self.conn.commit()

    def switch_user_model(self, user_id, new_model_id, chat_id_Sync=None):
        """
        Atomically switch the user's active model across user_state, sessions, and chats.
        If the current chat already contains messages from a different model, starts a new
        chat in the same session so prior model identity responses never pollute the new model.
        """
        user_id = int(user_id)
        with self.lock:
            state = self.get_or_create_user_state(user_id)
            active_chat_id = state["current_chat_id"]
            session_id = state["current_session_id"]
            system_prompt = state["system_prompt"]

            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*), model FROM chats LEFT JOIN messages ON chats.id = messages.chat_id WHERE chats.id = ?", (active_chat_id,))
            row = cursor.fetchone()
            msg_count = row[0] if row else 0
            old_chat_model = row[1] if row else None

            if msg_count > 0 and old_chat_model and old_chat_model != new_model_id:
                # Create a clean chat for the new model so previous model's self-identification doesn't carry over
                cursor.execute(
                    "INSERT INTO chats (session_id, user_id, model, system_prompt) VALUES (?, ?, ?, ?)",
                    (session_id, user_id, new_model_id, system_prompt)
                )
                active_chat_id = cursor.lastrowid
            else:
                cursor.execute(
                    "UPDATE chats SET model = ? WHERE id = ?",
                    (new_model_id, active_chat_id)
                )

            cursor.execute(
                "UPDATE sessions SET model = ? WHERE id = ?",
                (new_model_id, session_id)
            )
            cursor.execute(
                "UPDATE user_state SET current_model = ?, current_chat_id = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (new_model_id, active_chat_id, user_id)
            )

            if chat_id_Sync is not None and int(chat_id_Sync) != user_id:
                sync_id = int(chat_id_Sync)
                self.get_or_create_user_state(sync_id, default_model=new_model_id)
                cursor.execute(
                    "UPDATE user_state SET current_model = ?, current_chat_id = ?, current_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (new_model_id, active_chat_id, session_id, sync_id)
                )

            self.conn.commit()
            return active_chat_id

    def create_session(self, session_id, model, user_id=0):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sessions (id, user_id, model) VALUES (?, ?, ?)",
                (session_id, user_id, model)
            )
            self.conn.commit()

    def create_chat(self, session_id, model, user_id=0, system_prompt=DEFAULT_SYSTEM_PROMPT):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO chats (session_id, user_id, model, system_prompt) VALUES (?, ?, ?, ?)",
                (session_id, user_id, model, system_prompt)
            )
            self.conn.commit()
            return cursor.lastrowid

    def update_chat_model(self, chat_id, model):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("UPDATE chats SET model = ? WHERE id = ?", (model, chat_id))
            self.conn.commit()

    def add_message(self, chat_id, role, content, tokens=0):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO messages (chat_id, role, content, tokens_used) VALUES (?, ?, ?, ?)",
                (chat_id, role, content, tokens)
            )
            msg_id = cursor.lastrowid
            self.conn.commit()
            return msg_id

    def delete_message(self, message_row_id):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM messages WHERE id = ?", (message_row_id,))
            self.conn.commit()

    def get_chat_history(self, chat_id, limit=50):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (chat_id, limit)
            )
            return list(reversed(cursor.fetchall()))

    def get_session_chats(self, session_id):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT id, model, created_at FROM chats WHERE session_id = ? ORDER BY id",
                (session_id,)
            )
            return cursor.fetchall()

    def get_all_sessions(self, user_id=None):
        with self.lock:
            cursor = self.conn.cursor()
            if user_id is not None:
                cursor.execute(
                    "SELECT id, model, created_at, ended_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
                    (user_id,)
                )
            else:
                cursor.execute(
                    "SELECT id, model, created_at, ended_at FROM sessions ORDER BY created_at DESC LIMIT 20"
                )
            return cursor.fetchall()

    def get_recent_chats(self, limit=10, user_id=None):
        with self.lock:
            cursor = self.conn.cursor()
            if user_id is not None:
                cursor.execute(
                    """SELECT c.id, c.model, c.created_at,
                              (SELECT content FROM messages WHERE chat_id = c.id AND role = 'user' ORDER BY id LIMIT 1) as first_msg
                       FROM chats c
                       WHERE c.user_id = ?
                       ORDER BY c.id DESC LIMIT ?""",
                    (user_id, limit)
                )
            else:
                cursor.execute(
                    """SELECT c.id, c.model, c.created_at,
                              (SELECT content FROM messages WHERE chat_id = c.id AND role = 'user' ORDER BY id LIMIT 1) as first_msg
                       FROM chats c ORDER BY c.id DESC LIMIT ?""",
                    (limit,)
                )
            return cursor.fetchall()

    def get_chat_by_id(self, chat_id, user_id=None):
        """Get chat info by ID."""
        with self.lock:
            cursor = self.conn.cursor()
            if user_id is not None:
                cursor.execute(
                    "SELECT id, model, created_at, system_prompt FROM chats WHERE id = ? AND user_id = ?",
                    (chat_id, user_id)
                )
            else:
                cursor.execute(
                    "SELECT id, model, created_at, system_prompt FROM chats WHERE id = ?",
                    (chat_id,)
                )
            return cursor.fetchone()

    def close(self):
        with self.lock:
            self.conn.close()


# ============================================================================
# FIREBASE TOKEN GENERATOR
# ============================================================================

class FirebaseTokenGenerator:
    """
    Generates Firebase-like tokens for authentication.
    In the real app, this is handled by Firebase SDK.
    """

    @staticmethod
    def generate_firebase_instance_id():
        """Generate a random Firebase Installation ID format."""
        parts = [
            uuid.uuid4().hex[:7],
            uuid.uuid4().hex[:20],
        ]
        return "-".join(parts)

    @staticmethod
    def generate_fcm_token(fid):
        """Generate a fake FCM token based on FID."""
        random_part = uuid.uuid4().hex[:32]
        return f"{fid}:{random_part}"

    @staticmethod
    def generate_session_token():
        """Generate a session/local storage token."""
        return uuid.uuid4().hex


# ============================================================================
# CHAT CLIENT
# ============================================================================

class ChatClient:
    """
    Firebase Cloud Function chat client.
    Replicates the Android app's network behavior and handles model-specific
    payload formatting and response parsing across OpenAI, Gemini, Claude, and DeepSeek.
    """

    def __init__(self, config):
        self.config = config
        self.fid = FirebaseTokenGenerator.generate_firebase_instance_id()
        self.fcm_token = FirebaseTokenGenerator.generate_fcm_token(self.fid)
        self.session_token = FirebaseTokenGenerator.generate_session_token()
        self.headers = {
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "user-agent": self.config["user_agent"],
            "firebase-instance-id-token": self.fcm_token,
            "X-Android-Package": self.config["app_package"],
            "X-Android-Cert": "085878EDBA0200080CA6CD94242C6ADBE21F28BA",
        }

    def format_message_content(self, text):
        """
        Format message content as expected by the Cloud Function.
        From HAR analysis, content can be an array of objects.
        """
        return [{"type": "text", "text": text}]

    def apply_content_filter(self, text):
        """Apply content filter if enabled."""
        if not self.config.get("content_filter_enabled"):
            return text
        return text

    def get_model_spec(self, model_id):
        """Return model metadata dict from CONFIG['models']."""
        found = resolve_model_choice(model_id, self.config.get("models"))
        if found:
            return found
        low = (model_id or "").lower()
        supports_sys = not (
            low.startswith("o1")
            or "claude" in low
            or "gemini" in low
            or "deepseek-r1" in low
        )
        return {
            "id": model_id,
            "aliases": [model_id],
            "name": model_id,
            "description": "AI Model",
            "tier": "PRO",
            "supports_system_role": supports_sys,
        }

    def normalize_messages(self, messages, include_system_role=True, model_info=None):
        """
        Produce a clean, strictly alternating list of messages suitable for OpenAI,
        Anthropic Claude, Google Gemini, o1 reasoning models, and DeepSeek.
        """
        system_parts = []
        conversation = []

        for msg in messages:
            role = (msg.get("role") or "user").lower()
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            content = str(content).strip()
            if not content:
                continue

            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                # Merge consecutive messages of the same role so Claude/Gemini/DeepSeek never reject
                if conversation and conversation[-1]["role"] == role:
                    conversation[-1]["content"] += f"\n\n{content}"
                else:
                    conversation.append({"role": role, "content": content})

        # Ensure conversation starts with a user message
        while conversation and conversation[0]["role"] != "user":
            conversation.pop(0)

        if not conversation:
            return []

        system_text = "\n".join(system_parts).strip()

        if include_system_role and system_text:
            return [{"role": "system", "content": system_text}] + [dict(m) for m in conversation]

        # When system role is omitted (o1, o1-mini, Claude, Gemini, DeepSeek R1),
        # only prepend custom system instructions if the user customized them
        cloned = [dict(m) for m in conversation]
        if system_text and system_text != DEFAULT_SYSTEM_PROMPT:
            cloned[0]["content"] = f"[Instructions: {system_text}]\n\n{cloned[0]['content']}"
        return cloned

    def build_request_payload(self, messages, model, temperature=0.7, max_tokens=1000, use_array_content=False):
        """Build the request payload matching the Android app format (wrapped in 'data')."""
        formatted_messages = []
        for msg in messages:
            content = msg["content"]
            if use_array_content and isinstance(content, str):
                content = self.format_message_content(content)
            formatted_messages.append({
                "role": msg["role"],
                "content": content,
            })

        return {
            "data": {
                "messages": formatted_messages,
                "model": model,
            }
        }

    def build_request_payload_v2(self, messages, model):
        """Alternative format: Direct OpenAI-compatible format."""
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        return {
            "model": model,
            "messages": formatted_messages,
        }

    def send_message(self, messages, model, temperature=0.7, max_tokens=1000):
        """
        Send message to Firebase Cloud Function using the selected model.
        Tries model-compatible message formats and model aliases before falling back.
        """
        spec = self.get_model_spec(model)
        canonical_model = spec["id"]
        candidate_models = list(dict.fromkeys([canonical_model] + spec.get("aliases", [canonical_model])))
        supports_sys = spec.get("supports_system_role", False)

        msgs_no_sys = self.normalize_messages(messages, include_system_role=False, model_info=spec)
        msgs_with_sys = self.normalize_messages(messages, include_system_role=True, model_info=spec)

        if not msgs_no_sys:
            return None

        # Single-turn fallback for the chosen model in case multi-turn context causes backend error
        last_user_msg = [{"role": "user", "content": msgs_no_sys[-1]["content"]}]

        # Order message variants so models that reject 'system' role try user-only messages first
        if supports_sys:
            msg_variants = [
                ("wrapped-no-sys", msgs_no_sys, False, True),
                ("wrapped-sys", msgs_with_sys, False, True),
                ("wrapped-array", msgs_no_sys, True, True),
                ("direct-no-sys", msgs_no_sys, False, False),
                ("single-turn", last_user_msg, False, True),
            ]
        else:
            msg_variants = [
                ("wrapped-no-sys", msgs_no_sys, False, True),
                ("wrapped-array", msgs_no_sys, True, True),
                ("single-turn", last_user_msg, False, True),
                ("direct-no-sys", msgs_no_sys, False, False),
                ("wrapped-sys", msgs_with_sys, False, True),
            ]

        try:
            print(f"{self.config['colors']['system']}[*] Sending to Cloud Function (model: {canonical_model})...{self.config['colors']['reset']}")
            last_error_text = ""

            for candidate_id in candidate_models:
                for variant_name, msg_list, use_array, wrapped in msg_variants:
                    if wrapped:
                        payload = self.build_request_payload(
                            msg_list,
                            candidate_id,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            use_array_content=use_array,
                        )
                    else:
                        payload = self.build_request_payload_v2(msg_list, candidate_id)

                    response = requests.post(
                        self.config["chat_endpoint"],
                        headers=self.headers,
                        json=payload,
                        timeout=60,
                    )

                    if response.status_code == 200:
                        try:
                            data = response.json()
                        except Exception:
                            data = {"result": response.text}
                        parsed = self.parse_response(data, requested_model=canonical_model)
                        if parsed and parsed.get("content"):
                            parsed["requested_model"] = canonical_model
                            return parsed
                    last_error_text = response.text[:300]

            # Final fallback if the backend endpoint temporarily rejects the specific model ID
            if canonical_model != "gpt-4o-mini":
                print(f"{self.config['colors']['system']}[*] Primary model '{canonical_model}' unavailable on backend. Trying fallback...{self.config['colors']['reset']}")
                for fb_model in ("gpt-4o", "gpt-4o-mini"):
                    if fb_model == canonical_model:
                        continue
                    payload_fallback = self.build_request_payload(msgs_no_sys, fb_model, temperature, max_tokens)
                    response_fallback = requests.post(
                        self.config["chat_endpoint"],
                        headers=self.headers,
                        json=payload_fallback,
                        timeout=60,
                    )
                    if response_fallback.status_code == 200:
                        data = response_fallback.json()
                        parsed = self.parse_response(data, requested_model=canonical_model)
                        if parsed and parsed.get("content"):
                            parsed["requested_model"] = canonical_model
                            parsed["backend_model"] = parsed.get("model", fb_model)
                            parsed["model"] = canonical_model
                            parsed["fallback_used"] = True
                            return parsed

            print(f"{self.config['colors']['error']}[!] All formats failed. Last response: {last_error_text}{self.config['colors']['reset']}")
            return None

        except requests.exceptions.Timeout:
            print(f"{self.config['colors']['error']}[!] Request timed out{self.config['colors']['reset']}")
            return None
        except requests.exceptions.ConnectionError:
            print(f"{self.config['colors']['error']}[!] Connection error - check internet{self.config['colors']['reset']}")
            return None
        except Exception as e:
            print(f"{self.config['colors']['error']}[!] Error: {str(e)}{self.config['colors']['reset']}")
            return None

    @staticmethod
    def _extract_text_from_content(content):
        """Extract plain string from string, list of content blocks, or dict."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if "text" in item and isinstance(item["text"], str):
                        parts.append(item["text"])
                    elif "content" in item and isinstance(item["content"], str):
                        parts.append(item["content"])
            return "\n".join(p for p in parts if p)
        if isinstance(content, dict):
            for key in ("text", "content", "value", "response"):
                if isinstance(content.get(key), str):
                    return content[key]
        return ""

    def parse_response(self, data, requested_model=None):
        """
        Parse the Cloud Function response across OpenAI, Anthropic Claude,
        Google Gemini, and DeepSeek formats.
        """
        try:
            if isinstance(data, str):
                text = data.strip()
                if not text:
                    return None
                return {
                    "content": text,
                    "role": "assistant",
                    "model": requested_model or "unknown",
                    "finish_reason": "stop",
                    "tokens": {"prompt": 0, "completion": 0, "total": 0},
                    "id": "",
                }

            if not isinstance(data, dict):
                return None

            result = data.get("result", data)

            if isinstance(result, str):
                text = result.strip()
                if not text:
                    return None
                return {
                    "content": text,
                    "role": "assistant",
                    "model": requested_model or "unknown",
                    "finish_reason": "stop",
                    "tokens": {"prompt": 0, "completion": 0, "total": 0},
                    "id": "",
                }

            if not isinstance(result, dict):
                return None

            # Reject explicit error payloads wrapped inside HTTP 200
            if "error" in result and result["error"] and not result.get("choices") and not result.get("content") and not result.get("candidates"):
                return None

            # 1. OpenAI / DeepSeek format: result.choices[0].message.content
            choices = result.get("choices", [])
            if isinstance(choices, list) and choices:
                first_choice = choices[0] if isinstance(choices[0], dict) else {}
                message = first_choice.get("message") or first_choice.get("delta") or {}
                content = self._extract_text_from_content(message.get("content", ""))
                if not content and isinstance(first_choice.get("text"), str):
                    content = first_choice["text"]
                usage = result.get("usage", {}) if isinstance(result.get("usage"), dict) else {}
                if content:
                    return {
                        "content": content,
                        "role": message.get("role", "assistant"),
                        "model": result.get("model") or requested_model or "unknown",
                        "finish_reason": first_choice.get("finish_reason", "stop"),
                        "tokens": {
                            "prompt": usage.get("prompt_tokens", 0),
                            "completion": usage.get("completion_tokens", 0),
                            "total": usage.get("total_tokens", 0),
                        },
                        "id": result.get("id", ""),
                    }

            # 2. Anthropic Claude format: result.content = [{"type": "text", "text": "..."}]
            if "content" in result:
                content = self._extract_text_from_content(result["content"])
                if content:
                    usage = result.get("usage", {}) if isinstance(result.get("usage"), dict) else {}
                    in_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0))
                    out_tok = usage.get("output_tokens", usage.get("completion_tokens", 0))
                    return {
                        "content": content,
                        "role": result.get("role", "assistant"),
                        "model": result.get("model") or requested_model or "unknown",
                        "finish_reason": result.get("stop_reason", "stop"),
                        "tokens": {
                            "prompt": in_tok,
                            "completion": out_tok,
                            "total": usage.get("total_tokens", in_tok + out_tok),
                        },
                        "id": result.get("id", ""),
                    }

            # 3. Google Gemini format: result.candidates[0].content.parts[0].text
            candidates = result.get("candidates", [])
            if isinstance(candidates, list) and candidates:
                cand = candidates[0] if isinstance(candidates[0], dict) else {}
                cand_content = cand.get("content", {}) if isinstance(cand.get("content"), dict) else {}
                parts = cand_content.get("parts", [])
                content = self._extract_text_from_content(parts)
                if content:
                    usage = result.get("usageMetadata", {}) if isinstance(result.get("usageMetadata"), dict) else {}
                    return {
                        "content": content,
                        "role": "assistant",
                        "model": result.get("modelVersion") or result.get("model") or requested_model or "unknown",
                        "finish_reason": cand.get("finishReason", "stop"),
                        "tokens": {
                            "prompt": usage.get("promptTokenCount", 0),
                            "completion": usage.get("candidatesTokenCount", 0),
                            "total": usage.get("totalTokenCount", 0),
                        },
                        "id": result.get("id", ""),
                    }

            # 4. Generic direct keys: message / text / response / output / answer
            for key in ("message", "text", "response", "output", "output_text", "answer", "reply"):
                if key in result:
                    content = self._extract_text_from_content(result[key])
                    if content:
                        return {
                            "content": content,
                            "role": "assistant",
                            "model": result.get("model") or requested_model or "unknown",
                            "finish_reason": "stop",
                            "tokens": {"prompt": 0, "completion": 0, "total": 0},
                            "id": result.get("id", ""),
                        }

            return None

        except Exception as e:
            print(f"{self.config['colors']['error']}[!] Parse error: {e}{self.config['colors']['reset']}")
            return None


# ============================================================================
# RAILWAY HEALTHCHECK SERVER (OPTIONAL PORT BINDING)
# ============================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler so Railway web services pass health checks if PORT is set."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"AI Chat Bot Telegram Service is running.")

    def log_message(self, format, *args):
        return


def start_railway_health_server():
    """Start a background HTTP server if Railway provides a PORT environment variable."""
    port_str = os.environ.get("PORT")
    if not port_str:
        return
    try:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"[*] Railway healthcheck server listening on port {port}")
    except Exception as e:
        print(f"[!] Could not bind healthcheck server: {e}")


# ============================================================================
# TELEGRAM BOT APPLICATION
# ============================================================================

class TelegramBotApp:
    """
    Full-featured Telegram Bot wrapper around ChatClient and ChatDatabase.
    Uses the official Telegram Bot HTTP API via `requests` (zero extra dependencies)
    and reads the bot token from the `API_BOT` environment variable.
    """

    def __init__(self, token, config=None):
        self.token = token
        self.config = config or CONFIG
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.db = ChatDatabase(self.config["db_path"])
        self.client = ChatClient(self.config)
        self.running = True
        self.offset = 0
        # In-memory fast lookup for active model and pending '/model' text selection
        self.active_models = {}
        self.awaiting_model_selection = {}

        try:
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        except ValueError:
            pass

    def signal_handler(self, sig, frame):
        print(f"\n{self.config['colors']['system']}[*] Shutting down Telegram bot gracefully...{self.config['colors']['reset']}")
        self.running = False
        self.db.close()
        sys.exit(0)

    # ------------------------------------------------------------------------
    # Telegram API Helpers (Thread-Safe via Direct Requests)
    # ------------------------------------------------------------------------

    def tg_request(self, method, payload=None, files=None, timeout=35):
        url = f"{self.base_url}/{method}"
        try:
            if files:
                resp = requests.post(url, data=payload, files=files, timeout=timeout)
            else:
                resp = requests.post(url, json=payload, timeout=timeout)
            return resp.json()
        except Exception as e:
            print(f"{self.config['colors']['error']}[!] Telegram API error ({method}): {e}{self.config['colors']['reset']}")
            return {"ok": False, "description": str(e)}

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML", reply_to_message_id=None):
        """Send a message to a Telegram user, automatically splitting messages > 4000 chars."""
        if not text:
            text = "(Empty response)"

        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        last_result = None

        for idx, chunk in enumerate(chunks):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_to_message_id and idx == 0:
                payload["reply_to_message_id"] = reply_to_message_id
            if reply_markup and idx == len(chunks) - 1:
                payload["reply_markup"] = reply_markup

            res = self.tg_request("sendMessage", payload)
            if not res.get("ok") and parse_mode:
                # Fallback to plain text if HTML/Markdown entities fail to parse
                payload.pop("parse_mode", None)
                res = self.tg_request("sendMessage", payload)
            last_result = res

        return last_result

    def edit_message(self, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        res = self.tg_request("editMessageText", payload)
        if res.get("ok"):
            return res

        desc = (res.get("description") or "").lower()
        # If the user clicked the already-selected button, ignore "message is not modified"
        if "message is not modified" in desc:
            return res

        # If message cannot be edited, send a fresh message so the user always sees the update
        return self.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        return self.tg_request("answerCallbackQuery", payload)

    def send_chat_action(self, chat_id, action="typing"):
        return self.tg_request("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=10)

    def send_document(self, chat_id, filename, content_bytes, caption=None):
        payload = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        files = {"document": (filename, content_bytes, "text/plain")}
        return self.tg_request("sendDocument", payload=payload, files=files, timeout=60)

    def register_commands(self):
        commands = [
            {"command": "start", "description": "Start bot & open main menu"},
            {"command": "model", "description": "Switch AI model (GPT-4o, o1, Gemini, Claude, DeepSeek)"},
            {"command": "clear", "description": "Start a fresh chat conversation"},
            {"command": "resume", "description": "Resume your most recent chat"},
            {"command": "history", "description": "View & load recent chats"},
            {"command": "stats", "description": "Show usage statistics"},
            {"command": "export", "description": "Export current chat to a text file"},
            {"command": "system", "description": "Set custom system prompt"},
            {"command": "admin", "description": "Show admin & session status"},
            {"command": "debug", "description": "Toggle debug token info"},
            {"command": "help", "description": "Show all commands & usage"},
        ]
        self.tg_request("setMyCommands", {"commands": commands})

    # ------------------------------------------------------------------------
    # UI Keyboards & Formatting
    # ------------------------------------------------------------------------

    @staticmethod
    def escape_html(text):
        if text is None:
            return ""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def get_model_info_by_id(self, model_id):
        found = resolve_model_choice(model_id, self.config["models"])
        if found:
            return found
        return {"id": model_id, "name": model_id, "description": "Custom model", "tier": "PRO"}

    def get_effective_model(self, user_id, chat_id=None):
        state = self.db.get_or_create_user_state(user_id)
        if user_id in self.active_models:
            return self.active_models[user_id]
        if chat_id is not None and chat_id in self.active_models:
            return self.active_models[chat_id]
        return state["current_model"]

    def build_model_keyboard(self, current_model_id):
        inline_keyboard = []
        row = []
        for key, model in self.config["models"].items():
            is_selected = (model["id"] == current_model_id)
            prefix = "✅ " if is_selected else f"{key}. "
            badge = f"[{model['tier']}]"
            btn_text = f"{prefix}{model['name']} {badge}"
            row.append({
                "text": btn_text,
                "callback_data": f"model:{model['id']}"
            })
            if len(row) == 2:
                inline_keyboard.append(row)
                row = []
        if row:
            inline_keyboard.append(row)

        inline_keyboard.append([
            {"text": "🧹 New Chat", "callback_data": "action:clear"},
            {"text": "📜 History", "callback_data": "action:history"},
            {"text": "📊 Stats", "callback_data": "action:stats"},
        ])
        return {"inline_keyboard": inline_keyboard}

    def build_start_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "🧠 Switch AI Model", "callback_data": "action:models"},
                    {"text": "🧹 New Chat", "callback_data": "action:clear"},
                ],
                [
                    {"text": "📜 Chat History", "callback_data": "action:history"},
                    {"text": "⏯️ Resume Last", "callback_data": "action:resume"},
                ],
                [
                    {"text": "📊 Statistics", "callback_data": "action:stats"},
                    {"text": "📤 Export Chat", "callback_data": "action:export"},
                ],
                [
                    {"text": "🛡️ Admin Info", "callback_data": "action:admin"},
                    {"text": "❓ Help", "callback_data": "action:help"},
                ],
            ]
        }

    def build_history_keyboard(self, chats):
        buttons = []
        for chat_id, model, created_at, first_msg in chats:
            preview = (first_msg[:24] + "...") if first_msg and len(first_msg) > 24 else (first_msg or "Empty chat")
            buttons.append([{
                "text": f"📂 #{chat_id} ({model}): {preview}",
                "callback_data": f"load:{chat_id}"
            }])
        buttons.append([
            {"text": "🧹 Start New Chat", "callback_data": "action:clear"},
            {"text": "🧠 Switch Model", "callback_data": "action:models"},
        ])
        return {"inline_keyboard": buttons}

    # ------------------------------------------------------------------------
    # Command & Message Handlers
    # ------------------------------------------------------------------------

    def build_context_messages(self, chat_id, system_prompt):
        history = self.db.get_chat_history(chat_id, limit=50)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for role, content in history:
            if role != "system":
                messages.append({"role": role, "content": content})
        return messages

    def handle_start(self, chat_id, user_id, first_name="User"):
        self.awaiting_model_selection.pop(chat_id, None)
        self.awaiting_model_selection.pop(user_id, None)
        state = self.db.get_or_create_user_state(user_id)
        current_model = self.get_effective_model(user_id, chat_id)
        model_info = self.get_model_info_by_id(current_model)
        history_count = len(self.db.get_chat_history(state["current_chat_id"]))

        text = (
            f"👋 <b>Welcome, {self.escape_html(first_name)}!</b>\n\n"
            f"🤖 <b>AI Chat Bot</b> — <i>Admin Mode (All PRO Models Unlocked)</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 <b>Current Model:</b> <code>{self.escape_html(model_info['name'])}</code> (<code>{self.escape_html(model_info['id'])}</code>)\n"
            f"🔹 <b>Active Chat ID:</b> <code>#{state['current_chat_id']}</code> ({history_count} messages)\n"
            f"🔹 <b>Session ID:</b> <code>{state['current_session_id']}</code>\n"
            f"🔹 <b>Subscription:</b> <code>BYPASSED (UNLIMITED)</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 <b>Send any message below to start chatting</b>, or tap <b>🧠 Switch AI Model</b> to change models:"
        )
        self.send_message(chat_id, text, reply_markup=self.build_start_keyboard())

    def handle_help(self, chat_id):
        text = (
            "📚 <b>AVAILABLE COMMANDS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• /start - Open main control panel\n"
            "• /model - Switch AI model (tap a button or type <code>/model 1-9</code>)\n"
            "• /clear - Start a new empty chat\n"
            "• /resume - Resume your most recent conversation\n"
            "• /history - List recent chats &amp; load with 1 tap\n"
            "• /load <code>&lt;id&gt;</code> - Load a specific chat by ID\n"
            "• /sessions - View your recent session IDs\n"
            "• /stats - View total sessions, chats &amp; messages\n"
            "• /export - Download current chat as a <code>.txt</code> file\n"
            "• /system <code>&lt;prompt&gt;</code> - Set a custom system prompt\n"
            "• /debug - Toggle token &amp; debug info on replies\n"
            "• /admin - View Admin &amp; PRO bypass status\n\n"
            "💡 <i>Tip: You can also switch models anytime with <code>/model 2</code> (GPT-4o), <code>/model 5</code> (Gemini 1.5 Flash), <code>/model 7</code> (Claude 3.5 Sonnet), or <code>/model 8</code> (DeepSeek R1)!</i>"
        )
        self.send_message(chat_id, text, reply_markup=self.build_start_keyboard())

    def handle_model_menu(self, chat_id, user_id, edit_msg_id=None):
        self.db.get_or_create_user_state(user_id)
        current_model = self.get_effective_model(user_id, chat_id)
        model_info = self.get_model_info_by_id(current_model)

        # Allow the user to either tap an inline button OR reply with 1-9 / model name
        self.awaiting_model_selection[chat_id] = True
        self.awaiting_model_selection[user_id] = True

        lines = [
            "🧠 <b>SELECT AI MODEL (ADMIN MODE - PRO UNLOCKED)</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for key, m in self.config["models"].items():
            mark = "✅" if m["id"] == current_model else f"[{key}]"
            lines.append(
                f"{mark} <b>{self.escape_html(m['name'])}</b> (<code>{self.escape_html(m['id'])}</code>)\n"
                f"    └ <i>{self.escape_html(m['description'])} • {m['tier']}</i>"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"Current: <b>{self.escape_html(model_info['name'])}</b> (<code>{self.escape_html(model_info['id'])}</code>)")
        lines.append("<i>Tap a model button below or reply with a number (1–9):</i>")

        text = "\n".join(lines)
        kb = self.build_model_keyboard(current_model)
        if edit_msg_id:
            self.edit_message(chat_id, edit_msg_id, text, reply_markup=kb)
        else:
            self.send_message(chat_id, text, reply_markup=kb)

    def handle_set_model(self, chat_id, user_id, model_choice, edit_msg_id=None):
        self.awaiting_model_selection.pop(chat_id, None)
        self.awaiting_model_selection.pop(user_id, None)

        matched = resolve_model_choice(model_choice, self.config["models"])
        if not matched:
            self.send_message(
                chat_id,
                "❌ Invalid model selection. Tap a button below or send <code>/model 1</code>–<code>/model 9</code>:",
                reply_markup=self.build_model_keyboard(self.get_effective_model(user_id, chat_id)),
            )
            return False

        chosen_model_id = matched["id"]
        self.active_models[user_id] = chosen_model_id
        self.active_models[chat_id] = chosen_model_id

        active_chat_id = self.db.switch_user_model(user_id, chosen_model_id, chat_id_Sync=chat_id)
        model_info = self.get_model_info_by_id(chosen_model_id)

        text = (
            f"✅ <b>Model Switched Successfully!</b>\n\n"
            f"🤖 <b>Active Model:</b> {self.escape_html(model_info['name'])} (<code>{self.escape_html(model_info['id'])}</code>)\n"
            f"⚡ <b>Tier:</b> {model_info['tier']} — {self.escape_html(model_info['description'])}\n"
            f"💬 <b>Chat ID:</b> <code>#{active_chat_id}</code>\n\n"
            f"Send your message now to chat with <b>{self.escape_html(model_info['name'])}</b>!"
        )
        kb = self.build_model_keyboard(chosen_model_id)
        if edit_msg_id:
            self.edit_message(chat_id, edit_msg_id, text, reply_markup=kb)
        else:
            self.send_message(chat_id, text, reply_markup=kb)
        return True

    def handle_clear(self, chat_id, user_id):
        self.awaiting_model_selection.pop(chat_id, None)
        self.awaiting_model_selection.pop(user_id, None)
        state = self.db.get_or_create_user_state(user_id)
        current_model = self.get_effective_model(user_id, chat_id)
        new_chat_id = self.db.create_chat(
            state["current_session_id"],
            current_model,
            user_id=user_id,
            system_prompt=state["system_prompt"]
        )
        self.db.update_user_state(user_id, current_chat_id=new_chat_id, current_model=current_model)
        if chat_id != user_id:
            self.db.update_user_state(chat_id, current_chat_id=new_chat_id, current_model=current_model)
        model_info = self.get_model_info_by_id(current_model)
        self.send_message(
            chat_id,
            f"🧹 <b>Chat cleared!</b>\nStarted fresh conversation <code>#{new_chat_id}</code> with model <b>{self.escape_html(model_info['name'])}</b> (<code>{self.escape_html(current_model)}</code>)."
        )

    def handle_history(self, chat_id, user_id):
        chats = self.db.get_recent_chats(limit=10, user_id=user_id)
        if not chats:
            self.send_message(chat_id, "📭 No chat history found yet. Send a message to begin!")
            return

        lines = ["📜 <b>RECENT CHATS</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
        for cid, model, created_at, first_msg in chats:
            preview = (first_msg[:45] + "...") if first_msg and len(first_msg) > 45 else (first_msg or "(No user messages yet)")
            lines.append(
                f"• <b>ID #{cid}</b> | <code>{self.escape_html(model)}</code> | {self.escape_html(created_at)}\n"
                f"  <i>{self.escape_html(preview)}</i>"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━\nTap a chat below to load and continue it:")
        self.send_message(chat_id, "\n".join(lines), reply_markup=self.build_history_keyboard(chats))

    def handle_load_chat(self, chat_id, user_id, target_chat_id):
        chat_info = self.db.get_chat_by_id(target_chat_id, user_id=user_id)
        if not chat_info:
            chat_info = self.db.get_chat_by_id(target_chat_id)
        if not chat_info:
            self.send_message(chat_id, f"❌ Chat <code>#{target_chat_id}</code> not found.")
            return

        cid, model, created_at, sys_prompt = chat_info
        history = self.db.get_chat_history(cid, limit=50)
        self.active_models[user_id] = model
        self.active_models[chat_id] = model
        self.db.update_user_state(
            user_id,
            current_chat_id=cid,
            current_model=model,
            system_prompt=sys_prompt or DEFAULT_SYSTEM_PROMPT
        )
        if chat_id != user_id:
            self.db.update_user_state(
                chat_id,
                current_chat_id=cid,
                current_model=model,
                system_prompt=sys_prompt or DEFAULT_SYSTEM_PROMPT
            )

        lines = [
            f"📂 <b>Loaded Chat #{cid}</b>",
            f"🤖 <b>Model:</b> <code>{self.escape_html(model)}</code> | <b>Messages:</b> {len(history)}",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        if history:
            for role, content in history[-5:]:
                icon = "👤 You" if role == "user" else "🤖 AI"
                snippet = (content[:100] + "...") if len(content) > 100 else content
                lines.append(f"<b>{icon}:</b> {self.escape_html(snippet)}")
        else:
            lines.append("<i>(No messages in this chat yet)</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━\n💬 Send a message to continue this conversation!")
        self.send_message(chat_id, "\n".join(lines))

    def handle_resume(self, chat_id, user_id):
        chats = self.db.get_recent_chats(limit=1, user_id=user_id)
        if not chats:
            chats = self.db.get_recent_chats(limit=1)
        if not chats:
            self.send_message(chat_id, "📭 No previous chats found to resume.")
            return
        self.handle_load_chat(chat_id, user_id, chats[0][0])

    def handle_sessions(self, chat_id, user_id):
        sessions = self.db.get_all_sessions(user_id=user_id)
        if not sessions:
            sessions = self.db.get_all_sessions()
        if not sessions:
            self.send_message(chat_id, "📭 No sessions recorded yet.")
            return

        lines = ["🗂️ <b>RECENT SESSIONS</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
        for sid, model, created, ended in sessions[:15]:
            lines.append(f"• <code>{self.escape_html(sid)}</code> | <code>{self.escape_html(model)}</code> | {self.escape_html(created)}")
        self.send_message(chat_id, "\n".join(lines))

    def handle_stats(self, chat_id, user_id):
        sessions = self.db.get_all_sessions(user_id=user_id)
        recent_chats = self.db.get_recent_chats(limit=100, user_id=user_id)
        total_messages = 0
        for c in recent_chats:
            total_messages += len(self.db.get_chat_history(c[0], limit=1000))

        state = self.db.get_or_create_user_state(user_id)
        current_model = self.get_effective_model(user_id, chat_id)
        text = (
            "📊 <b>BOT USAGE STATISTICS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Your Sessions:</b> {len(sessions)}\n"
            f"• <b>Your Chats:</b> {len(recent_chats)}\n"
            f"• <b>Total Messages:</b> {total_messages}\n"
            f"• <b>Active Chat ID:</b> <code>#{state['current_chat_id']}</code>\n"
            f"• <b>Active Model:</b> <code>{self.escape_html(current_model)}</code>\n"
            f"• <b>Database:</b> <code>{self.escape_html(self.config['db_path'])}</code>"
        )
        self.send_message(chat_id, text)

    def handle_export(self, chat_id, user_id):
        state = self.db.get_or_create_user_state(user_id)
        current_model = self.get_effective_model(user_id, chat_id)
        history = self.db.get_chat_history(state["current_chat_id"], limit=1000)
        if not history:
            self.send_message(chat_id, "📭 No messages in the current chat to export yet.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{state['current_chat_id']}_{timestamp}.txt"

        buf = [
            f"Chat Export - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Chat ID: #{state['current_chat_id']}",
            f"Model: {current_model}",
            f"System Prompt: {state['system_prompt']}",
            "=" * 60,
            "",
        ]
        for role, content in history:
            buf.append(f"[{role.upper()}]:\n{content}\n")

        content_bytes = "\n".join(buf).encode("utf-8")
        self.send_document(
            chat_id,
            filename,
            content_bytes,
            caption=f"📤 Exported Chat #{state['current_chat_id']} ({len(history)} messages)"
        )

    def handle_system_prompt(self, chat_id, user_id, prompt_text):
        state = self.db.get_or_create_user_state(user_id)
        if not prompt_text:
            self.send_message(
                chat_id,
                f"⚙️ <b>Current System Prompt:</b>\n<code>{self.escape_html(state['system_prompt'])}</code>\n\n"
                f"To update it, send:\n<code>/system You are an expert coding assistant.</code>"
            )
            return

        self.db.update_user_state(user_id, system_prompt=prompt_text)
        if chat_id != user_id:
            self.db.update_user_state(chat_id, system_prompt=prompt_text)
        self.send_message(
            chat_id,
            f"✅ <b>System prompt updated!</b>\n<code>{self.escape_html(prompt_text)}</code>"
        )

    def handle_debug(self, chat_id, user_id):
        state = self.db.get_or_create_user_state(user_id)
        new_val = 0 if state["debug_mode"] else 1
        self.db.update_user_state(user_id, debug_mode=new_val)
        if chat_id != user_id:
            self.db.update_user_state(chat_id, debug_mode=new_val)
        status = "ON ✅" if new_val else "OFF ❌"
        self.send_message(chat_id, f"🛠️ <b>Debug Mode:</b> {status}")

    def handle_admin(self, chat_id, user_id):
        state = self.db.get_or_create_user_state(user_id)
        current_model = self.get_effective_model(user_id, chat_id)
        text = (
            "🛡️ <b>ADMIN &amp; CLIENT INFO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Mode:</b> {'ADMIN' if self.config.get('admin_mode') else 'USER'}\n"
            f"• <b>Subscription:</b> {'BYPASSED' if self.config.get('bypass_subscription') else 'ACTIVE'}\n"
            f"• <b>Messages:</b> {'UNLIMITED' if self.config.get('unlimited_messages') else 'LIMITED'}\n"
            f"• <b>Content Filter:</b> {'DISABLED' if not self.config.get('content_filter_enabled') else 'ENABLED'}\n"
            f"• <b>Current Model:</b> <code>{self.escape_html(current_model)}</code>\n"
            f"• <b>Active Chat ID:</b> <code>#{state['current_chat_id']}</code>\n"
            f"• <b>Session ID:</b> <code>{self.escape_html(state['current_session_id'])}</code>\n"
            f"• <b>Debug Mode:</b> {'ON' if state['debug_mode'] else 'OFF'}"
        )
        self.send_message(chat_id, text)

    def handle_user_chat_message(self, chat_id, user_id, text, message_id=None):
        state = self.db.get_or_create_user_state(user_id)
        active_chat_id = state["current_chat_id"]
        model = self.get_effective_model(user_id, chat_id)
        system_prompt = state["system_prompt"]

        # Keep DB chat model synchronized with the active model
        self.db.update_chat_model(active_chat_id, model)

        # Save user message in SQLite
        user_msg_row_id = self.db.add_message(active_chat_id, "user", text)

        # Build context from history
        messages = self.build_context_messages(active_chat_id, system_prompt)

        # Show typing status in Telegram
        self.send_chat_action(chat_id, "typing")

        # Call Firebase Cloud Function endpoint with selected model
        response = self.client.send_message(
            messages,
            model,
            temperature=0.7,
            max_tokens=1000
        )

        if response and response.get("content"):
            assistant_msg = response["content"]
            tokens = response.get("tokens", {}).get("total", 0)
            used_model = model
            model_info = self.get_model_info_by_id(used_model)

            self.db.add_message(active_chat_id, "assistant", assistant_msg, tokens)

            reply_text = self.escape_html(assistant_msg)
            if state["debug_mode"]:
                footer_parts = [f"Model: {model_info['name']} ({used_model})"]
                if tokens > 0:
                    footer_parts.append(f"Tokens: {tokens}")
                reply_text += f"\n\n<i>[{' | '.join(footer_parts)}]</i>"

            self.send_message(chat_id, reply_text, reply_to_message_id=message_id)
        else:
            # Remove unanswered user message so it does not corrupt alternating user/assistant history
            if user_msg_row_id:
                self.db.delete_message(user_msg_row_id)
            self.send_message(
                chat_id,
                "⚠️ <b>No response received from AI endpoint.</b> Please try again or switch models with /model.",
                reply_to_message_id=message_id
            )

    # ------------------------------------------------------------------------
    # Update Dispatcher & Long-Polling Loop
    # ------------------------------------------------------------------------

    def process_update(self, update):
        # 1. Handle Inline Button Callback Queries
        if "callback_query" in update:
            cb = update["callback_query"]
            cb_id = cb["id"]
            user = cb.get("from", {})
            msg = cb.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            user_id = user.get("id") or chat_id or 0
            msg_id = msg.get("message_id")
            data = (cb.get("data") or "").strip()

            if not chat_id:
                self.answer_callback_query(cb_id)
                return

            if data.startswith("model:"):
                model_id = data.split(":", 1)[1].strip()
                matched = resolve_model_choice(model_id, self.config["models"])
                label = matched["name"] if matched else model_id
                self.answer_callback_query(cb_id, f"✅ Switched to {label}")
                self.handle_set_model(chat_id, user_id, model_id, edit_msg_id=msg_id)

            elif data.startswith("load:"):
                try:
                    target_id = int(data.split(":", 1)[1])
                    self.answer_callback_query(cb_id, f"Loaded chat #{target_id}")
                    self.handle_load_chat(chat_id, user_id, target_id)
                except ValueError:
                    self.answer_callback_query(cb_id, "Invalid chat ID")

            elif data.startswith("action:"):
                action = data.split(":", 1)[1]
                self.answer_callback_query(cb_id)
                if action == "models":
                    self.handle_model_menu(chat_id, user_id, edit_msg_id=msg_id)
                elif action == "clear":
                    self.handle_clear(chat_id, user_id)
                elif action == "history":
                    self.handle_history(chat_id, user_id)
                elif action == "resume":
                    self.handle_resume(chat_id, user_id)
                elif action == "stats":
                    self.handle_stats(chat_id, user_id)
                elif action == "export":
                    self.handle_export(chat_id, user_id)
                elif action == "admin":
                    self.handle_admin(chat_id, user_id)
                elif action == "help":
                    self.handle_help(chat_id)
            else:
                self.answer_callback_query(cb_id)
            return

        # 2. Handle Standard Text Messages & Commands
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat_id = message.get("chat", {}).get("id")
        user = message.get("from", {})
        user_id = user.get("id") or chat_id
        first_name = user.get("first_name", "User")
        message_id = message.get("message_id")
        text = (message.get("text") or "").strip()

        if not chat_id or not text:
            return

        # Handle slash commands
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            raw_cmd = parts[0].lower()
            command = raw_cmd.split("@")[0]
            args = parts[1].strip() if len(parts) > 1 else ""

            if command == "/start":
                self.handle_start(chat_id, user_id, first_name=first_name)
                return
            elif command == "/help":
                self.handle_help(chat_id)
                return
            elif command in ("/model", "/models", "/switch"):
                if args:
                    self.handle_set_model(chat_id, user_id, args)
                else:
                    self.handle_model_menu(chat_id, user_id)
                return
            elif command.startswith("/model") and len(command) > 6:
                # Support /model1, /model_2, etc.
                suffix = command[6:].lstrip("_")
                self.handle_set_model(chat_id, user_id, suffix)
                return
            elif command == "/clear":
                self.handle_clear(chat_id, user_id)
                return
            elif command == "/resume":
                self.handle_resume(chat_id, user_id)
                return
            elif command == "/history":
                self.handle_history(chat_id, user_id)
                return
            elif command == "/load":
                if args.isdigit():
                    self.handle_load_chat(chat_id, user_id, int(args))
                else:
                    self.send_message(chat_id, "⚠️ Usage: <code>/load &lt;chat_id&gt;</code>")
                return
            elif command == "/sessions":
                self.handle_sessions(chat_id, user_id)
                return
            elif command == "/stats":
                self.handle_stats(chat_id, user_id)
                return
            elif command == "/export":
                self.handle_export(chat_id, user_id)
                return
            elif command == "/system":
                self.handle_system_prompt(chat_id, user_id, args)
                return
            elif command == "/debug":
                self.handle_debug(chat_id, user_id)
                return
            elif command == "/admin":
                self.handle_admin(chat_id, user_id)
                return
            else:
                # Check if slash command itself is a model shortcut like /1, /2, /gpt4o, /gemini, /claude
                matched_shortcut = resolve_model_choice(command[1:], self.config["models"])
                if matched_shortcut:
                    self.handle_set_model(chat_id, user_id, matched_shortcut["id"])
                    return
                self.send_message(chat_id, f"❓ Unknown command: <code>{self.escape_html(command)}</code>. Type /help for all commands.")
                return

        # If the user just opened /model and replied with a number (1-9) or model name, switch the model
        if self.awaiting_model_selection.get(chat_id) or self.awaiting_model_selection.get(user_id):
            matched = resolve_model_choice(text, self.config["models"])
            if matched:
                self.handle_set_model(chat_id, user_id, matched["id"])
                return
            self.awaiting_model_selection.pop(chat_id, None)
            self.awaiting_model_selection.pop(user_id, None)

        # Regular user chat message -> process in worker thread so polling stays responsive
        threading.Thread(
            target=self.handle_user_chat_message,
            args=(chat_id, user_id, text, message_id),
            daemon=True
        ).start()

    def run(self):
        print(f"{self.config['colors']['bold']}============================================")
        print("  AI CHAT BOT - Telegram Bot Mode (Railway)")
        print("  [ADMIN MODE] All PRO features unlocked")
        print(f"============================================{self.config['colors']['reset']}")

        me = self.tg_request("getMe")
        if not me.get("ok"):
            print(f"{self.config['colors']['error']}[!] Invalid API_BOT token or Telegram unreachable: {me}{self.config['colors']['reset']}")
            return

        bot_info = me.get("result", {})
        print(f"{self.config['colors']['assistant']}[*] Connected as @{bot_info.get('username')} (ID: {bot_info.get('id')}){self.config['colors']['reset']}")

        # Clear any active webhook so long-polling works cleanly on Railway
        self.tg_request("deleteWebhook", {"drop_pending_updates": False})
        self.register_commands()
        start_railway_health_server()

        print(f"{self.config['colors']['system']}[*] Polling for Telegram messages...{self.config['colors']['reset']}")

        while self.running:
            try:
                res = self.tg_request(
                    "getUpdates",
                    {
                        "offset": self.offset,
                        "timeout": 25,
                        "allowed_updates": ["message", "edited_message", "callback_query"],
                    },
                    timeout=35
                )

                if res and res.get("ok"):
                    for update in res.get("result", []):
                        self.offset = update["update_id"] + 1
                        try:
                            self.process_update(update)
                        except Exception as err:
                            print(f"{self.config['colors']['error']}[!] Update handler error: {err}{self.config['colors']['reset']}")
                else:
                    time.sleep(3)

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"{self.config['colors']['error']}[!] Polling loop error: {e}{self.config['colors']['reset']}")
                time.sleep(3)

        self.db.close()


# ============================================================================
# CLI FALLBACK APPLICATION (INTERACTIVE TERMINAL / QUICKEDIT PREVIEW MODE)
# ============================================================================

class ChatApp:
    """Interactive terminal application (also supports /model switching mid-chat)."""

    def __init__(self):
        self.config = CONFIG
        self.db = ChatDatabase(self.config["db_path"])
        self.client = ChatClient(self.config)
        self.current_chat_id = None
        self.current_session_id = None
        self.current_model = self.config.get("default_model", "gpt-4o")
        self.messages = []
        self.running = True

        try:
            signal.signal(signal.SIGINT, self.signal_handler)
        except ValueError:
            pass

    def signal_handler(self, sig, frame):
        print(f"\n\n{self.config['colors']['system']}[*] Shutting down gracefully...{self.config['colors']['reset']}")
        self.running = False
        self.cleanup()
        sys.exit(0)

    def print_banner(self):
        banner = f"""
{self.config['colors']['bold']}============================================
  AI CHAT BOT - Firebase Chat Client
  Reverse Engineered from Android App v1.5.6
  [ADMIN MODE] All PRO features unlocked
============================================{self.config['colors']['reset']}
"""
        print(banner)

    def select_model(self, non_interactive_default=False):
        print(f"\n{self.config['colors']['bold']}{'='*60}")
        print("  SELECT MODEL (ADMIN MODE - ALL PRO FEATURES UNLOCKED)")
        print(f"{'='*60}{self.config['colors']['reset']}\n")

        for key, model in self.config["models"].items():
            tier_color = self.config['colors']['assistant'] if model['tier'] == 'PRO' else self.config['colors']['system']
            print(f"  {self.config['colors']['bold']}[{key}]{self.config['colors']['reset']} {model['name']} ({model['id']})")
            print(f"      {model['description']} [{tier_color}{model['tier']}{self.config['colors']['reset']}]")
            print()

        default_key = "2"
        print(f"  {self.config['colors']['system']}[default: {default_key} - GPT-4o (Best Model)]{self.config['colors']['reset']}\n")

        if non_interactive_default or not sys.stdin or not sys.stdin.isatty():
            model = self.config["models"][default_key]
            print(f"  {self.config['colors']['assistant']}Auto-selected best model: {model['name']} ({model['id']}){self.config['colors']['reset']}")
            return model["id"]

        while True:
            try:
                choice = input(f"  {self.config['colors']['user']}Enter choice (1-{len(self.config['models'])}): {self.config['colors']['reset']}").strip()
            except EOFError:
                choice = default_key
            if not choice:
                choice = default_key
            matched = resolve_model_choice(choice, self.config["models"])
            if matched:
                print(f"\n  {self.config['colors']['assistant']}Selected: {matched['name']} ({matched['id']}){self.config['colors']['reset']}\n")
                return matched["id"]
            print(f"  {self.config['colors']['error']}Invalid choice. Please enter a valid number or model name.{self.config['colors']['reset']}")

    def cleanup(self):
        if self.db:
            self.db.close()

    def run(self):
        self.print_banner()
        self.current_model = self.select_model()
        self.current_session_id = str(uuid.uuid4())[:8]
        self.db.create_session(self.current_session_id, self.current_model)
        self.current_chat_id = self.db.create_chat(self.current_session_id, self.current_model)
        self.messages = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}]

        if not sys.stdin or not sys.stdin.isatty():
            print(f"{self.config['colors']['system']}[*] Non-interactive preview detected. Set API_BOT in environment to run Telegram Bot mode.{self.config['colors']['reset']}")
            self.cleanup()
            return

        print(f"{self.config['colors']['system']}Commands: /model [1-9], /clear, /quit{self.config['colors']['reset']}\n")

        while self.running:
            try:
                user_input = input(f"{self.config['colors']['user']}You ({self.current_model}): {self.config['colors']['reset']}").strip()
                if not user_input:
                    continue
                if user_input.lower() in ["/quit", "/exit", "/q"]:
                    break
                if user_input.lower().startswith("/model"):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) > 1:
                        matched = resolve_model_choice(parts[1], self.config["models"])
                        if matched:
                            self.current_model = matched["id"]
                            print(f"\n{self.config['colors']['assistant']}[*] Switched model to: {matched['name']} ({matched['id']}){self.config['colors']['reset']}\n")
                        else:
                            print(f"{self.config['colors']['error']}[!] Unknown model: {parts[1]}{self.config['colors']['reset']}")
                    else:
                        self.current_model = self.select_model()
                    self.current_chat_id = self.db.create_chat(self.current_session_id, self.current_model)
                    self.messages = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}]
                    continue
                if user_input.lower() == "/clear":
                    self.current_chat_id = self.db.create_chat(self.current_session_id, self.current_model)
                    self.messages = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}]
                    print(f"{self.config['colors']['system']}[*] Chat history cleared.{self.config['colors']['reset']}\n")
                    continue

                self.messages.append({"role": "user", "content": user_input})
                row_id = self.db.add_message(self.current_chat_id, "user", user_input)
                response = self.client.send_message(self.messages, self.current_model)
                if response and response.get("content"):
                    assistant_msg = response["content"]
                    tokens = response.get("tokens", {}).get("total", 0)
                    self.messages.append({"role": "assistant", "content": assistant_msg})
                    self.db.add_message(self.current_chat_id, "assistant", assistant_msg, tokens)
                    print(f"\n{self.config['colors']['assistant']}Assistant ({self.current_model}): {assistant_msg}{self.config['colors']['reset']}\n")
                else:
                    self.messages.pop()
                    self.db.delete_message(row_id)
                    print(f"{self.config['colors']['error']}[!] No response received.{self.config['colors']['reset']}")
            except (EOFError, KeyboardInterrupt):
                break
        self.cleanup()


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    """
    Main entry point.
    - If `API_BOT` environment variable (or .env) is set, starts TelegramBotApp (for Railway).
    - If `API_BOT` is not set (e.g. local terminal or QuickEdit preview), runs cleanly
      with GPT-4o auto-selected as the best default model and zero non-zero exit error.
    """
    token = load_env_token() or CONFIG.get("api_bot", "").strip()
    if token:
        bot = TelegramBotApp(token, CONFIG)
        bot.run()
    else:
        print(
            f"{CONFIG['colors']['system']}[*] 'API_BOT' environment variable not set — starting in local/preview mode (Default Model: GPT-4o).{CONFIG['colors']['reset']}\n"
            f"{CONFIG['colors']['system']}[*] On Railway, set API_BOT=<your_bot_token> to run as a Telegram Bot.{CONFIG['colors']['reset']}"
        )
        app = ChatApp()
        app.run()


if __name__ == "__main__":
    main()