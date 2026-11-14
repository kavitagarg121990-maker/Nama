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

API_BOT = os.environ.get("API_BOT", "").strip()

CONFIG = {
    # Telegram Bot Token from environment variable API_BOT
    "api_bot": API_BOT,

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

    # Available models (extracted from APK decompilation - with correct API IDs)
    "models": {
        "1": {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "description": "Fast & cheap", "tier": "FREE"},
        "2": {"id": "gpt-4o", "name": "GPT-4o", "description": "Most capable", "tier": "PRO"},
        "3": {"id": "o1", "name": "GPT-01", "description": "Reasoning model", "tier": "PRO"},
        "4": {"id": "o1-mini", "name": "GPT-01 Mini", "description": "Fast reasoning", "tier": "PRO"},
        "5": {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "description": "Google fast", "tier": "PRO"},
        "6": {"id": "claude-3-5-haiku-latest", "name": "Claude 3.5 Haiku", "description": "Anthropic fast", "tier": "PRO"},
        "7": {"id": "claude-3-5-sonnet-latest", "name": "Claude 3.5 Sonnet", "description": "Anthropic smart", "tier": "PRO"},
        # "8": {"id": "deepseek-r1", "name": "DeepSeek R1", "description": "Deep reasoning", "tier": "PRO"},
        # "9": {"id": "deepseek-v3", "name": "DeepSeek V3", "description": "Deep fast", "tier": "PRO"},
    },

    # Admin/Owner privileges (you are the app owner)
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

# ============================================================================
# DATABASE MANAGER
# ============================================================================

class ChatDatabase:
    """SQLite database for chat history and per-user Telegram state persistence."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    current_chat_id INTEGER,
                    current_session_id TEXT,
                    current_model TEXT DEFAULT 'gpt-4o-mini',
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

            self.conn.commit()

    def get_or_create_user_state(self, user_id, default_model="gpt-4o-mini"):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT user_id, current_chat_id, current_session_id, current_model, system_prompt, debug_mode FROM user_state WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "current_chat_id": row[1],
                    "current_session_id": row[2],
                    "current_model": row[3] or default_model,
                    "system_prompt": row[4] or "You are a helpful AI assistant. Be concise and helpful.",
                    "debug_mode": bool(row[5]),
                }

            session_id = str(uuid.uuid4())[:8]
            cursor.execute(
                "INSERT INTO sessions (id, user_id, model) VALUES (?, ?, ?)",
                (session_id, user_id, default_model)
            )
            cursor.execute(
                "INSERT INTO chats (session_id, user_id, model) VALUES (?, ?, ?)",
                (session_id, user_id, default_model)
            )
            chat_id = cursor.lastrowid
            system_prompt = "You are a helpful AI assistant. Be concise and helpful."
            cursor.execute(
                """INSERT INTO user_state (user_id, current_chat_id, current_session_id, current_model, system_prompt, debug_mode)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (user_id, chat_id, session_id, default_model, system_prompt)
            )
            self.conn.commit()
            return {
                "user_id": user_id,
                "current_chat_id": chat_id,
                "current_session_id": session_id,
                "current_model": default_model,
                "system_prompt": system_prompt,
                "debug_mode": False,
            }

    def update_user_state(self, user_id, **kwargs):
        if not kwargs:
            return
        with self.lock:
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

    def create_session(self, session_id, model, user_id=0):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sessions (id, user_id, model) VALUES (?, ?, ?)",
                (session_id, user_id, model)
            )
            self.conn.commit()

    def create_chat(self, session_id, model, user_id=0, system_prompt="You are a helpful AI assistant. Be concise and helpful."):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO chats (session_id, user_id, model, system_prompt) VALUES (?, ?, ?, ?)",
                (session_id, user_id, model, system_prompt)
            )
            self.conn.commit()
            return cursor.lastrowid

    def add_message(self, chat_id, role, content, tokens=0):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO messages (chat_id, role, content, tokens_used) VALUES (?, ?, ?, ?)",
                (chat_id, role, content, tokens)
            )
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
                       ORDER BY c.created_at DESC LIMIT ?""",
                    (user_id, limit)
                )
            else:
                cursor.execute(
                    """SELECT c.id, c.model, c.created_at,
                              (SELECT content FROM messages WHERE chat_id = c.id AND role = 'user' ORDER BY id LIMIT 1) as first_msg
                       FROM chats c ORDER BY c.created_at DESC LIMIT ?""",
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
    Replicates the Android app's network behavior.
    """

    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.fid = FirebaseTokenGenerator.generate_firebase_instance_id()
        self.fcm_token = FirebaseTokenGenerator.generate_fcm_token(self.fid)
        self.session_token = FirebaseTokenGenerator.generate_session_token()
        self.setup_session()

    def setup_session(self):
        """Configure HTTP session with proper headers."""
        self.session.headers.update({
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "user-agent": self.config["user_agent"],
            "firebase-instance-id-token": self.fcm_token,
            # Firebase identifiers
            "X-Android-Package": self.config["app_package"],
            "X-Android-Cert": "085878EDBA0200080CA6CD94242C6ADBE21F28BA",
        })

    def format_message_content(self, text):
        """
        Format message content as expected by the Cloud Function.
        From HAR analysis, content is an array of objects.
        """
        return [{"type": "text", "text": text}]

    def apply_content_filter(self, text):
        """
        Apply content filter like the Android app does.
        The app injects 80+ restricted words into the system prompt.
        """
        if not self.config.get("content_filter_enabled"):
            return text
        return text

    def build_request_payload(self, messages, model, temperature=0.7, max_tokens=1000):
        """Build the request payload matching the Android app format (wrapped in 'data')."""
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
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
                "content": msg["content"]
            })

        return {
            "model": model,
            "messages": formatted_messages,
        }

    def send_message(self, messages, model, temperature=0.7, max_tokens=1000):
        """
        Send message to Firebase Cloud Function.
        Tries multiple formats to find the working one.
        Returns the response dict or None on error.
        """
        payload = self.build_request_payload(messages, model, temperature, max_tokens)

        try:
            print(f"{self.config['colors']['system']}[*] Sending to Cloud Function (model: {model})...{self.config['colors']['reset']}")

            response = self.session.post(
                self.config["chat_endpoint"],
                json=payload,
                timeout=60
            )

            if response.status_code == 200:
                data = response.json()
                parsed = self.parse_response(data)
                if parsed:
                    return parsed

            print(f"{self.config['colors']['system']}[*] Format 1 failed ({response.status_code}). Trying Format 2...{self.config['colors']['reset']}")

            payload_v2 = self.build_request_payload_v2(messages, model)
            response_v2 = self.session.post(
                self.config["chat_endpoint"],
                json=payload_v2,
                timeout=60
            )

            if response_v2.status_code == 200:
                data = response_v2.json()
                parsed = self.parse_response(data)
                if parsed:
                    return parsed

            print(f"{self.config['colors']['system']}[*] Format 2 failed ({response_v2.status_code}). Trying gpt-4o-mini fallback...{self.config['colors']['reset']}")

            if model != "gpt-4o-mini":
                payload_fallback = self.build_request_payload(messages, "gpt-4o-mini", temperature, max_tokens)
                response_fallback = self.session.post(
                    self.config["chat_endpoint"],
                    json=payload_fallback,
                    timeout=60
                )

                if response_fallback.status_code == 200:
                    data = response_fallback.json()
                    print(f"{self.config['colors']['system']}[*] Using gpt-4o-mini as fallback{self.config['colors']['reset']}")
                    parsed = self.parse_response(data)
                    if parsed:
                        parsed["fallback_used"] = True
                        return parsed

            print(f"{self.config['colors']['error']}[!] All formats failed. Last error: {response.text[:300]}{self.config['colors']['reset']}")
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

    def parse_response(self, data):
        """Parse the Cloud Function response."""
        try:
            result = data.get("result", data)
            if isinstance(result, dict):
                choices = result.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    usage = result.get("usage", {})

                    return {
                        "content": message.get("content", ""),
                        "role": message.get("role", "assistant"),
                        "model": result.get("model", "unknown"),
                        "finish_reason": choices[0].get("finish_reason", "unknown"),
                        "tokens": {
                            "prompt": usage.get("prompt_tokens", 0),
                            "completion": usage.get("completion_tokens", 0),
                            "total": usage.get("total_tokens", 0),
                        },
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
        self.http = requests.Session()
        self.running = True
        self.offset = 0

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, sig, frame):
        print(f"\n{self.config['colors']['system']}[*] Shutting down Telegram bot gracefully...{self.config['colors']['reset']}")
        self.running = False
        self.db.close()
        sys.exit(0)

    # ------------------------------------------------------------------------
    # Telegram API Helpers
    # ------------------------------------------------------------------------

    def tg_request(self, method, payload=None, files=None, timeout=35):
        url = f"{self.base_url}/{method}"
        try:
            if files:
                resp = self.http.post(url, data=payload, files=files, timeout=timeout)
            else:
                resp = self.http.post(url, json=payload, timeout=timeout)
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
                # Fallback to plain text if HTML/Markdown entities in AI output fail to parse
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
        if not res.get("ok") and parse_mode:
            payload.pop("parse_mode", None)
            res = self.tg_request("editMessageText", payload)
        return res

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
            {"command": "model", "description": "Select AI model (GPT-4o, o1, Gemini, Claude)"},
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
        for key, info in self.config["models"].items():
            if info["id"] == model_id:
                return info
        return {"id": model_id, "name": model_id, "description": "Custom model", "tier": "PRO"}

    def build_model_keyboard(self, current_model_id):
        inline_keyboard = []
        row = []
        for key, model in self.config["models"].items():
            is_selected = (model["id"] == current_model_id)
            prefix = "✅ " if is_selected else "🤖 "
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
                    {"text": "🧠 Select Model", "callback_data": "action:models"},
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
            {"text": "🧠 Select Model", "callback_data": "action:models"},
        ])
        return {"inline_keyboard": buttons}

    # ------------------------------------------------------------------------
    # Command & Message Handlers
    # ------------------------------------------------------------------------

    def build_context_messages(self, chat_id, system_prompt):
        history = self.db.get_chat_history(chat_id, limit=50)
        messages = [{"role": "system", "content": system_prompt}]
        for role, content in history:
            if role != "system":
                messages.append({"role": role, "content": content})
        return messages

    def handle_start(self, chat_id, user_id, first_name="User"):
        state = self.db.get_or_create_user_state(user_id)
        model_info = self.get_model_info_by_id(state["current_model"])
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
            f"💬 <b>Send any message below to start chatting</b>, or use the buttons to switch AI models and manage chats:"
        )
        self.send_message(chat_id, text, reply_markup=self.build_start_keyboard())

    def handle_help(self, chat_id):
        text = (
            "📚 <b>AVAILABLE COMMANDS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• /start - Open main control panel\n"
            "• /model - Select AI model (GPT-4o, o1, Gemini, Claude)\n"
            "• /clear - Start a new empty chat\n"
            "• /resume - Resume your most recent conversation\n"
            "• /history - List recent chats & load with 1 tap\n"
            "• /load <code>&lt;id&gt;</code> - Load a specific chat by ID\n"
            "• /sessions - View your recent session IDs\n"
            "• /stats - View total sessions, chats & messages\n"
            "• /export - Download current chat as a <code>.txt</code> file\n"
            "• /system <code>&lt;prompt&gt;</code> - Set a custom system prompt\n"
            "• /debug - Toggle token & debug info on replies\n"
            "• /admin - View Admin & PRO bypass status\n\n"
            "💡 <i>Tip: Just type any question or prompt directly to chat with the selected model!</i>"
        )
        self.send_message(chat_id, text, reply_markup=self.build_start_keyboard())

    def handle_model_menu(self, chat_id, user_id, edit_msg_id=None):
        state = self.db.get_or_create_user_state(user_id)
        current_model = state["current_model"]
        model_info = self.get_model_info_by_id(current_model)

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
        lines.append(f"Current: <b>{self.escape_html(model_info['name'])}</b>")

        text = "\n".join(lines)
        kb = self.build_model_keyboard(current_model)
        if edit_msg_id:
            self.edit_message(chat_id, edit_msg_id, text, reply_markup=kb)
        else:
            self.send_message(chat_id, text, reply_markup=kb)

    def handle_set_model(self, chat_id, user_id, model_choice, edit_msg_id=None):
        self.db.get_or_create_user_state(user_id)
        chosen_model_id = None

        if model_choice in self.config["models"]:
            chosen_model_id = self.config["models"][model_choice]["id"]
        else:
            for m in self.config["models"].values():
                if m["id"].lower() == model_choice.lower() or m["name"].lower() == model_choice.lower():
                    chosen_model_id = m["id"]
                    break

        if not chosen_model_id:
            self.send_message(chat_id, "❌ Invalid model selection. Use /model to choose from the menu.")
            return

        self.db.update_user_state(user_id, current_model=chosen_model_id)
        model_info = self.get_model_info_by_id(chosen_model_id)
        text = (
            f"✅ <b>Model Updated!</b>\n\n"
            f"🤖 <b>Selected:</b> {self.escape_html(model_info['name'])} (<code>{self.escape_html(model_info['id'])}</code>)\n"
            f"⚡ <b>Tier:</b> {model_info['tier']} — {self.escape_html(model_info['description'])}\n\n"
            f"Send your message now to chat with <b>{self.escape_html(model_info['name'])}</b>!"
        )
        if edit_msg_id:
            self.edit_message(chat_id, edit_msg_id, text, reply_markup=self.build_model_keyboard(chosen_model_id))
        else:
            self.send_message(chat_id, text, reply_markup=self.build_model_keyboard(chosen_model_id))

    def handle_clear(self, chat_id, user_id):
        state = self.db.get_or_create_user_state(user_id)
        new_chat_id = self.db.create_chat(
            state["current_session_id"],
            state["current_model"],
            user_id=user_id,
            system_prompt=state["system_prompt"]
        )
        self.db.update_user_state(user_id, current_chat_id=new_chat_id)
        self.send_message(
            chat_id,
            f"🧹 <b>Chat cleared!</b>\nStarted fresh conversation <code>#{new_chat_id}</code> with model <code>{self.escape_html(state['current_model'])}</code>."
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
            # Also check global chats if created in CLI mode (user_id=0)
            chat_info = self.db.get_chat_by_id(target_chat_id)
        if not chat_info:
            self.send_message(chat_id, f"❌ Chat <code>#{target_chat_id}</code> not found.")
            return

        cid, model, created_at, sys_prompt = chat_info
        history = self.db.get_chat_history(cid, limit=50)
        self.db.update_user_state(
            user_id,
            current_chat_id=cid,
            current_model=model,
            system_prompt=sys_prompt or "You are a helpful AI assistant. Be concise and helpful."
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
        text = (
            "📊 <b>BOT USAGE STATISTICS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Your Sessions:</b> {len(sessions)}\n"
            f"• <b>Your Chats:</b> {len(recent_chats)}\n"
            f"• <b>Total Messages:</b> {total_messages}\n"
            f"• <b>Active Chat ID:</b> <code>#{state['current_chat_id']}</code>\n"
            f"• <b>Active Model:</b> <code>{self.escape_html(state['current_model'])}</code>\n"
            f"• <b>Database:</b> <code>{self.escape_html(self.config['db_path'])}</code>"
        )
        self.send_message(chat_id, text)

    def handle_export(self, chat_id, user_id):
        state = self.db.get_or_create_user_state(user_id)
        history = self.db.get_chat_history(state["current_chat_id"], limit=1000)
        if not history:
            self.send_message(chat_id, "📭 No messages in the current chat to export yet.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{state['current_chat_id']}_{timestamp}.txt"

        buf = [
            f"Chat Export - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Chat ID: #{state['current_chat_id']}",
            f"Model: {state['current_model']}",
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
        self.send_message(
            chat_id,
            f"✅ <b>System prompt updated!</b>\n<code>{self.escape_html(prompt_text)}</code>"
        )

    def handle_debug(self, chat_id, user_id):
        state = self.db.get_or_create_user_state(user_id)
        new_val = 0 if state["debug_mode"] else 1
        self.db.update_user_state(user_id, debug_mode=new_val)
        status = "ON ✅" if new_val else "OFF ❌"
        self.send_message(chat_id, f"🛠️ <b>Debug Mode:</b> {status}")

    def handle_admin(self, chat_id, user_id):
        state = self.db.get_or_create_user_state(user_id)
        text = (
            "🛡️ <b>ADMIN &amp; CLIENT INFO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Mode:</b> {'ADMIN' if self.config.get('admin_mode') else 'USER'}\n"
            f"• <b>Subscription:</b> {'BYPASSED' if self.config.get('bypass_subscription') else 'ACTIVE'}\n"
            f"• <b>Messages:</b> {'UNLIMITED' if self.config.get('unlimited_messages') else 'LIMITED'}\n"
            f"• <b>Content Filter:</b> {'DISABLED' if not self.config.get('content_filter_enabled') else 'ENABLED'}\n"
            f"• <b>Current Model:</b> <code>{self.escape_html(state['current_model'])}</code>\n"
            f"• <b>Active Chat ID:</b> <code>#{state['current_chat_id']}</code>\n"
            f"• <b>Session ID:</b> <code>{self.escape_html(state['current_session_id'])}</code>\n"
            f"• <b>Debug Mode:</b> {'ON' if state['debug_mode'] else 'OFF'}"
        )
        self.send_message(chat_id, text)

    def handle_user_chat_message(self, chat_id, user_id, text, message_id=None):
        state = self.db.get_or_create_user_state(user_id)
        active_chat_id = state["current_chat_id"]
        model = state["current_model"]
        system_prompt = state["system_prompt"]

        # Save user message in SQLite
        self.db.add_message(active_chat_id, "user", text)

        # Build context from history
        messages = self.build_context_messages(active_chat_id, system_prompt)

        # Show typing status in Telegram
        self.send_chat_action(chat_id, "typing")

        # Call Firebase Cloud Function endpoint
        response = self.client.send_message(
            messages,
            model,
            temperature=0.7,
            max_tokens=1000
        )

        if response and response.get("content"):
            assistant_msg = response["content"]
            tokens = response.get("tokens", {}).get("total", 0)
            used_model = response.get("model") or model

            self.db.add_message(active_chat_id, "assistant", assistant_msg, tokens)

            reply_text = self.escape_html(assistant_msg)
            if state["debug_mode"] or response.get("fallback_used"):
                footer_parts = [f"Model: {used_model}"]
                if tokens > 0:
                    footer_parts.append(f"Tokens: {tokens}")
                if response.get("fallback_used"):
                    footer_parts.append("Fallback: gpt-4o-mini")
                reply_text += f"\n\n<i>[{' | '.join(footer_parts)}]</i>"

            self.send_message(chat_id, reply_text, reply_to_message_id=message_id)
        else:
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
            user_id = user.get("id", 0)
            msg = cb.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            msg_id = msg.get("message_id")
            data = cb.get("data", "")

            if not chat_id:
                self.answer_callback_query(cb_id)
                return

            if data.startswith("model:"):
                model_id = data.split(":", 1)[1]
                self.answer_callback_query(cb_id, f"Switched to {model_id}")
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
                    self.handle_model_menu(chat_id, user_id)
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
        user_id = user.get("id", chat_id)
        first_name = user.get("first_name", "User")
        message_id = message.get("message_id")
        text = (message.get("text") or "").strip()

        if not chat_id or not text:
            return

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            raw_cmd = parts[0].lower()
            # Strip @bot_username suffix if command sent in group
            command = raw_cmd.split("@")[0]
            args = parts[1].strip() if len(parts) > 1 else ""

            if command == "/start":
                self.handle_start(chat_id, user_id, first_name=first_name)
            elif command == "/help":
                self.handle_help(chat_id)
            elif command == "/model":
                if args:
                    self.handle_set_model(chat_id, user_id, args)
                else:
                    self.handle_model_menu(chat_id, user_id)
            elif command == "/clear":
                self.handle_clear(chat_id, user_id)
            elif command == "/resume":
                self.handle_resume(chat_id, user_id)
            elif command == "/history":
                self.handle_history(chat_id, user_id)
            elif command == "/load":
                if args.isdigit():
                    self.handle_load_chat(chat_id, user_id, int(args))
                else:
                    self.send_message(chat_id, "⚠️ Usage: <code>/load &lt;chat_id&gt;</code>")
            elif command == "/sessions":
                self.handle_sessions(chat_id, user_id)
            elif command == "/stats":
                self.handle_stats(chat_id, user_id)
            elif command == "/export":
                self.handle_export(chat_id, user_id)
            elif command == "/system":
                self.handle_system_prompt(chat_id, user_id, args)
            elif command == "/debug":
                self.handle_debug(chat_id, user_id)
            elif command == "/admin":
                self.handle_admin(chat_id, user_id)
            else:
                self.send_message(chat_id, f"❓ Unknown command: <code>{self.escape_html(command)}</code>. Type /help for all commands.")
            return

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
            sys.exit(1)

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
# CLI FALLBACK APPLICATION (ORIGINAL TERMINAL MODE)
# ============================================================================

class ChatApp:
    """Original terminal application class (used if run interactively without API_BOT)."""

    def __init__(self):
        self.config = CONFIG
        self.db = ChatDatabase(self.config["db_path"])
        self.client = None
        self.current_chat_id = None
        self.current_session_id = None
        self.current_model = None
        self.messages = []
        self.running = True

        signal.signal(signal.SIGINT, self.signal_handler)

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

    def select_model(self):
        print(f"\n{self.config['colors']['bold']}{'='*60}")
        print("  SELECT MODEL (ADMIN MODE - ALL PRO FEATURES UNLOCKED)")
        print(f"{'='*60}{self.config['colors']['reset']}\n")

        for key, model in self.config["models"].items():
            tier_color = self.config['colors']['assistant'] if model['tier'] == 'PRO' else self.config['colors']['system']
            print(f"  {self.config['colors']['bold']}[{key}]{self.config['colors']['reset']} {model['name']}")
            print(f"      {model['description']} [{tier_color}{model['tier']}{self.config['colors']['reset']}]")
            print()

        print(f"  {self.config['colors']['system']}[default: 1]{self.config['colors']['reset']}\n")

        while True:
            choice = input(f"  {self.config['colors']['user']}Enter choice (1-7): {self.config['colors']['reset']}").strip()
            if not choice:
                choice = "1"
            if choice in self.config["models"]:
                model = self.config["models"][choice]
                print(f"\n  {self.config['colors']['assistant']}Selected: {model['name']} ({model['id']}){self.config['colors']['reset']}")
                return model["id"]
            print(f"  {self.config['colors']['error']}Invalid choice. Please enter a valid number.{self.config['colors']['reset']}")

    def cleanup(self):
        if self.db:
            self.db.close()

    def run(self):
        self.print_banner()
        self.current_model = self.select_model()
        self.client = ChatClient(self.config)
        self.current_session_id = str(uuid.uuid4())[:8]
        self.db.create_session(self.current_session_id, self.current_model)
        self.current_chat_id = self.db.create_chat(self.current_session_id, self.current_model)
        self.messages = [{"role": "system", "content": "You are a helpful AI assistant. Be concise and helpful."}]

        while self.running:
            try:
                user_input = input(f"{self.config['colors']['user']}You: {self.config['colors']['reset']}").strip()
                if not user_input:
                    continue
                if user_input.lower() in ["/quit", "/exit", "/q"]:
                    break
                self.messages.append({"role": "user", "content": user_input})
                self.db.add_message(self.current_chat_id, "user", user_input)
                response = self.client.send_message(self.messages, self.current_model)
                if response and response.get("content"):
                    assistant_msg = response["content"]
                    tokens = response.get("tokens", {}).get("total", 0)
                    self.messages.append({"role": "assistant", "content": assistant_msg})
                    self.db.add_message(self.current_chat_id, "assistant", assistant_msg, tokens)
                    print(f"\n{self.config['colors']['assistant']}Assistant: {assistant_msg}{self.config['colors']['reset']}\n")
                else:
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
    Reads `API_BOT` environment variable to run as a Telegram Bot on Railway.
    """
    token = os.environ.get("API_BOT", "").strip()
    if not token:
        print(
            f"{CONFIG['colors']['error']}[!] Environment variable 'API_BOT' is not set!{CONFIG['colors']['reset']}\n"
            f"{CONFIG['colors']['system']}[*] On Railway, add a variable named API_BOT with your Telegram Bot Token from @BotFather.{CONFIG['colors']['reset']}"
        )
        sys.exit(1)

    bot = TelegramBotApp(token, CONFIG)
    bot.run()

if __name__ == "__main__":
    main()
