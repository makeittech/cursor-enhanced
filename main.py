import sys
import os
import json
import subprocess
import argparse
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import asyncio

# OpenClaw integration imports
OPENCLAW_AVAILABLE = False
try:
    from openclaw_integration import get_openclaw_integration, SessionEntry
    from mcp_tools import get_mcp_client
    OPENCLAW_AVAILABLE = True
except ImportError as e:
    # Logger will be set up later, just mark as unavailable
    pass

DEFAULT_HISTORY_FILE = os.path.expanduser("~/.cursor-enhanced-history.json")
CONFIG_FILE = os.path.expanduser("~/.cursor-enhanced-config.json")
DEFAULT_HISTORY_LIMIT = 10
TOKEN_LIMIT = 100000
# Approximating 1 token as 4 characters
CHARS_PER_TOKEN = 4

# Logging configuration
LOG_DIR = os.path.expanduser("~/.cursor-enhanced/logs")
LOG_FILE = os.path.join(LOG_DIR, "cursor-enhanced.log")

def setup_logging():
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    logger = logging.getLogger("cursor_enhanced")
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        # Rotate logs: Max 5MB, keep 5 backups
        handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger

logger = setup_logging()

def get_history_file(chat_name=None):
    if not chat_name:
        return DEFAULT_HISTORY_FILE
    
    # Sanitize chat name to be safe for filename
    safe_name = "".join(c for c in chat_name if c.isalnum() or c in ('_', '-'))
    if not safe_name:
        safe_name = "default"
    
    return os.path.expanduser(f"~/.cursor-enhanced-history-{safe_name}.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def load_history(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_history(history, filepath):
    with open(filepath, 'w') as f:
        json.dump(history, f, indent=2)

def estimate_tokens(text):
    return len(text) // CHARS_PER_TOKEN

def format_message(role, content):
    return f"{role}: {content}\n\n"

def get_history_fitting_token_limit(history, max_tokens, system_prompt_tokens, user_prompt_tokens):
    """
    Selects as many recent messages as possible that fit within the token limit.
    Always tries to preserve the summary (first message if role is system) if present.
    """
    if not history:
        return [], 0

    available_tokens = max_tokens - system_prompt_tokens - user_prompt_tokens
    # Reserve a safety buffer
    available_tokens -= 1000 
    
    if available_tokens <= 0:
        return [], 0

    # Check for summary
    summary_msg = None
    remaining_history = history
    if history[0].get('role') == 'system':
        summary_msg = history[0]
        remaining_history = history[1:]
    
    selected_messages = []
    current_tokens = 0
    
    # If summary exists, include it first
    if summary_msg:
        summary_text = format_message("SYSTEM SUMMARY", summary_msg['content'])
        summary_tokens = estimate_tokens(summary_text)
        if summary_tokens < available_tokens:
            selected_messages.append(summary_msg)
            current_tokens += summary_tokens
        else:
            # Summary is too big? This is bad.
            # We will still try to include it, but maybe we need to re-summarize everything.
            # For now, let's just proceed with other messages if summary takes everything? 
            # No, if summary takes everything, we have no space for context.
            pass

    # Now add recent messages from the end
    temp_messages = []
    for item in reversed(remaining_history):
        role = "User" if item['role'] == 'user' else "Agent"
        msg_text = format_message(role, item['content'])
        msg_tokens = estimate_tokens(msg_text)
        
        if current_tokens + msg_tokens <= available_tokens:
            temp_messages.append(item)
            current_tokens += msg_tokens
        else:
            break
            
    # Reverse back to chronological order and append to selected
    selected_messages.extend(reversed(temp_messages))
    
    return selected_messages, current_tokens

def format_history_for_prompt(history):
    if not history:
        return ""

    formatted_context = "=== START OF CONVERSATION HISTORY ===\n"
    for item in history:
        role = "User" if item['role'] == 'user' else "Agent"
        if item['role'] == 'system':
            role = "SYSTEM SUMMARY"
            
        content = item['content']
        formatted_context += f"{role}: {content}\n\n"
    formatted_context += "=== END OF CONVERSATION HISTORY ===\n\n"
    
    return formatted_context

def summarize_history(history, cursor_flags):
    # Construct a prompt to summarize the history
    # We will summarize the FIRST half of the history to compress it.
    
    if len(history) < 2:
        return history

    # We will summarize the oldest 50% of messages.
    split_idx = len(history) // 2
    
    old_messages = history[:split_idx]
    recent_messages = history[split_idx:]
    
    # Format old messages for the agent
    text_to_summarize = ""
    for item in old_messages:
        role = "User" if item['role'] == 'user' else "Agent"
        text_to_summarize += f"{role}: {item['content']}\n\n"
        
    summary_prompt = (
        "Please provide a comprehensive summary of the following conversation history. "
        "Retain all key technical details, code snippets, decisions, and context. "
        "The summary should be dense and information-rich to serve as context for future interactions. "
        "Do not output anything else but the summary.\n\n"
        f"{text_to_summarize}"
    )
    
    # Call cursor-agent to summarize
    print("Auto-summarizing history...", file=sys.stderr)
    logger.info("Starting auto-summarization of history.")
    logger.info(f"Messages to summarize: {len(old_messages)}")
    
    try:
        # We use --print to get stdout
        cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
        cmd = ["bash", cursor_agent_path] + cursor_flags + ["-p", summary_prompt]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180 # Increased timeout for better summarization
        )
        
        if result.returncode == 0:
            summary = result.stdout.strip()
            
            logger.info("Summarization successful.")
            logger.info(f"Summary length: {len(summary)} chars")
            logger.info(f"Summary content: {summary}")
            
            # Create a new history entry for the summary
            summary_entry = {
                "role": "system", 
                "content": f"Previous conversation summary: {summary}"
            }
            # New history is Summary + Recent Messages
            return [summary_entry] + recent_messages
        else:
            error_msg = f"Summarization failed: {result.stderr}"
            print(error_msg, file=sys.stderr)
            logger.error(error_msg)
            return history
            
    except Exception as e:
        error_msg = f"Summarization error: {e}"
        print(error_msg, file=sys.stderr)
        logger.exception(error_msg)
        return history

def main():
    # Load config first to check for defaults
    config = load_config()

    # Determine default history limit precedence:
    # 1. Environment variable
    # 2. Config file
    # 3. Hardcoded default
    env_limit = os.environ.get("CURSOR_ENHANCED_HISTORY_LIMIT")
    
    default_limit = None # Default is now None (Token Limit based)
    
    if env_limit and env_limit.isdigit():
        default_limit = int(env_limit)
    elif "history_limit" in config and isinstance(config["history_limit"], int):
        default_limit = config["history_limit"]
    
    # If explicitly None, we default to token limit, but for argparser default=None works.

    parser = argparse.ArgumentParser(description="Wrapper for cursor-agent with history context")
    parser.add_argument("--history-limit", type=int, default=default_limit, help=f"Number of previous messages to include (default: max fitting tokens)")
    parser.add_argument("--clear-history", action="store_true", help="Clear conversation history")
    parser.add_argument("--view-history", action="store_true", help="View conversation history")
    parser.add_argument("--system-prompt", type=str, default="default", help="Name of the system prompt configuration to use")
    parser.add_argument("--chat", type=str, default=None, help="Name of the chat session to use")
    parser.add_argument("--model", type=str, default=None, help="Model to use (e.g., gpt-5, sonnet-4)")
    parser.add_argument("--agent-id", type=str, default=None, help="Agent ID for multi-agent routing (OpenClaw-style)")
    parser.add_argument("--session-id", type=str, default=None, help="Session ID for session management")
    parser.add_argument("--list-tools", action="store_true", help="List available MCP tools")
    parser.add_argument("--list-skills", action="store_true", help="List available skills")
    parser.add_argument("--gateway-url", type=str, default=None, help="Gateway WebSocket URL (OpenClaw-style)")
    parser.add_argument("--enable-openclaw", action="store_true", default=True, help="Enable OpenClaw integration features")
    parser.add_argument("--telegram", action="store_true", help="Start Telegram bot")
    parser.add_argument("--telegram-approve", type=str, metavar="CODE", help="Approve Telegram pairing code")
    
    # We use parse_known_args to separate wrapper args from cursor-agent args/prompt
    args, unknown_args = parser.parse_known_args()
    
    # Handle Telegram-specific commands
    if args.telegram_approve:
        # Approve Telegram pairing
        try:
            from telegram_integration import load_telegram_config, TelegramBot, TelegramConfig
            config = load_telegram_config()
            if not config:
                print("Error: Telegram not configured. Set TELEGRAM_BOT_TOKEN or configure in ~/.cursor-enhanced-config.json")
                return
            
            # Create a temporary bot instance to approve pairing
            # We don't need to start it, just use the approve method
            bot = TelegramBot(config, openclaw_integration=None)
            if bot.approve_pairing(args.telegram_approve):
                print(f"✅ Pairing code {args.telegram_approve} approved successfully!")
                print("You can now send messages to the bot.")
            else:
                print(f"❌ Pairing code {args.telegram_approve} not found or already used.")
                print("Make sure you're using the exact code shown by the bot.")
        except ImportError:
            print("Telegram integration not available. Install with: pip install python-telegram-bot")
        except Exception as e:
            print(f"Error approving pairing: {e}")
        return
    
    if args.telegram:
        # Start Telegram bot
        try:
            from telegram_integration import run_telegram_bot, load_telegram_config
            from openclaw_integration import get_openclaw_integration
            
            config = load_telegram_config()
            if not config:
                print("Error: Telegram bot token required.")
                print("Set TELEGRAM_BOT_TOKEN environment variable or configure in ~/.cursor-enhanced-config.json")
                return
            
            openclaw = None
            if OPENCLAW_AVAILABLE and args.enable_openclaw:
                try:
                    from openclaw_integration import get_openclaw_integration
                    openclaw = get_openclaw_integration()
                except Exception as e:
                    logger.warning(f"Failed to initialize OpenClaw: {e}")
            
            print("Starting Telegram bot...")
            asyncio.run(run_telegram_bot(config, openclaw))
        except ImportError:
            print("Telegram integration not available. Install with: pip install python-telegram-bot")
        except Exception as e:
            print(f"Error starting Telegram bot: {e}")
        return

    # Initialize OpenClaw integration if available
    openclaw = None
    mcp_client = None
    if OPENCLAW_AVAILABLE and args.enable_openclaw:
        try:
            from openclaw_integration import get_openclaw_integration
            from mcp_tools import get_mcp_client
            openclaw = get_openclaw_integration()
            mcp_client = get_mcp_client()
            
            # Connect to gateway if URL provided
            if args.gateway_url:
                try:
                    asyncio.run(openclaw.connect_gateway(args.gateway_url, config=config))
                except Exception as e:
                    logger.warning(f"Failed to connect to gateway: {e}")
        except Exception as e:
            logger.warning(f"Failed to initialize OpenClaw integration: {e}")

    # Handle tool/skill listing
    if args.list_tools:
        if mcp_client:
            tools = mcp_client.discover_mcp_tools()
            openclaw_tools = openclaw.list_tools() if openclaw else []
            print("Available MCP Tools:")
            for tool in tools:
                print(f"  - {tool.get('name', 'unknown')}: {tool.get('description', '')}")
            if openclaw_tools:
                print("\nOpenClaw Tools:")
                for tool in openclaw_tools:
                    print(f"  - {tool.get('name', 'unknown')}: {tool.get('description', '')}")
        else:
            print("MCP tools not available")
        return
    
    if args.list_skills:
        if openclaw:
            skills = openclaw.list_skills()
            print("Available Skills:")
            for skill in skills:
                print(f"  - {skill}")
        else:
            print("Skills not available")
        return

    history_file = get_history_file(args.chat)

    if args.clear_history:
        if os.path.exists(history_file):
            os.remove(history_file)
        msg = f"History cleared for session: {args.chat if args.chat else 'default'}"
        print(msg)
        logger.info(msg)
        return
        
    if args.view_history:
        history = load_history(history_file)
        if not history:
            print(f"No history found for session: {args.chat if args.chat else 'default'}")
        else:
            print(f"--- History for session: {args.chat if args.chat else 'default'} ---\n")
            for item in history:
                role = "User" if item['role'] == 'user' else "Agent"
                # If role is system (summary), display it differently
                if item['role'] == 'system':
                    role = "SYSTEM SUMMARY"
                
                content = item['content']
                print(f"[{role}]")
                print(f"{content}\n")
                print("-" * 40 + "\n")
        return

    # Robustly separate flags from the prompt.
    flags_with_args = {
        '--api-key', 
        '-H', '--header', 
        '--output-format', 
        '--workspace'
    }
    
    cursor_flags = []
    
    # Pass explicit arguments if present
    if args.model:
        cursor_flags.extend(["--model", args.model])

    user_prompt_parts = []
    
    i = 0
    while i < len(unknown_args):
        arg = unknown_args[i]
        
        if arg.startswith("-"):
            cursor_flags.append(arg)
            if arg in flags_with_args:
                if i + 1 < len(unknown_args):
                    cursor_flags.append(unknown_args[i+1])
                    i += 1
            elif arg == '--resume':
                if i + 1 < len(unknown_args) and not unknown_args[i+1].startswith("-"):
                     cursor_flags.append(unknown_args[i+1])
                     i += 1
        else:
            user_prompt_parts.append(arg)
        
        i += 1
            
    user_prompt = " ".join(user_prompt_parts)
    
    if not user_prompt:
        # Use direct path for compatibility
        cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
        subprocess.run(["bash", cursor_agent_path] + unknown_args)
        return

    # Log user prompt
    logger.info(f"User Request (Session: {args.chat if args.chat else 'default'}): {user_prompt}")

    # OpenClaw session management
    session_entry = None
    if openclaw:
        try:
            # Create or get session
            session_id = args.session_id or args.chat or "default"
            agent_id = args.agent_id or "main"
            session_key = f"{agent_id}:{session_id}"
            
            existing_session = openclaw.get_session(session_key)
            if existing_session:
                session_entry = existing_session
            else:
                session_entry = openclaw.create_session(
                    session_id=session_id,
                    agent_id=agent_id,
                    channel=args.chat
                )
            
            # Set presence
            openclaw.presence_manager.set_presence(session_key, True)
            openclaw.presence_manager.set_typing(session_key, True)
        except Exception as e:
            logger.warning(f"Session management error: {e}")

    # Load config and history
    history = load_history(history_file)
    
    # Resolve system prompt
    system_prompt_content = ""
    if args.system_prompt:
        # Check if it is a key in config
        if "system_prompts" in config and args.system_prompt in config["system_prompts"]:
            system_prompt_content = config["system_prompts"][args.system_prompt]
        elif args.system_prompt != "default": 
            print(f"Warning: System prompt config '{args.system_prompt}' not found. Using default if available.", file=sys.stderr)
            if "system_prompts" in config and "default" in config["system_prompts"]:
                system_prompt_content = config["system_prompts"]["default"]
        else:
            # Default requested but not found, check if 'default' key exists
            if "system_prompts" in config and "default" in config["system_prompts"]:
                system_prompt_content = config["system_prompts"]["default"]
    
    # Calculate Token Usage and Prepare Context
    system_prompt_tokens = estimate_tokens(system_prompt_content)
    user_prompt_tokens = estimate_tokens("User Current Request: " + user_prompt)
    
    # If args.history_limit is set, we use it as a hard limit on count.
    # Otherwise, we use token limit logic.
    
    if args.history_limit is not None:
        # Legacy mode: Use fixed number of messages
        context_history = history[-args.history_limit:] if args.history_limit > 0 else []
        formatted_history = format_history_for_prompt(context_history)
        total_text = system_prompt_content + formatted_history + user_prompt
        
        # Check limit just for logging/warning, or maybe trigger compression if it's REALLY big?
        # But user asked for specific limit.
        # However, we should still respect TOKEN_LIMIT overall to avoid API errors.
        if estimate_tokens(total_text) > TOKEN_LIMIT:
            logger.info(f"Token limit exceeded in fixed-limit mode. Triggering summarization.")
            history = summarize_history(history, cursor_flags)
            save_history(history, history_file)
            # Re-fetch
            context_history = history[-args.history_limit:] if args.history_limit > 0 else []
            formatted_history = format_history_for_prompt(context_history)
    else:
        # Smart Token Mode
        # First, check if TOTAL history (all of it) fits.
        all_history_text = format_history_for_prompt(history)
        total_estimated = estimate_tokens(all_history_text) + system_prompt_tokens + user_prompt_tokens
        
        if total_estimated > TOKEN_LIMIT:
             # We are over the limit. Trigger compression on the main history first.
             logger.info(f"Total history ({total_estimated} tokens) exceeds limit. Summarizing...")
             history = summarize_history(history, cursor_flags)
             save_history(history, history_file)
             
             # Re-calculate with compressed history
             # Even after compression, we might still be over (if recent messages are huge).
             # So we proceed to select what fits.
        
        # Select what fits
        context_history, tokens_used = get_history_fitting_token_limit(
            history, 
            TOKEN_LIMIT, 
            system_prompt_tokens, 
            user_prompt_tokens
        )
        formatted_history = format_history_for_prompt(context_history)
        logger.info(f"Selected {len(context_history)} messages ({tokens_used} tokens) for context.")

    full_prompt_parts = []
    if system_prompt_content:
        full_prompt_parts.append(f"System: {system_prompt_content}\n")
    
    # Add OpenClaw tools information to system prompt
    if openclaw and args.enable_openclaw:
        tools_info = []
        if mcp_client:
            mcp_tools = mcp_client.discover_mcp_tools()
            if mcp_tools:
                tools_info.append("\n=== Available MCP Tools ===")
                for tool in mcp_tools[:10]:  # Limit to first 10
                    tools_info.append(f"- {tool.get('name')}: {tool.get('description', '')}")
        
        openclaw_tools = openclaw.list_tools()
        if openclaw_tools:
            tools_info.append("\n=== Available OpenClaw Tools ===")
            for tool in openclaw_tools:
                name = tool.get('name', 'unknown')
                desc = tool.get('description', '')
                if desc:
                    tools_info.append(f"- {name}: {desc}")
                else:
                    tools_info.append(f"- {name}")
        
        skills = openclaw.list_skills()
        if skills:
            tools_info.append(f"\n=== Available Skills ({len(skills)}) ===")
            tools_info.append(", ".join(skills[:10]))  # Limit to first 10
        
        if tools_info:
            full_prompt_parts.append("\n".join(tools_info) + "\n")
            full_prompt_parts.append("\n**IMPORTANT: You have access to these tools and can use them to help the user.**\n")
            full_prompt_parts.append("When the user asks what you can do or what tools you have, list ALL available tools from above.\n")
            full_prompt_parts.append("You can use these tools by describing what you want to do - the system will execute them for you.\n\n")
    
    full_prompt_parts.append(formatted_history)
    full_prompt_parts.append("User Current Request: " + user_prompt)
    
    full_prompt = "".join(full_prompt_parts)
    
    # Construct command
    # Use direct path to cursor-agent via bash for compatibility
    cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
    cmd = ["bash", cursor_agent_path] + cursor_flags + [full_prompt]
    
    # Run and capture output
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1, 
        universal_newlines=True
    )
    
    agent_response = ""
    
    # Stream stdout
    if process.stdout:
        for line in process.stdout:
            print(line, end="")
            agent_response += line
            
    # Capture stderr
    if process.stderr:
        for line in process.stderr:
            print(line, end="", file=sys.stderr)
            
    process.wait()
    
    if process.returncode == 0:
        # Save to history
        history.append({"role": "user", "content": user_prompt})
        history.append({"role": "agent", "content": agent_response.strip()})
        save_history(history, history_file)
        
        # Update OpenClaw session
        if openclaw and session_entry:
            try:
                session_key = session_entry.session_key
                openclaw.presence_manager.set_typing(session_key, False)
                # Update session metadata if needed
                if session_entry:
                    session_entry.metadata = session_entry.metadata or {}
                    session_entry.metadata["last_interaction"] = datetime.now().isoformat()
                    openclaw.session_store.set(session_entry.session_key, session_entry)
            except Exception as e:
                logger.warning(f"Failed to update session: {e}")
        
        # Log final result
        logger.info(f"Agent Response: {agent_response.strip()}")
    else:
        logger.error(f"Agent execution failed with return code {process.returncode}")
        # Update presence on error
        if openclaw and session_entry:
            try:
                openclaw.presence_manager.set_typing(session_entry.session_key, False)
            except:
                pass

if __name__ == "__main__":
    main()
