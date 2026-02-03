"""
Telegram Integration for Cursor Enhanced

This module provides Telegram bot integration, allowing cursor-enhanced to
receive and respond to messages via Telegram, similar to OpenClaw's Telegram channel.
"""

import os
import json
import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger("cursor_enhanced.telegram")

# Try to import telegram bot library
try:
    from telegram import Update, Bot
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError as e:
    TELEGRAM_AVAILABLE = False
    Update = None
    Bot = None
    Application = None
    CommandHandler = None
    MessageHandler = None
    filters = None
    ContextTypes = None
    logger.warning(f"python-telegram-bot not available: {e}. Install with: pip install python-telegram-bot")

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

class TelegramBot:
    """Telegram bot for cursor-enhanced"""
    
    def __init__(self, config: TelegramConfig, openclaw_integration=None):
        if not TELEGRAM_AVAILABLE or Update is None:
            raise RuntimeError("python-telegram-bot required. Install with: pip install python-telegram-bot")
        
        self.config = config
        self.openclaw = openclaw_integration
        self.application = None
        self.bot = None
        self.pairing_store_path = os.path.expanduser("~/.cursor-enhanced/telegram-pairings.json")
        self.paired_users: set = set()
        self.pending_pairings: Dict[str, str] = {}  # chat_id -> pairing_code
        self._pairings_loaded = False
        self._load_pairings(force_reload=True)
    
    def _load_pairings(self, force_reload: bool = False):
        """Load paired users and pending pairings from disk"""
        old_paired_count = len(self.paired_users) if hasattr(self, 'paired_users') else 0
        old_paired_set = set(self.paired_users) if hasattr(self, 'paired_users') else set()
        
        if not hasattr(self, 'paired_users'):
            self.paired_users: set = set()
        if not hasattr(self, 'pending_pairings'):
            self.pending_pairings: Dict[str, str] = {}
        
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
                                if force_reload or len(new_paired) != old_paired_count:
                                    logger.info(f"Loaded {len(self.paired_users)} paired users from disk (was {old_paired_count}, file had: {paired_list}, converted to: {list(new_paired)})")
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
        
        if self.config.dm_policy == "open":
            if self.config.allow_from:
                if "*" in self.config.allow_from:
                    return True
                user_str = str(user_id)
                username_lower = username.lower() if username else None
                for allowed in self.config.allow_from:
                    if user_str == allowed or (username_lower and username_lower == allowed.lower()):
                        return True
                return False
            return True  # Open policy with no allowlist = allow all
        
        # Pairing policy - check if user_id is in paired_users
        # Check if user_id is in paired_users (all should be ints now)
        logger.debug(f"About to check: user_id_int={user_id_int} (type={type(user_id_int)}), self.paired_users={self.paired_users} (type={type(self.paired_users)}), len={len(self.paired_users) if hasattr(self.paired_users, '__len__') else 'N/A'}")
        
        # Explicit check with detailed logging
        is_paired = False
        if user_id_int in self.paired_users:
            is_paired = True
            logger.info(f"✅ User {user_id_int} is paired (found in set), allowing access")
        else:
            # Try explicit iteration to see what's in the set
            logger.error(f"❌ User {user_id_int} (type={type(user_id_int)}) NOT found in paired_users set")
            logger.error(f"   paired_users set contents: {list(self.paired_users)}")
            logger.error(f"   paired_users set type: {type(self.paired_users)}")
            for uid in self.paired_users:
                logger.error(f"   Comparing: {user_id_int} == {uid} (type {type(uid)})? {user_id_int == uid}")
                logger.error(f"   Comparing: {user_id_int} is {uid}? {user_id_int is uid}")
        
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
                    "Hello! I'm cursor-enhanced with OpenClaw integration. "
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
/status - Show bot status and available tools

You can also just send me messages and I'll respond using my available tools."""
        
        await update.message.reply_text(help_text)
    
    async def _handle_status(self, update, context):
        """Handle /status command"""
        status_parts = ["**Cursor-Enhanced Status**\n"]
        
        if self.openclaw:
            tools = self.openclaw.list_tools()
            status_parts.append(f"Available tools: {len(tools)}")
            for tool in tools[:5]:  # Show first 5
                name = tool.get('name', 'unknown')
                status_parts.append(f"- {name}")
            
            skills = self.openclaw.list_skills()
            if skills:
                status_parts.append(f"\nAvailable skills: {len(skills)}")
                status_parts.append(", ".join(skills[:5]))
        else:
            status_parts.append("OpenClaw integration: Not available")
        
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
        import sys
        
        # Get the path to cursor-enhanced (the current script)
        # We'll call cursor-enhanced itself with the message
        cursor_enhanced_path = os.path.expanduser("~/.local/bin/cursor-enhanced")
        if not os.path.exists(cursor_enhanced_path):
            # Try to find it in PATH
            import shutil
            cursor_enhanced_path = shutil.which("cursor-enhanced")
            if not cursor_enhanced_path:
                # Fallback: use python to run main.py
                script_dir = os.path.dirname(os.path.abspath(__file__))
                main_py = os.path.join(os.path.dirname(script_dir), "main.py")
                if os.path.exists(main_py):
                    cursor_enhanced_path = "python3"
                    cmd = [cursor_enhanced_path, main_py, "--enable-openclaw", "-p", message]
                else:
                    return "Error: Could not find cursor-enhanced. Please check installation."
            else:
                cmd = [cursor_enhanced_path, "--enable-openclaw", "-p", message]
        else:
            cmd = ["bash", cursor_enhanced_path, "--enable-openclaw", "-p", message]
        
        try:
            logger.info(f"Processing Telegram message from user {user_id}: {message[:100]}")
            
            # Run cursor-enhanced and capture output
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                cwd=os.path.expanduser("~")
            )
            
            if result.returncode == 0:
                response = result.stdout.strip()
                if not response:
                    response = "I processed your message but didn't get a response."
                logger.info(f"Response generated: {len(response)} characters")
                return response
            else:
                error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                logger.error(f"cursor-enhanced error (code {result.returncode}): {error_msg}")
                # Try to extract useful error message
                if "Error" in error_msg or "error" in error_msg.lower():
                    return f"Sorry, I encountered an error: {error_msg[:500]}"
                return "Sorry, I encountered an error processing your message. Please try again."
        except subprocess.TimeoutExpired:
            logger.warning("Message processing timed out")
            return "Sorry, the request timed out. Please try again with a simpler question."
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
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
    
    return TelegramConfig(
        bot_token=bot_token,
        enabled=telegram_config.get("enabled", True),
        dm_policy=telegram_config.get("dmPolicy", "pairing"),
        allow_from=telegram_config.get("allowFrom"),
        groups=telegram_config.get("groups"),
        webhook_url=telegram_config.get("webhookUrl"),
        webhook_secret=telegram_config.get("webhookSecret")
    )

async def run_telegram_bot(config: Optional[TelegramConfig] = None, openclaw_integration=None):
    """Run the Telegram bot"""
    if not TELEGRAM_AVAILABLE or Update is None:
        raise RuntimeError("python-telegram-bot required. Install with: pip install python-telegram-bot")
    
    if config is None:
        config = load_telegram_config()
    
    if not config or not config.bot_token:
        raise ValueError("Telegram bot token is required. Set TELEGRAM_BOT_TOKEN env var or configure in ~/.cursor-enhanced-config.json")
    
    bot = TelegramBot(config, openclaw_integration)
    await bot.start()
    
    # Keep running until interrupted
    try:
        # Keep the event loop running
        print("Press Ctrl+C to stop the bot...")
        await asyncio.Event().wait()  # Wait indefinitely
    except KeyboardInterrupt:
        print("\nStopping Telegram bot...")
        logger.info("Stopping Telegram bot...")
        await bot.stop()
        print("Telegram bot stopped.")
