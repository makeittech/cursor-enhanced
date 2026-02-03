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
        self.paired_users: set = set()  # Users who have been approved
        self.pending_pairings: Dict[str, str] = {}  # chat_id -> pairing_code
        
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
        await self.application.updater.start_polling()
        logger.info("Telegram bot started and polling for messages")
    
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
        
        # Pairing policy
        if user_id in self.paired_users:
            return True
        
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
                self.pending_pairings[str(chat.id)] = code
                await update.message.reply_text(
                    f"Hello! To start using this bot, please approve the pairing code: **{code}**\n\n"
                    f"Run this command on your system:\n"
                    f"`cursor-enhanced telegram approve {code}`\n\n"
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
        if chat.type == "private" and not self._is_allowed_user(user.id, user.username):
            # Check if they have a pending pairing
            chat_id_str = str(chat.id)
            if chat_id_str in self.pending_pairings:
                code = self.pending_pairings[chat_id_str]
                await message.reply_text(
                    f"Please approve the pairing code first: **{code}**\n\n"
                    f"Run: `cursor-enhanced telegram approve {code}`",
                    parse_mode="Markdown"
                )
            else:
                code = self._generate_pairing_code()
                self.pending_pairings[chat_id_str] = code
                await message.reply_text(
                    f"Pairing required. Code: **{code}**\n\n"
                    f"Run: `cursor-enhanced telegram approve {code}`",
                    parse_mode="Markdown"
                )
            return
        
        # Process message through cursor-enhanced
        user_message = message.text
        
        try:
            # Send typing indicator
            await context.bot.send_chat_action(chat_id=chat.id, action="typing")
            
            # Route message through cursor-enhanced
            # This would need to integrate with the main.py logic
            # For now, we'll create a simple response
            response = await self._process_message(user_message, user.id, chat.id)
            
            # Send response
            await message.reply_text(response)
        except Exception as e:
            logger.error(f"Error processing Telegram message: {e}")
            await message.reply_text(f"Sorry, I encountered an error: {str(e)}")
    
    async def _process_message(self, message: str, user_id: int, chat_id: int) -> str:
        """Process a message through cursor-enhanced"""
        import subprocess
        import os
        
        # Route message through cursor-agent
        cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
        
        # Build command with OpenClaw enabled
        cmd = ["bash", cursor_agent_path, "--enable-openclaw", "-p", message]
        
        try:
            # Run cursor-agent and capture output
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120
            )
            
            if result.returncode == 0:
                response = result.stdout.strip()
                if not response:
                    response = "I processed your message but didn't get a response."
                return response
            else:
                error_msg = result.stderr.strip() or "Unknown error"
                logger.error(f"cursor-agent error: {error_msg}")
                return f"Sorry, I encountered an error processing your message: {error_msg}"
        except subprocess.TimeoutExpired:
            return "Sorry, the request timed out. Please try again."
        except Exception as e:
            logger.error(f"Error processing message: {e}")
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
    
    # Keep running
    try:
        await asyncio.Event().wait()  # Wait indefinitely
    except KeyboardInterrupt:
        logger.info("Stopping Telegram bot...")
        await bot.stop()
