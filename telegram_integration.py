"""
Telegram Integration for Cursor Enhanced

This module provides Telegram bot integration, allowing cursor-enhanced to
receive and respond to messages via Telegram, similar to Runtime's Telegram channel.
"""

import os
import json
import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass

logger = logging.getLogger("cursor_enhanced.telegram")

# Scheduled notifications store path
SCHEDULE_STORE_PATH = os.path.expanduser("~/.cursor-enhanced/scheduled-notifications.json")
DEFAULT_SCHEDULE_CHECK_INTERVAL_SECONDS = 90

# Try to import telegram bot library
try:
    from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError as e:
    TELEGRAM_AVAILABLE = False
    Update = None
    Bot = None
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    Application = None
    CommandHandler = None
    MessageHandler = None
    CallbackQueryHandler = None
    filters = None
    ContextTypes = None
    logger.warning(f"python-telegram-bot not available: {e}. Install with: pip install python-telegram-bot")


# --- Scheduled notifications (cron-like) ---

def _load_schedule_store(path: str = SCHEDULE_STORE_PATH) -> List[Dict[str, Any]]:
    """Load schedule store; return list of notification entries."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("notifications") if isinstance(data, dict) else data
        return list(entries) if isinstance(entries, list) else []
    except Exception as e:
        logger.warning(f"Failed to load schedule store {path}: {e}")
        return []


def _save_schedule_store(entries: List[Dict[str, Any]], path: str = SCHEDULE_STORE_PATH) -> None:
    """Save schedule store (atomic write)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"notifications": entries}, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        logger.error(f"Failed to save schedule store {path}: {e}")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def schedule_add(
    schedule_type: str,
    message: str,
    time_spec: str,
    telegram_chat_id: Union[int, str] = "all",
    timezone_name: Optional[str] = None,
    path: str = SCHEDULE_STORE_PATH,
) -> str:
    """Add a scheduled notification. schedule_type: 'daily' | 'once'. time_spec: 'HH:MM' or ISO datetime. Returns id."""
    uid = str(uuid.uuid4())
    entry: Dict[str, Any] = {
        "id": uid,
        "schedule_type": schedule_type,
        "message": message,
        "telegram_chat_id": telegram_chat_id,
        "enabled": True,
    }
    if timezone_name:
        entry["timezone"] = timezone_name
    if schedule_type == "daily":
        entry["time"] = time_spec  # HH:MM
        entry["last_run"] = None
        entry["next_run"] = None
    else:
        entry["once_at"] = time_spec  # ISO datetime string
    entries = _load_schedule_store(path)
    entries.append(entry)
    _save_schedule_store(entries, path)
    logger.info(f"Scheduled notification added: id={uid}, type={schedule_type}, time_spec={time_spec}")
    return uid


def schedule_list(path: str = SCHEDULE_STORE_PATH) -> List[Dict[str, Any]]:
    """List all scheduled notifications."""
    return _load_schedule_store(path)


def schedule_remove(entry_id: str, path: str = SCHEDULE_STORE_PATH) -> bool:
    """Remove a scheduled notification by id. Returns True if removed."""
    entries = _load_schedule_store(path)
    new_entries = [e for e in entries if e.get("id") != entry_id]
    if len(new_entries) == len(entries):
        return False
    _save_schedule_store(new_entries, path)
    logger.info(f"Scheduled notification removed: id={entry_id}")
    return True


def schedule_update_enabled(entry_id: str, enabled: bool, path: str = SCHEDULE_STORE_PATH) -> bool:
    """Enable or disable a scheduled notification by id. Returns True if found and updated."""
    entries = _load_schedule_store(path)
    for e in entries:
        if e.get("id") == entry_id:
            e["enabled"] = bool(enabled)
            _save_schedule_store(entries, path)
            logger.info(f"Scheduled notification updated: id={entry_id}, enabled={enabled}")
            return True
    return False


def _parse_daily_next_run(entry: Dict[str, Any]) -> Optional[datetime]:
    """Compute next run time for a daily entry (UTC). Uses entry['time'] HH:MM and optional timezone."""
    tz = timezone.utc
    if entry.get("timezone"):
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(entry["timezone"])
        except Exception:
            pass
    now = datetime.now(tz)
    time_str = (entry.get("time") or "09:00").strip()
    parts = time_str.split(":")
    try:
        hour = int(parts[0]) if len(parts) > 0 else 9
        minute = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
    except (ValueError, TypeError):
        return None
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return next_run.astimezone(timezone.utc)


def get_due_notifications(path: str = SCHEDULE_STORE_PATH) -> List[Dict[str, Any]]:
    """Return list of enabled notifications that are due (next_run <= now or once_at <= now)."""
    entries = _load_schedule_store(path)
    now_utc = datetime.now(timezone.utc)
    due: List[Dict[str, Any]] = []
    for e in entries:
        if not e.get("enabled", True):
            continue
        if e.get("schedule_type") == "once":
            once_at_str = e.get("once_at")
            if not once_at_str:
                continue
            try:
                # Support ISO with or without Z
                if once_at_str.endswith("Z"):
                    once_at = datetime.fromisoformat(once_at_str.replace("Z", "+00:00"))
                else:
                    once_at = datetime.fromisoformat(once_at_str)
                if once_at.tzinfo is None:
                    once_at = once_at.replace(tzinfo=timezone.utc)
                else:
                    once_at = once_at.astimezone(timezone.utc)
                if once_at <= now_utc:
                    due.append(e)
            except ValueError:
                logger.warning(f"Invalid once_at for schedule {e.get('id')}: {once_at_str}")
        else:
            # daily: use next_run if set, else compute and persist so we don't fire until then
            next_run = e.get("next_run")
            if next_run is None:
                next_run_dt = _parse_daily_next_run(e)
                if next_run_dt is None:
                    continue
                e["next_run"] = next_run_dt.isoformat()
                # Persist so this daily is not due until next_run
                entries_copy = [x if x.get("id") != e.get("id") else dict(e) for x in entries]
                _save_schedule_store(entries_copy, path)
                continue
            try:
                next_run_dt = datetime.fromisoformat(next_run) if isinstance(next_run, str) else next_run
                if next_run_dt.tzinfo is None:
                    next_run_dt = next_run_dt.replace(tzinfo=timezone.utc)
                else:
                    next_run_dt = next_run_dt.astimezone(timezone.utc)
            except (ValueError, TypeError):
                next_run_dt = _parse_daily_next_run(e)
                if next_run_dt is None:
                    continue
                e["next_run"] = next_run_dt.isoformat()
            if next_run_dt <= now_utc:
                due.append(e)
    return due


def _get_chat_ids_for_entry(entry: Dict[str, Any]) -> List[int]:
    """Resolve telegram_chat_id to list of chat IDs (from pairings if 'all')."""
    target = entry.get("telegram_chat_id", "all")
    if target == "all":
        return _get_paired_chat_ids()
    try:
        return [int(target)]
    except (ValueError, TypeError):
        return _get_paired_chat_ids()


async def send_scheduled_notification_async(
    entry: Dict[str, Any],
    config: Optional["TelegramConfig"] = None,
) -> bool:
    """Send one scheduled notification. Returns True if at least one send succeeded."""
    if not TELEGRAM_AVAILABLE or Bot is None:
        logger.warning("Telegram not available for scheduled notification")
        return False
    if config is None:
        config = load_telegram_config()
    if not config or not config.bot_token:
        logger.warning("Telegram not configured for scheduled notification")
        return False
    chat_ids = _get_chat_ids_for_entry(entry)
    if not chat_ids:
        logger.warning("No chat IDs for scheduled notification id=%s", entry.get("id"))
        return False
    bot = Bot(token=config.bot_token)
    sent = 0
    for cid in chat_ids:
        try:
            await bot.send_message(chat_id=cid, text=entry.get("message", ""))
            sent += 1
        except Exception as e:
            logger.warning(f"Scheduled notification to {cid} failed: {e}")
    return sent > 0


def _mark_notification_sent(entry: Dict[str, Any], path: str = SCHEDULE_STORE_PATH) -> None:
    """After sending: remove one-shot or update daily next_run."""
    entries = _load_schedule_store(path)
    eid = entry.get("id")
    now_utc = datetime.now(timezone.utc)
    new_entries = []
    for e in entries:
        if e.get("id") != eid:
            new_entries.append(e)
            continue
        if e.get("schedule_type") == "once":
            # Remove one-shot after send
            logger.info(f"Scheduled one-shot notification sent and removed: id={eid}")
            continue
        # daily: set last_run and next_run
        e["last_run"] = now_utc.isoformat()
        next_run = _parse_daily_next_run(e)
        if next_run:
            e["next_run"] = next_run.isoformat()
            new_entries.append(e)
            logger.info(f"Scheduled daily notification sent; next_run={e['next_run']}: id={eid}")
    _save_schedule_store(new_entries, path)


async def run_scheduler_iteration(config: Optional["TelegramConfig"] = None, path: str = SCHEDULE_STORE_PATH) -> None:
    """Run one iteration: get due notifications, send each, update store; then fire due reach schedules."""
    if config is None:
        config = load_telegram_config()
    due = get_due_notifications(path)
    for entry in due:
        try:
            ok = await send_scheduled_notification_async(entry, config)
            if ok:
                _mark_notification_sent(entry, path)
            else:
                logger.warning("Scheduled notification send failed; will retry next interval: id=%s", entry.get("id"))
        except Exception as e:
            logger.error(f"Scheduled notification error for id={entry.get('id')}: {e}", exc_info=True)
    # Fire reach-at-time schedules (e.g. "remind me in 10 min") so they work without cron
    try:
        from reach_schedules import fire_due_schedules_async
        fired = await fire_due_schedules_async()
        if fired:
            logger.info("Reach fired %d schedule(s) from in-process scheduler", len(fired))
    except Exception as e:
        logger.error("Reach fire (in-process) failed: %s", e, exc_info=True)


async def run_scheduler_loop(
    config: Optional["TelegramConfig"] = None,
    interval_seconds: int = DEFAULT_SCHEDULE_CHECK_INTERVAL_SECONDS,
    path: str = SCHEDULE_STORE_PATH,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Background loop: every interval_seconds, run one scheduler iteration. If stop_event is set, stop when it is set."""
    if config is None:
        config = load_telegram_config()
    while True:
        try:
            await run_scheduler_iteration(config, path)
        except Exception as e:
            logger.error(f"Scheduler iteration error: {e}", exc_info=True)
        if stop_event is not None:
            try:
                await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=float(interval_seconds))
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                logger.info("Scheduler loop stopping (stop_event set)")
                break
        else:
            await asyncio.sleep(interval_seconds)


@dataclass
class TelegramConfig:
    """Telegram bot configuration"""
    bot_token: str
    enabled: bool = True
    dm_policy: str = "pairing"  # "pairing" or "open"
    allow_from: Optional[List[str]] = None  # List of user IDs/usernames allowed
    groups: Optional[Dict[str, Any]] = None  # Group configuration
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    request_timeout_seconds: int = 900  # Max seconds per message (default 15 min; set high to avoid "request timed out")


# Default menu items (can be extended via config "telegram_menu" key)
DEFAULT_MENU_ITEMS: List[Dict[str, Any]] = [
    {
        "id": "weather_lviv",
        "label": "🌤 Weather Lviv",
        "action": "weather",
        "city": "Lviv",
    },
    {
        "id": "ha_house_fuse_power",
        "label": "🔌 House Fuse Power",
        "action": "delegate",
        "persona_id": "home_assistant",
        "task": (
            "Check the current power usage on the house fuse (main grid input sensor). "
            "Report: current power (W), today's total energy if available (kWh), "
            "and a brief note if the value is unusually high or low. "
            "Use MCP to query the relevant power/energy sensor(s). Be concise."
        ),
    },
]


class TelegramBot:
    """Telegram bot for cursor-enhanced"""
    
    def __init__(self, config: TelegramConfig, runtime_integration=None):
        if not TELEGRAM_AVAILABLE or Update is None:
            raise RuntimeError("python-telegram-bot required. Install with: pip install python-telegram-bot")
        
        self.config = config
        self.runtime = runtime_integration
        self.application = None
        self.bot = None
        self.pairing_store_path = os.path.expanduser("~/.cursor-enhanced/telegram-pairings.json")
        self.paired_users: set = set()
        self.pending_pairings: Dict[str, str] = {}  # chat_id -> pairing_code
        self._pairings_loaded = False
        self._load_pairings(force_reload=True)
        self.menu_items: List[Dict[str, Any]] = self._load_menu_items()
    
    def _load_pairings(self, force_reload: bool = False):
        """Load paired users and pending pairings from disk"""
        old_paired_count = len(self.paired_users) if hasattr(self, 'paired_users') else 0
        old_paired_set = set(self.paired_users) if hasattr(self, 'paired_users') else set()
        
        if not hasattr(self, 'paired_users'):
            self.paired_users: set = set()
        if not hasattr(self, 'pending_pairings'):
            self.pending_pairings: Dict[str, str] = {}
        
        # Ensure paired_users is actually a set (not a list or something else)
        if not isinstance(self.paired_users, set):
            logger.warning(f"paired_users is not a set! It's a {type(self.paired_users)}. Converting...")
            self.paired_users = set(self.paired_users)
        
        if os.path.exists(self.pairing_store_path):
            try:
                with open(self.pairing_store_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        if "paired_users" in data:
                            # Ensure all user IDs are integers
                            paired_list = data["paired_users"]
                            new_paired = set()
                            for uid in paired_list:
                                try:
                                    # Try to convert to int (handles both int and string)
                                    new_paired.add(int(uid))
                                except (ValueError, TypeError):
                                    logger.warning(f"Invalid user_id in pairings: {uid} (type: {type(uid)})")
                            
                            if new_paired != old_paired_set or force_reload:
                                self.paired_users = new_paired
                                # Ensure it's a set
                                if not isinstance(self.paired_users, set):
                                    logger.warning(f"After assignment, paired_users is {type(self.paired_users)}, converting to set")
                                    self.paired_users = set(self.paired_users)
                                if force_reload or len(new_paired) != old_paired_count:
                                    logger.info(f"Loaded {len(self.paired_users)} paired users from disk (was {old_paired_count}, file had: {paired_list}, converted to: {list(new_paired)})")
                                    logger.info(f"   Set contents: {self.paired_users}, type: {type(self.paired_users)}")
                                    if force_reload:
                                        logger.debug(f"Force reload: old_set={old_paired_set}, new_set={new_paired}, match={new_paired == old_paired_set}")
                        if "pending_pairings" in data:
                            self.pending_pairings = dict(data["pending_pairings"])
                            if len(self.pending_pairings) > 0:
                                logger.debug(f"Loaded {len(self.pending_pairings)} pending pairings")
            except Exception as e:
                logger.error(f"Failed to load pairings: {e}", exc_info=True)
        else:
            if force_reload:
                logger.debug(f"Pairing file does not exist: {self.pairing_store_path}")
        
        if not hasattr(self, '_pairings_loaded'):
            self._pairings_loaded = True
    
    def _save_pairings(self):
        """Save paired users to disk"""
        try:
            os.makedirs(os.path.dirname(self.pairing_store_path), exist_ok=True)
            data = {}
            if os.path.exists(self.pairing_store_path):
                try:
                    with open(self.pairing_store_path, 'r') as f:
                        data = json.load(f)
                except:
                    pass
            # Ensure paired_users are stored as list of integers
            data["paired_users"] = [int(uid) for uid in self.paired_users]
            # Preserve pending_pairings if they exist
            if "pending_pairings" not in data:
                data["pending_pairings"] = {}
            with open(self.pairing_store_path, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved {len(self.paired_users)} paired users to {self.pairing_store_path}")
        except Exception as e:
            logger.error(f"Failed to save pairings: {e}", exc_info=True)
    
    def approve_pairing(self, code: str) -> bool:
        """Approve a pairing code"""
        # First check in-memory pending pairings
        chat_id = None
        for cid, pairing_code in self.pending_pairings.items():
            if pairing_code.upper() == code.upper():  # Case-insensitive
                chat_id = cid
                break
        
        # If not found in memory, check stored pending pairings
        if not chat_id and os.path.exists(self.pairing_store_path):
            try:
                with open(self.pairing_store_path, 'r') as f:
                    data = json.load(f)
                    pending = data.get("pending_pairings", {})
                    for cid, pairing_code in pending.items():
                        if pairing_code.upper() == code.upper():  # Case-insensitive
                            chat_id = cid
                            # Also update in-memory
                            self.pending_pairings[chat_id] = pairing_code
                            break
            except Exception as e:
                logger.warning(f"Failed to load pending pairings: {e}")
        
        if chat_id:
            try:
                # Convert chat_id to int - this is the user_id for private chats
                user_id = int(chat_id)
                # Also store chat_id as int in case they differ (shouldn't in private chats)
                chat_id_int = int(chat_id)
                
                # Add both user_id and chat_id_int to paired_users (they should be the same)
                self.paired_users.add(user_id)
                if chat_id_int != user_id:
                    self.paired_users.add(chat_id_int)
                    logger.warning(f"chat_id ({chat_id_int}) != user_id ({user_id}), added both to paired_users")
                
                # Remove from pending (both memory and disk)
                self.pending_pairings.pop(chat_id, None)
                
                # Update stored file - ensure we preserve structure
                try:
                    data = {}
                    if os.path.exists(self.pairing_store_path):
                        with open(self.pairing_store_path, 'r') as f:
                            data = json.load(f)
                    
                    # Update both paired_users and pending_pairings
                    # Ensure paired_users are stored as list of integers
                    data["paired_users"] = sorted([int(uid) for uid in self.paired_users])
                    if "pending_pairings" in data:
                        data["pending_pairings"] = {k: v for k, v in data["pending_pairings"].items() if k != chat_id}
                    else:
                        data["pending_pairings"] = {}
                    
                    os.makedirs(os.path.dirname(self.pairing_store_path), exist_ok=True)
                    with open(self.pairing_store_path, 'w') as f:
                        json.dump(data, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())  # Force write to disk
                    
                    # Verify the file was written correctly
                    try:
                        with open(self.pairing_store_path, 'r') as f:
                            verify_data = json.load(f)
                            verify_paired = verify_data.get("paired_users", [])
                            logger.info(f"Saved approval: user_id={user_id} (chat_id={chat_id}). File verification: {verify_paired} (types: {[type(u) for u in verify_paired]})")
                    except Exception as e:
                        logger.error(f"Failed to verify saved file: {e}")
                except Exception as e:
                    logger.error(f"Failed to update pairing store: {e}", exc_info=True)
                    # Still save paired users using the simpler method
                    try:
                        self._save_pairings()
                    except:
                        pass
                
                logger.info(f"Approved pairing for user {user_id} (code: {code}, chat_id: {chat_id})")
                return True
            except ValueError as e:
                logger.error(f"Invalid chat_id format: {chat_id}, error: {e}")
        
        return False
    
    def _save_pending_pairing(self, chat_id: str, code: str):
        """Save pending pairing to disk"""
        try:
            os.makedirs(os.path.dirname(self.pairing_store_path), exist_ok=True)
            data = {}
            if os.path.exists(self.pairing_store_path):
                try:
                    with open(self.pairing_store_path, 'r') as f:
                        data = json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read existing pairing file: {e}")
            
            # Preserve paired_users if they exist
            if "paired_users" not in data:
                data["paired_users"] = list(self.paired_users) if self.paired_users else []
            
            if "pending_pairings" not in data:
                data["pending_pairings"] = {}
            data["pending_pairings"][chat_id] = code
            
            with open(self.pairing_store_path, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved pending pairing: chat_id={chat_id}, code={code}")
        except Exception as e:
            logger.error(f"Failed to save pending pairing: {e}", exc_info=True)
        
    def _load_menu_items(self) -> List[Dict[str, Any]]:
        """Load menu items: defaults merged with config overrides from 'telegram_menu'."""
        items_by_id = {item["id"]: dict(item) for item in DEFAULT_MENU_ITEMS}
        try:
            config_file = os.path.expanduser("~/.cursor-enhanced-config.json")
            if os.path.exists(config_file):
                with open(config_file, "r") as f:
                    cfg = json.load(f)
                for item in cfg.get("telegram_menu", []):
                    if isinstance(item, dict) and item.get("id"):
                        items_by_id[item["id"]] = item
        except Exception as e:
            logger.warning(f"Failed to load telegram_menu from config: {e}")
        return list(items_by_id.values())

    def _build_menu_keyboard(self) -> "InlineKeyboardMarkup":
        """Build inline keyboard from menu items."""
        buttons = []
        for item in self.menu_items:
            buttons.append([InlineKeyboardButton(
                text=item.get("label", item["id"]),
                callback_data=f"menu:{item['id']}",
            )])
        return InlineKeyboardMarkup(buttons)

    async def _handle_menu(self, update, context):
        """Handle /menu command — show inline keyboard."""
        user = update.effective_user
        chat = update.effective_chat
        if chat.type == "private" and not self._is_allowed_user(user.id, user.username):
            await update.message.reply_text("Please pair first. Send /start")
            return
        if not self.menu_items:
            await update.message.reply_text("No menu items configured.")
            return
        await update.message.reply_text("Choose an action:", reply_markup=self._build_menu_keyboard())

    async def _handle_callback_query(self, update, context):
        """Handle inline keyboard button presses."""
        query = update.callback_query
        user = update.effective_user
        chat = update.effective_chat
        if not query or not query.data:
            return
        # Acknowledge the button press immediately
        await query.answer()

        if chat.type == "private" and not self._is_allowed_user(user.id, user.username):
            await query.edit_message_text("Please pair first. Send /start")
            return

        data = query.data  # e.g. "menu:ha_house_fuse_power"
        if not data.startswith("menu:"):
            return
        item_id = data[len("menu:"):]
        item = next((i for i in self.menu_items if i["id"] == item_id), None)
        if not item:
            await query.edit_message_text(f"Unknown menu item: {item_id}")
            return

        action = item.get("action", "message")
        label = item.get("label", item_id)

        # Show a "working" indicator
        await query.edit_message_text(f"⏳ {label}…")
        try:
            await context.bot.send_chat_action(chat_id=chat.id, action="typing")
        except Exception:
            pass

        try:
            if action == "weather":
                response = await self._run_menu_weather(item)
            elif action == "delegate":
                response = await self._run_menu_delegate(item, user.id, chat.id)
            elif action == "message":
                # Plain text passthrough to cursor-enhanced
                response = await self._process_message(item.get("task", label), user.id, chat.id)
            else:
                response = f"Unknown action type: {action}"
        except Exception as e:
            logger.error(f"Menu action {item_id} failed: {e}", exc_info=True)
            response = f"Error running {label}: {e}"

        # Send result and re-attach the menu keyboard for quick re-use
        max_length = 4096
        keyboard = self._build_menu_keyboard()
        if len(response) > max_length:
            chunks = [response[i:i + max_length] for i in range(0, len(response), max_length - 100)]
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1:
                    await context.bot.send_message(chat_id=chat.id, text=chunk, reply_markup=keyboard)
                else:
                    await context.bot.send_message(chat_id=chat.id, text=chunk)
        else:
            await context.bot.send_message(chat_id=chat.id, text=response, reply_markup=keyboard)

    async def _run_menu_weather(self, item: Dict[str, Any]) -> str:
        """Execute a weather menu item directly via WeatherTool (fast, in-process)."""
        city = item.get("city", "Lviv")
        try:
            from runtime_weather_tool import WeatherTool
            tool = WeatherTool()
            result = await tool.execute(city=city, forecast_days=7)
            if "error" in result:
                return f"Weather error: {result['error']}"
            # Format a nice text response
            cur = result.get("current", {})
            city_name = result.get("city", city)
            lines = [f"🌤 Weather in {city_name}"]
            lines.append("")
            lines.append(
                f"Now: {cur.get('temperature_c', '?')}°C "
                f"(feels {cur.get('feels_like_c', '?')}°C), "
                f"{cur.get('weather', '?')}"
            )
            lines.append(
                f"💧 {cur.get('humidity_pct', '?')}%  "
                f"💨 {cur.get('wind_speed_kmh', '?')} km/h  "
                f"🔻 {cur.get('pressure_hpa', '?')} hPa"
            )
            forecast = result.get("forecast", [])
            if forecast:
                lines.append("")
                lines.append("📅 7-day forecast:")
                for day in forecast:
                    t_min = day.get("temp_min_c", "?")
                    t_max = day.get("temp_max_c", "?")
                    precip = day.get("precipitation_mm", 0) or 0
                    precip_str = f", {precip} mm" if precip else ""
                    lines.append(
                        f"  {day.get('date', '?')}: {day.get('weather', '?')} "
                        f"({t_min}…{t_max}°C{precip_str})"
                    )
            return "\n".join(lines)
        except Exception as e:
            logger.error("Weather menu action failed: %s", e, exc_info=True)
            return f"Weather error: {e}"

    async def _run_menu_delegate(self, item: Dict[str, Any], user_id: int, chat_id: int) -> str:
        """Execute a menu item via the delegate tool (sub-agent with persona)."""
        persona_id = item.get("persona_id", "researcher")
        task = item.get("task", "")
        if not task:
            return "Menu item has no task configured."

        # Try using the delegate tool directly if runtime is available
        if self.runtime and hasattr(self.runtime, "tool_registry"):
            try:
                result = await self.runtime.tool_registry.execute(
                    "delegate", "", {"persona_id": persona_id, "task": task}
                )
                if result.get("success"):
                    return result.get("response", "(empty response from delegate)")
                else:
                    error = result.get("error", "Unknown delegate error")
                    logger.warning(f"Delegate {persona_id} failed: {error}")
                    # Fall through to subprocess
            except Exception as e:
                logger.warning(f"Delegate via runtime failed, falling back to subprocess: {e}")

        # Fallback: run via cursor-enhanced subprocess
        return await self._process_message(
            f"delegate to {persona_id}: {task}", user_id, chat_id
        )

    async def start(self):
        """Start the Telegram bot"""
        if not self.config.enabled:
            logger.info("Telegram bot is disabled")
            return
        
        if not self.config.bot_token:
            raise ValueError("Telegram bot token is required")
        
        self.application = Application.builder().token(self.config.bot_token).build()
        self.bot = self.application.bot
        
        # Register handlers
        self.application.add_handler(CommandHandler("start", self._handle_start))
        self.application.add_handler(CommandHandler("help", self._handle_help))
        self.application.add_handler(CommandHandler("status", self._handle_status))
        self.application.add_handler(CommandHandler("menu", self._handle_menu))
        self.application.add_handler(CallbackQueryHandler(self._handle_callback_query))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        
        # Start polling
        logger.info("Starting Telegram bot...")
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started and polling for messages")
        print("✅ Telegram bot is running and listening for messages...")
        print("   Send /start to your bot to begin pairing.")
    
    async def stop(self):
        """Stop the Telegram bot"""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram bot stopped")
    
    def _generate_pairing_code(self) -> str:
        """Generate a pairing code"""
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    def _is_allowed_user(self, user_id: int, username: Optional[str] = None) -> bool:
        """Check if user is allowed to interact with the bot"""
        # Reload pairings from disk to get latest approvals
        # (in case approval happened while bot was running)
        old_paired = set(self.paired_users) if hasattr(self, 'paired_users') else set()
        self._load_pairings(force_reload=True)
        
        # Ensure user_id is int for comparison
        user_id_int = int(user_id) if not isinstance(user_id, int) else user_id
        
        logger.info(f"Checking access for user_id={user_id_int} (type={type(user_id_int)}), paired_users={sorted(self.paired_users)} (count={len(self.paired_users)}, types: {[type(u) for u in self.paired_users]})")
        logger.info(f"DM policy: {self.config.dm_policy}, allow_from: {self.config.allow_from}")
        
        if self.config.dm_policy == "open":
            logger.info(f"Using OPEN policy - checking allowlist")
            if self.config.allow_from:
                if "*" in self.config.allow_from:
                    logger.info(f"Open policy with * - allowing all")
                    return True
                user_str = str(user_id)
                username_lower = username.lower() if username else None
                for allowed in self.config.allow_from:
                    if user_str == allowed or (username_lower and username_lower == allowed.lower()):
                        logger.info(f"User {user_id_int} found in allowlist: {allowed}")
                        return True
                # User not in allowlist - but check if they're paired
                logger.info(f"User {user_id_int} NOT in allowlist, checking if paired...")
                if user_id_int in self.paired_users:
                    logger.info(f"User {user_id_int} is paired, allowing access despite not being in allowlist")
                    return True
                logger.info(f"User {user_id_int} NOT in allowlist and NOT paired, denying access")
                return False
            logger.info(f"Open policy with no allowlist - allowing all")
            return True  # Open policy with no allowlist = allow all
        
        logger.info(f"Using PAIRING policy - checking paired_users")
        
        # Pairing policy - check if user_id is in paired_users
        # Check if user_id is in paired_users (all should be ints now)
        logger.info(f"About to check membership: user_id_int={user_id_int} (type={type(user_id_int)}), self.paired_users={self.paired_users} (type={type(self.paired_users)}), len={len(self.paired_users) if hasattr(self.paired_users, '__len__') else 'N/A'}")
        
        # Explicit check with detailed logging
        is_paired = False
        membership_result = user_id_int in self.paired_users
        logger.info(f"Membership check result: {user_id_int} in {self.paired_users} = {membership_result}")
        
        if membership_result:
            is_paired = True
            logger.info(f"✅ User {user_id_int} is paired (found in set), allowing access")
        else:
            # Try explicit iteration to see what's in the set
            logger.info(f"❌ User {user_id_int} (type={type(user_id_int)}) NOT found in paired_users set")
            logger.info(f"   paired_users set contents: {list(self.paired_users)}")
            logger.info(f"   paired_users set type: {type(self.paired_users)}")
            for uid in self.paired_users:
                eq_result = user_id_int == uid
                is_result = user_id_int is uid
                logger.info(f"   Comparing: {user_id_int} == {uid} (type {type(uid)})? {eq_result}")
                logger.info(f"   Comparing: {user_id_int} is {uid}? {is_result}")
        
        if is_paired:
            return True
        else:
            # Double-check by reading file directly
            if os.path.exists(self.pairing_store_path):
                try:
                    with open(self.pairing_store_path, 'r') as f:
                        direct_data = json.load(f)
                        direct_paired = direct_data.get("paired_users", [])
                        logger.error(f"Direct file read shows paired_users: {direct_paired} (types: {[type(u) for u in direct_paired]})")
                        # Check if user_id is in the direct read
                        direct_paired_set = set(int(uid) for uid in direct_paired)
                        if user_id_int in direct_paired_set:
                            logger.error(f"⚠️  User {user_id_int} IS in file but NOT in memory! Forcing reload...")
                            # Force reload again
                            self._load_pairings(force_reload=True)
                            # Check again
                            if user_id_int in self.paired_users:
                                logger.info(f"✅ After forced reload, user {user_id_int} is now in paired_users")
                                return True
                except Exception as e:
                    logger.error(f"Failed to read file directly: {e}")
            return False
    
    async def _handle_start(self, update, context):
        """Handle /start command"""
        user = update.effective_user
        chat = update.effective_chat
        
        if chat.type == "private":
            if self._is_allowed_user(user.id, user.username):
                await update.message.reply_text(
                    "Hello! I'm cursor-enhanced with Runtime integration. "
                    "You can ask me anything and I'll help you using my available tools.\n\n"
                    "Use /help to see available commands."
                )
            else:
                # Generate pairing code
                code = self._generate_pairing_code()
                chat_id_str = str(chat.id)
                self.pending_pairings[chat_id_str] = code
                self._save_pending_pairing(chat_id_str, code)
                logger.info(f"Generated pairing code {code} for chat {chat_id_str}")
                await update.message.reply_text(
                    f"Hello! To start using this bot, please approve the pairing code: **{code}**\n\n"
                    f"Run this command on your system:\n"
                    f"`cursor-enhanced --telegram-approve {code}`\n\n"
                    "Or if you're already paired, you may need to check your configuration.",
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(
                "This bot is available in this group. "
                "Mention me or reply to my messages to interact."
            )
    
    async def _handle_help(self, update, context):
        """Handle /help command"""
        help_text = """Available commands:
/start - Start the bot
/help - Show this help message
/menu - Quick actions menu
/status - Show bot status and available tools

You can also just send me messages and I'll respond using my available tools."""
        
        await update.message.reply_text(help_text)
    
    async def _handle_status(self, update, context):
        """Handle /status command"""
        status_parts = ["**Cursor-Enhanced Status**\n"]
        
        if self.runtime:
            tools = self.runtime.list_tools()
            status_parts.append(f"Available tools: {len(tools)}")
            for tool in tools[:5]:  # Show first 5
                name = tool.get('name', 'unknown')
                status_parts.append(f"- {name}")
            
            skills = self.runtime.list_skills()
            if skills:
                status_parts.append(f"\nAvailable skills: {len(skills)}")
                status_parts.append(", ".join(skills[:5]))
        else:
            status_parts.append("Runtime integration: Not available")
        
        await update.message.reply_text("\n".join(status_parts), parse_mode="Markdown")
    
    async def _handle_message(self, update, context):
        """Handle incoming messages"""
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        
        if not message or not message.text:
            return
        
        # Check if user is allowed
        # In private chats, chat.id == user.id, but check both to be safe
        logger.debug(f"Checking message from user_id={user.id} (type={type(user.id)}), chat_id={chat.id} (type={type(chat.id)})")
        is_allowed = self._is_allowed_user(user.id, user.username)
        
        # Also check chat.id in case there's a mismatch (shouldn't happen in private chats, but be safe)
        if not is_allowed and chat.type == "private" and chat.id != user.id:
            logger.warning(f"chat.id ({chat.id}) != user.id ({user.id}) in private chat - checking chat.id too")
            is_allowed = self._is_allowed_user(chat.id, user.username)
        
        logger.debug(f"User {user.id} (chat {chat.id}) allowed status: {is_allowed}")
        
        if chat.type == "private" and not is_allowed:
            # Check if they have a pending pairing
            chat_id_str = str(chat.id)
            if chat_id_str in self.pending_pairings:
                code = self.pending_pairings[chat_id_str]
                logger.info(f"User {user.id} has pending pairing {code}, reminding them")
                await message.reply_text(
                    f"Please approve the pairing code first: **{code}**\n\n"
                    f"Run: `cursor-enhanced --telegram-approve {code}`",
                    parse_mode="Markdown"
                )
            else:
                code = self._generate_pairing_code()
                self.pending_pairings[chat_id_str] = code
                self._save_pending_pairing(chat_id_str, code)
                logger.info(f"Generated new pairing code {code} for user {user.id} (chat {chat_id_str})")
                await message.reply_text(
                    f"Pairing required. Code: **{code}**\n\n"
                    f"Run: `cursor-enhanced --telegram-approve {code}`",
                    parse_mode="Markdown"
                )
            return
        
        # Process message through cursor-enhanced
        user_message = message.text
        
        # Check if message starts with "new" — spawn a fresh concurrent task (no queue, no history)
        # This allows multithreaded conversation: "new" messages run concurrently without blocking
        stripped = user_message.strip()
        # Case-insensitive check: message must start with "new " (with space) or be exactly "new"
        if stripped.lower().startswith("new ") or stripped.lower() == "new":
            # Strip the "new" prefix to get the actual message
            actual_message = stripped[4:].strip() if len(stripped) > 3 else ""
            if not actual_message:
                await message.reply_text("Please provide a message after 'new'. Example: new What is the weather?")
                return
            logger.info(f"New-thread message from user {user.id}: spawning concurrent task with fresh context (no history)")
            # Fire-and-forget: spawn a background task so it doesn't block the queue
            # This allows multiple "new" messages to run concurrently, and they won't wait for regular messages
            asyncio.create_task(
                self._handle_new_thread_message(actual_message, user.id, chat.id, message)
            )
            return
        
        try:
            # Send typing indicator
            await context.bot.send_chat_action(chat_id=chat.id, action="typing")
            
            # Route message through cursor-enhanced
            response = await self._process_message(user_message, user.id, chat.id)
            
            # Send response (split if too long for Telegram's 4096 char limit)
            max_length = 4096
            if len(response) > max_length:
                # Split into chunks
                chunks = [response[i:i+max_length] for i in range(0, len(response), max_length-100)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply_text(chunk)
                    else:
                        await message.reply_text(f"[Continued...]\n{chunk}")
            else:
                await message.reply_text(response)
        except Exception as e:
            logger.error(f"Error processing Telegram message: {e}", exc_info=True)
            await message.reply_text(f"Sorry, I encountered an error: {str(e)}")
    
    async def _process_message(self, message: str, user_id: int, chat_id: int) -> str:
        """Process a message through cursor-enhanced"""
        import subprocess
        import os
        
        # Get the path to cursor-enhanced (the current script)
        # We'll call cursor-enhanced itself with the message
        cursor_enhanced_path = os.environ.get("CURSOR_ENHANCED_BIN")
        cmd = None

        run_env = os.environ.copy()
        run_env["CURSOR_ENHANCED_CHANNEL"] = "telegram"
        if cursor_enhanced_path:
            cmd = [cursor_enhanced_path, "--enable-runtime", "-p", message]
        else:
            cursor_enhanced_path = os.path.expanduser("~/.local/bin/cursor-enhanced")
            if os.path.exists(cursor_enhanced_path):
                cmd = ["bash", cursor_enhanced_path, "--enable-runtime", "-p", message]
            else:
                import shutil
                cursor_enhanced_path = shutil.which("cursor-enhanced")
                if cursor_enhanced_path:
                    cmd = [cursor_enhanced_path, "--enable-runtime", "-p", message]

        if cmd is None:
            # Fallback: use python module entrypoint
            script_dir = os.path.dirname(os.path.abspath(__file__))
            repo_root = os.path.dirname(script_dir)
            run_env["PYTHONPATH"] = f"{repo_root}:{run_env.get('PYTHONPATH', '')}"
            cmd = ["python3", "-m", "cursor_enhanced", "--enable-runtime", "-p", message]
        
        try:
            logger.info(f"Processing Telegram message from user {user_id}: {message[:100]}")
            
            # Run cursor-enhanced and capture output
            # Use run_in_executor so the blocking subprocess doesn't freeze the event loop
            # (critical for allowing concurrent "new" thread messages)
            timeout_sec = getattr(self.config, "request_timeout_seconds", 900)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout_sec,
                    cwd=os.path.expanduser("~"),
                    env=run_env,
                )
            )
            
            stdout_text = (result.stdout or "").strip()
            stderr_text = (result.stderr or "").strip()
            
            if result.returncode == 0:
                if stdout_text:
                    response = stdout_text
                elif stderr_text:
                    # Agent succeeded but only wrote to stderr (unusual but possible)
                    logger.warning(f"cursor-enhanced returned 0 but stdout empty, stderr: {stderr_text[:200]}")
                    response = stderr_text
                else:
                    # Empty stdout AND stderr with exit 0 — cursor-agent returned nothing.
                    # main.py should now print a fallback, but guard against it here too.
                    logger.warning(
                        "cursor-enhanced returned 0 with empty stdout and stderr for message: %s",
                        message[:100],
                    )
                    response = (
                        "I received your message but the agent returned an empty response. "
                        "This can happen with complex or context-dependent queries. "
                        "Please try rephrasing or adding more detail."
                    )
                logger.info(f"Response generated: {len(response)} characters")
                return response
            else:
                # Non-zero exit: prefer stdout (main.py now prints errors there), fall back to stderr
                response = stdout_text or stderr_text or f"Sorry, the agent encountered an error (exit code {result.returncode}). Please try again."
                logger.error(f"cursor-enhanced error (code {result.returncode}): stdout={stdout_text[:200]}, stderr={stderr_text[:200]}")
                return response[:4000]
        except subprocess.TimeoutExpired:
            logger.warning("Message processing timed out")
            return "Sorry, the request timed out. You can increase the limit with requestTimeoutSeconds in config or CURSOR_ENHANCED_TELEGRAM_REQUEST_TIMEOUT (current limit was %s seconds)." % getattr(self.config, "request_timeout_seconds", 900)
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            return f"Sorry, I encountered an error: {str(e)}"

    async def _handle_new_thread_message(self, message: str, user_id: int, chat_id: int, original_message) -> None:
        """Handle a 'new' prefixed message: runs concurrently with no history context.
        
        This is fire-and-forget from the handler — it sends typing, processes the
        message with fresh context (no conversation history), and replies directly.
        """
        try:
            from telegram import Bot as TgBot
            bot = original_message.get_bot()
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass

        try:
            response = await self._process_message_fresh(message, user_id, chat_id)
            # Send response (split if too long for Telegram's 4096 char limit)
            max_length = 4096
            if len(response) > max_length:
                chunks = [response[i:i + max_length] for i in range(0, len(response), max_length - 100)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await original_message.reply_text(chunk)
                    else:
                        await original_message.reply_text(f"[Continued...]\n{chunk}")
            else:
                await original_message.reply_text(response)
        except Exception as e:
            logger.error(f"Error in new-thread message: {e}", exc_info=True)
            try:
                await original_message.reply_text(f"Sorry, I encountered an error: {str(e)}")
            except Exception:
                logger.error(f"Failed to send error reply for new-thread message: {e}")

    async def _process_message_fresh(self, message: str, user_id: int, chat_id: int) -> str:
        """Process a message with fresh context — no conversation history, minimal prompt.
        
        Uses --fresh so cursor-enhanced sends only a minimal system prompt and
        the current message (no conversation history, no verbose tool listings).
        Does NOT write to any history file. Runs in a thread pool to avoid
        blocking the event loop.
        """
        import subprocess
        import os

        cursor_enhanced_path = os.environ.get("CURSOR_ENHANCED_BIN")
        cmd = None

        run_env = os.environ.copy()
        run_env["CURSOR_ENHANCED_CHANNEL"] = "telegram"

        base_flags = ["--enable-runtime", "--fresh", "-p", message]

        if cursor_enhanced_path:
            cmd = [cursor_enhanced_path] + base_flags
        else:
            cursor_enhanced_path = os.path.expanduser("~/.local/bin/cursor-enhanced")
            if os.path.exists(cursor_enhanced_path):
                cmd = ["bash", cursor_enhanced_path] + base_flags
            else:
                import shutil
                cursor_enhanced_path = shutil.which("cursor-enhanced")
                if cursor_enhanced_path:
                    cmd = [cursor_enhanced_path] + base_flags

        if cmd is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            repo_root = os.path.dirname(script_dir)
            run_env["PYTHONPATH"] = f"{repo_root}:{run_env.get('PYTHONPATH', '')}"
            cmd = ["python3", "-m", "cursor_enhanced"] + base_flags

        try:
            logger.info(f"Processing NEW-thread message from user {user_id} (fresh context): {message[:100]}")

            timeout_sec = getattr(self.config, "request_timeout_seconds", 900)
            # Run subprocess in a thread pool so it doesn't block the event loop
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout_sec,
                    cwd=os.path.expanduser("~"),
                    env=run_env,
                )
            )

            stdout_text = (result.stdout or "").strip()
            stderr_text = (result.stderr or "").strip()

            if result.returncode == 0:
                if stdout_text:
                    return stdout_text
                elif stderr_text:
                    logger.warning(f"new-thread: cursor-enhanced returned 0 but stdout empty, stderr: {stderr_text[:200]}")
                    return stderr_text
                else:
                    return (
                        "I received your message but the agent returned an empty response. "
                        "Please try rephrasing or adding more detail."
                    )
            else:
                response = stdout_text or stderr_text or f"Sorry, the agent encountered an error (exit code {result.returncode}). Please try again."
                logger.error(f"new-thread cursor-enhanced error (code {result.returncode}): {stdout_text[:200]}")
                return response[:4000]
        except subprocess.TimeoutExpired:
            logger.warning("New-thread message processing timed out")
            return "Sorry, the request timed out."
        except Exception as e:
            logger.error(f"Error processing new-thread message: {e}", exc_info=True)
            return f"Sorry, I encountered an error: {str(e)}"

def load_telegram_config(config_file: Optional[str] = None) -> Optional[TelegramConfig]:
    """Load Telegram configuration from config file or environment"""
    if config_file is None:
        config_file = os.path.expanduser("~/.cursor-enhanced-config.json")
    
    config_data = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load config: {e}")
    
    # Check environment variable first
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    # Then check config file
    telegram_config = config_data.get("channels", {}).get("telegram", {})
    if not bot_token:
        bot_token = telegram_config.get("botToken")
    
    if not bot_token:
        return None
    
    timeout_val = 900
    env_timeout = os.environ.get("CURSOR_ENHANCED_TELEGRAM_REQUEST_TIMEOUT")
    if env_timeout is not None:
        try:
            timeout_val = max(60, int(env_timeout))
        except ValueError:
            pass
    elif telegram_config.get("requestTimeoutSeconds") is not None:
        try:
            timeout_val = max(60, int(telegram_config["requestTimeoutSeconds"]))
        except (ValueError, TypeError):
            pass
    
    return TelegramConfig(
        bot_token=bot_token,
        enabled=telegram_config.get("enabled", True),
        dm_policy=telegram_config.get("dmPolicy", "pairing"),
        allow_from=telegram_config.get("allowFrom"),
        groups=telegram_config.get("groups"),
        webhook_url=telegram_config.get("webhookUrl"),
        webhook_secret=telegram_config.get("webhookSecret"),
        request_timeout_seconds=timeout_val
    )


def _get_paired_chat_ids() -> List[int]:
    """Load paired user/chat IDs from pairing store (for reach notifications)."""
    path = os.path.expanduser("~/.cursor-enhanced/telegram-pairings.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        paired = data.get("paired_users") or []
        return [int(uid) for uid in paired]
    except Exception as e:
        logger.warning(f"Failed to load pairings for reach: {e}")
        return []


async def send_to_paired_users_async(message: str, config: Optional[TelegramConfig] = None) -> bool:
    """Send a message to all paired Telegram users (e.g. for reach-at-time). Returns True if at least one send succeeded."""
    if not TELEGRAM_AVAILABLE or Bot is None:
        logger.warning("Telegram not available for reach")
        return False
    if config is None:
        config = load_telegram_config()
    if not config or not config.bot_token:
        logger.warning("Telegram not configured for reach")
        return False
    chat_ids = _get_paired_chat_ids()
    if not chat_ids:
        logger.warning("No paired users for reach")
        return False
    bot = Bot(token=config.bot_token)
    sent = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=message)
            sent += 1
        except Exception as e:
            logger.warning(f"Reach: failed to send to {chat_id}: {e}")
    return sent > 0


def send_to_paired_users(message: str, config: Optional[TelegramConfig] = None) -> bool:
    """Synchronous wrapper: send a message to all paired Telegram users."""
    return asyncio.run(send_to_paired_users_async(message, config))


async def run_telegram_bot(config: Optional[TelegramConfig] = None, runtime_integration=None):
    """Run the Telegram bot"""
    if not TELEGRAM_AVAILABLE or Update is None:
        raise RuntimeError("python-telegram-bot required. Install with: pip install python-telegram-bot")
    
    if config is None:
        config = load_telegram_config()
    
    if not config or not config.bot_token:
        raise ValueError("Telegram bot token is required. Set TELEGRAM_BOT_TOKEN env var or configure in ~/.cursor-enhanced-config.json")
    
    bot = TelegramBot(config, runtime_integration)
    await bot.start()

    # In-process scheduler for scheduled notifications (scheduled-notifications.json)
    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(
        run_scheduler_loop(config=config, stop_event=stop_event)
    )

    try:
        print("Press Ctrl+C to stop the bot...")
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    print("\nStopping Telegram bot...")
    logger.info("Stopping Telegram bot...")
    stop_event.set()
    try:
        await asyncio.wait_for(asyncio.shield(scheduler_task), timeout=15)
    except asyncio.TimeoutError:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
    await bot.stop()
    print("Telegram bot stopped.")
