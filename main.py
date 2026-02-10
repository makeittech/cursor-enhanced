import sys
import os
import json
import subprocess
import time
import argparse
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
import asyncio
import re
from typing import Optional, Dict, Any, Tuple, List

# OpenClaw integration imports
OPENCLAW_AVAILABLE = False
try:
    from openclaw_integration import get_openclaw_integration, SessionEntry
    from mcp_tools import get_mcp_client
    OPENCLAW_AVAILABLE = True
except ImportError as e:
    # Logger will be set up later, just mark as unavailable
    pass

try:
    from cursor_enhanced.version import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "0.0.0"

DEFAULT_HISTORY_FILE = os.path.expanduser("~/.cursor-enhanced-history.json")
CONFIG_FILE = os.path.expanduser("~/.cursor-enhanced-config.json")
DEFAULT_HISTORY_LIMIT = 10
TOKEN_LIMIT = 100000
# Approximating 1 token as 4 characters
CHARS_PER_TOKEN = 4

# Memory flush settings (OpenClaw-style pre-compaction)
MEMORY_FLUSH_NO_REPLY = "NO_REPLY"
DEFAULT_MEMORY_FLUSH_SOFT_TOKENS = 4000
DEFAULT_MEMORY_FLUSH_RESERVE_TOKENS_FLOOR = 20000
DEFAULT_MEMORY_FLUSH_PROMPT = (
    "Pre-compaction memory flush. Store durable memories now. "
    "Write durable facts to MEMORY.md and day-to-day notes to memory/YYYY-MM-DD.md "
    "(create memory/ if needed). Preserve role separation (User vs Agent) in the notes. "
    f"If nothing to store, reply with {MEMORY_FLUSH_NO_REPLY}."
)
DEFAULT_MEMORY_FLUSH_SYSTEM_PROMPT = (
    "Pre-compaction memory flush turn. The session is near auto-compaction; "
    "capture durable memories to disk with role separation (User vs Agent). "
    f"If no user-visible reply is needed, start with {MEMORY_FLUSH_NO_REPLY}."
)

# Smart delegation instructions (appended to system prompt when smart_delegate tool is available)
SMART_DELEGATE_SYSTEM_PROMPT = (
    "\n\n=== Smart Delegation (core skill) ===\n"
    "You have the ability to delegate complex tasks to a more powerful AI model with a cleaner context.\n"
    "When a task is complex (architecture, deep analysis, multi-step implementation, security review, "
    "large refactoring, production debugging, etc.), you SHOULD delegate it:\n\n"
    "To delegate, write in your response: 'smart delegate: <clean task description>'\n"
    "The system will:\n"
    "1. Analyze task complexity and score it\n"
    "2. Discover all available models and their capabilities\n"
    "3. Select the optimal model for the task's complexity level\n"
    "4. Announce the choice and reasoning to the user (so they understand what model is used and why)\n"
    "5. Send ONLY the task (clean context, no conversation history noise) to the chosen model\n"
    "6. Return the delegate's response\n\n"
    "Model tiers (auto-selected based on complexity):\n"
    "- XHIGH (deep reasoning): opus-thinking, gpt-5.x-codex-xhigh — for architecture, complex analysis\n"
    "- HIGH (strong): opus, gpt-5.x-codex-high — for complex code, thorough review\n"
    "- MID (balanced): sonnet-thinking, gpt-5.x-codex — moderate complexity\n"
    "- LOW/FAST: simpler models for quick tasks\n\n"
    "IMPORTANT: Always announce to the user what you're delegating and why. "
    "The user should see the model choice and reasoning. This transparency is a core feature.\n"
    "Example: 'This requires deep architecture analysis. smart delegate: Design a microservices "
    "architecture for the payment system with the following requirements...'\n"
)

# Appended to system prompt when running from Telegram (remind/plan/schedule)
TELEGRAM_SYSTEM_PROMPT_REACH = (
    "\n\n=== When the user asks to remind, plan, or reach them at a set time ===\n"
    "Use the reach-at-time feature so they get a notification (e.g. Telegram) at the requested time.\n"
    "- In N minutes (one-shot): run: cursor-enhanced --reach-add --reach-in-minutes N --reach-message \"Your message\"\n"
    "- Daily at a fixed time (e.g. 09:00): run: cursor-enhanced --reach-add --reach-time 09:00 --reach-message \"Your message\"\n"
    "- Custom cron (e.g. weekdays 20:00): run: cursor-enhanced --reach-add --reach-cron \"0 20 * * 1-5\" --reach-message \"Your message\"\n"
    "Default timezone for daily/cron is Europe/Kyiv (or set reach_timezone in config). Use --reach-timezone TZ to override.\n"
    "- List schedules: cursor-enhanced --reach-list\n"
    "- Remove a schedule: cursor-enhanced --reach-remove <id>\n"
    "Reach fires via cron every minute; the user must have cron set to run: cursor-enhanced --reach-fire every minute.\n"
)

# Appended to system prompt when running from Telegram (project self-improvement)
TELEGRAM_SYSTEM_PROMPT_PROJECT = (
    "\n\n=== cursor-enhanced project (you work from ~/cursor-enhanced) ===\n"
    "When improving this project: update code → run tests (from project root) → reload the service. "
    "Commit when the scope is done; push only after user confirms. "
    "Full workflow: .cursor/rules/cursor-enhanced-workflow.mdc — follow it so you don't forget.\n"
)

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

def sanitize_chat_name(chat_name: Optional[str]) -> Optional[str]:
    if not chat_name:
        return None
    safe_name = "".join(c for c in chat_name if c.isalnum() or c in ('_', '-'))
    return safe_name or "default"

def get_history_file(chat_name: Optional[str] = None) -> str:
    safe_name = sanitize_chat_name(chat_name)
    if not safe_name:
        return DEFAULT_HISTORY_FILE
    return os.path.expanduser(f"~/.cursor-enhanced-history-{safe_name}.json")

def get_history_meta_file(chat_name: Optional[str] = None) -> str:
    safe_name = sanitize_chat_name(chat_name)
    meta_dir = os.path.expanduser("~/.cursor-enhanced")
    if not safe_name:
        return os.path.join(meta_dir, "history-meta.json")
    return os.path.join(meta_dir, f"history-meta-{safe_name}.json")

def load_history_meta(filepath: str) -> Dict[str, Any]:
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except json.JSONDecodeError:
            return {}
    return {}

def save_history_meta(meta: Dict[str, Any], filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def _env_for_cursor_agent() -> Dict[str, str]:
    """Build env for cursor-agent subprocess so main agent can use MCP (e.g. Home Assistant)."""
    env = os.environ.copy()
    config = load_config()
    path = config.get("mcp_config_path") or config.get("main_agent_mcp_config_path")
    if path:
        expanded = os.path.expanduser(str(path))
        if os.path.isfile(expanded):
            env["CURSOR_MCP_CONFIG_PATH"] = expanded
        else:
            env["CURSOR_MCP_CONFIG_PATH"] = expanded  # cursor-agent may still use it
    return env

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

async def execute_tools_from_response(response: str, openclaw, logger) -> Optional[str]:
    """Parse agent response for tool usage and execute tools"""
    if not openclaw:
        return None
    
    results = []
    response_lower = response.lower()
    
    def clean_query(query: str) -> str:
        """Clean extracted query by removing quotes, parentheses, and trailing punctuation"""
        if not query:
            return ""
        # Strip whitespace
        query = query.strip()
        # Remove surrounding quotes (single or double)
        query = query.strip('"\'')
        # Remove trailing punctuation and parentheses
        query = query.rstrip('.,;:!?)')
        # Remove leading/trailing whitespace again
        query = query.strip()
        return query
    
    # Check for web search mentions
    web_search_patterns = [
        r"search(?:ing|ed)?\s+(?:the\s+)?web\s+for\s+['\"]?([^'\"]+)['\"]?",
        r"search(?:ing|ed)?\s+for\s+['\"]?([^'\"]+)['\"]?",
        r"looking\s+up\s+['\"]?([^'\"]+)['\"]?",
    ]
    
    for pattern in web_search_patterns:
        matches = re.finditer(pattern, response_lower, re.IGNORECASE)
        for match in matches:
            query = clean_query(match.group(1))
            if query and len(query) > 3:  # Reasonable query length
                try:
                    logger.info(f"Executing web_search tool with query: {query}")
                    # ToolRegistry.execute expects (tool_name, action, params)
                    # For web_search, action is empty string, params is a dict
                    result = await openclaw.tool_registry.execute("web_search", "", {"query": query})
                    if result and not result.get("error"):
                        results.append(f"Web search for '{query}': {json.dumps(result, indent=2)}")
                    else:
                        results.append(f"Web search for '{query}' failed: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    logger.error(f"Error executing web_search: {e}")
                    results.append(f"Web search error: {str(e)}")
    
    # Check for web fetch mentions
    web_fetch_patterns = [
        r"fetch(?:ing|ed)?\s+(?:the\s+)?(?:url|page|website)?\s*(?:at\s+)?(https?://[^\s\)]+)",
        r"fetch(?:ing|ed)?\s+(?:from\s+)?(https?://[^\s\)]+)",
        r"get(?:ting)?\s+(?:the\s+)?(?:content\s+)?(?:from\s+)?(https?://[^\s\)]+)",
    ]
    
    for pattern in web_fetch_patterns:
        matches = re.finditer(pattern, response_lower, re.IGNORECASE)
        for match in matches:
            url = match.group(1).strip()
            if url:
                try:
                    logger.info(f"Executing web_fetch tool with URL: {url}")
                    # ToolRegistry.execute expects (tool_name, action, params)
                    # For web_fetch, action is empty string, params is a dict
                    result = await openclaw.tool_registry.execute("web_fetch", "", {"url": url})
                    if result and not result.get("error"):
                        content = result.get("content", "")[:1000]  # Limit content length
                        results.append(f"Fetched {url}: {content}...")
                    else:
                        results.append(f"Failed to fetch {url}: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    logger.error(f"Error executing web_fetch: {e}")
                    results.append(f"Web fetch error: {str(e)}")
    
    if results:
        return "\n".join(results)
    return None

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

def normalize_non_negative_int(value: Any) -> Optional[int]:
    if not isinstance(value, (int, float)):
        return None
    if not (value == value):  # NaN check
        return None
    int_value = int(value)
    return int_value if int_value >= 0 else None

def ensure_no_reply_hint(text: str) -> str:
    if MEMORY_FLUSH_NO_REPLY in text:
        return text
    return f"{text}\n\nIf no user-visible reply is needed, start with {MEMORY_FLUSH_NO_REPLY}."

def resolve_memory_flush_settings(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    compaction_cfg = config.get("agents", {}).get("defaults", {}).get("compaction", {})
    memory_flush_cfg = compaction_cfg.get("memoryFlush") or config.get("memory_flush") or {}
    enabled = memory_flush_cfg.get("enabled", True)
    if enabled is False:
        return None

    soft_threshold_tokens = normalize_non_negative_int(
        memory_flush_cfg.get("softThresholdTokens")
    ) or DEFAULT_MEMORY_FLUSH_SOFT_TOKENS
    reserve_tokens_floor = normalize_non_negative_int(
        compaction_cfg.get("reserveTokensFloor")
    ) or DEFAULT_MEMORY_FLUSH_RESERVE_TOKENS_FLOOR
    prompt = (memory_flush_cfg.get("prompt") or "").strip() or DEFAULT_MEMORY_FLUSH_PROMPT
    system_prompt = (memory_flush_cfg.get("systemPrompt") or "").strip() or DEFAULT_MEMORY_FLUSH_SYSTEM_PROMPT

    return {
        "enabled": True,
        "soft_threshold_tokens": soft_threshold_tokens,
        "reserve_tokens_floor": reserve_tokens_floor,
        "prompt": ensure_no_reply_hint(prompt),
        "system_prompt": ensure_no_reply_hint(system_prompt),
    }

def should_run_memory_flush(total_tokens: int, settings: Dict[str, Any], meta: Dict[str, Any]) -> bool:
    if total_tokens <= 0:
        return False
    context_window = max(1, int(TOKEN_LIMIT))
    reserve_tokens = max(0, int(settings.get("reserve_tokens_floor", 0)))
    soft_threshold = max(0, int(settings.get("soft_threshold_tokens", 0)))
    threshold = max(0, context_window - reserve_tokens - soft_threshold)
    if threshold <= 0:
        return False
    if total_tokens < threshold:
        return False

    compaction_count = int(meta.get("compaction_count", 0) or 0)
    next_compaction = compaction_count + 1
    last_flush_for = meta.get("memory_flush_compaction_count")
    if isinstance(last_flush_for, int) and last_flush_for == next_compaction:
        return False
    return True

def append_memory_entry(file_path: str, content: str) -> bool:
    if not content or not content.strip():
        return False
    content = content.strip()
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    needs_spacing = os.path.exists(file_path) and os.path.getsize(file_path) > 0
    with open(file_path, 'a', encoding='utf-8') as f:
        if needs_spacing:
            f.write("\n\n")
        f.write(content)
        f.write("\n")
    return True

def _parse_memory_flush_json(output: str) -> Optional[Dict[str, Any]]:
    if not output:
        return None
    try:
        parsed = json.loads(output)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(output[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None

def run_memory_flush(history: List[Dict[str, Any]], cursor_flags: List[str],
                     settings: Dict[str, Any], logger) -> bool:
    if not history:
        return False

    split_idx = max(1, len(history) // 2)
    flush_messages = history[:split_idx]
    formatted_history = format_history_for_prompt(flush_messages)

    flush_prompt = (
        f"{settings.get('system_prompt')}\n\n"
        f"{settings.get('prompt')}\n\n"
        "Return ONLY one of the following:\n"
        f"- {MEMORY_FLUSH_NO_REPLY}\n"
        "- A single JSON object with keys \"memory\" and \"daily\" containing markdown.\n"
        "If a key has no content, use an empty string.\n\n"
        "Conversation history (roles preserved):\n"
        f"{formatted_history}"
    )

    logger.info("Starting memory flush before compaction.")
    try:
        cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
        flush_flags = cursor_flags.copy()
        if "--force" not in flush_flags and "-f" not in flush_flags:
            flush_flags.append("--force")
        cmd = ["bash", cursor_agent_path] + flush_flags + ["-p", flush_prompt]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
            env=_env_for_cursor_agent(),
        )
        if result.returncode != 0:
            logger.warning(f"Memory flush failed: {result.stderr.strip()}")
            return False

        output = result.stdout.strip()
        if not output:
            return False
        if MEMORY_FLUSH_NO_REPLY in output.split():
            return True

        parsed = _parse_memory_flush_json(output)
        if not parsed:
            logger.warning("Memory flush output was not valid JSON; skipping write.")
            return False

        memory_content = str(parsed.get("memory", "") or "").strip()
        daily_content = str(parsed.get("daily", "") or "").strip()

        workspace_dir = os.path.expanduser("~/.cursor-enhanced/workspace")
        memory_dir = os.path.join(workspace_dir, "memory")
        memory_file = os.path.join(workspace_dir, "MEMORY.md")
        daily_file = os.path.join(memory_dir, f"{datetime.now().strftime('%Y-%m-%d')}.md")

        wrote = False
        if memory_content:
            wrote = append_memory_entry(memory_file, memory_content) or wrote
        if daily_content:
            wrote = append_memory_entry(daily_file, daily_content) or wrote
        return True
    except Exception as e:
        logger.exception(f"Memory flush error: {e}")
        return False

def summarize_history(history, cursor_flags) -> Tuple[List[Dict[str, Any]], bool]:
    # Construct a prompt to summarize the history
    # We will summarize the FIRST half of the history to compress it.
    
    if len(history) < 2:
        return history, False

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
        # Ensure --force is in flags for summarization too
        summary_flags = cursor_flags.copy()
        if "--force" not in summary_flags and "-f" not in summary_flags:
            summary_flags.append("--force")
        cmd = ["bash", cursor_agent_path] + summary_flags + ["-p", summary_prompt]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,  # Increased timeout for better summarization
            env=_env_for_cursor_agent(),
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
            return [summary_entry] + recent_messages, True
        else:
            error_msg = f"Summarization failed: {result.stderr}"
            print(error_msg, file=sys.stderr)
            logger.error(error_msg)
            return history, False
            
    except Exception as e:
        error_msg = f"Summarization error: {e}"
        print(error_msg, file=sys.stderr)
        logger.exception(error_msg)
        return history, False

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
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--telegram", action="store_true", help="Start Telegram bot")
    parser.add_argument("--telegram-approve", type=str, metavar="CODE", help="Approve Telegram pairing code")
    parser.add_argument("--telegram-list-pending", action="store_true", help="List pending Telegram pairing codes")
    parser.add_argument("--telegram-list-paired", action="store_true", help="List paired Telegram users")
    parser.add_argument("--telegram-debug", action="store_true", help="Show debug info about Telegram pairings")
    # Reach-at-time: schedule notifications (e.g. Telegram) at set times; use cron to run reach-fire every minute
    parser.add_argument("--reach-fire", action="store_true", help="Run due reach schedules (for cron)")
    parser.add_argument("--reach-list", action="store_true", help="List reach schedules")
    parser.add_argument("--reach-add", action="store_true", help="Add a reach schedule")
    parser.add_argument("--reach-time", type=str, metavar="HH:MM", help="Daily time for reach (e.g. 09:00)")
    parser.add_argument("--reach-cron", type=str, metavar="CRON", help="Cron expression (e.g. '0 9 * * 1-5')")
    parser.add_argument("--reach-message", type=str, metavar="TEXT", help="Message to send when reach fires")
    parser.add_argument("--reach-remove", type=str, metavar="ID", help="Remove reach schedule by id")
    parser.add_argument("--reach-timezone", type=str, metavar="TZ", help="Timezone for daily/cron (e.g. Europe/London); default Europe/Kyiv from config")
    parser.add_argument("--reach-in-minutes", type=int, metavar="N", help="One-shot: fire once in N minutes (UTC)")
    parser.add_argument("--reach-once-at", type=str, metavar="ISO", help="One-shot at ISO datetime (e.g. with Z for UTC)")
    # Scheduled notifications (in-process when Telegram bot runs; same store as scheduler loop)
    parser.add_argument("--schedule-add", action="store_true", help="Add a scheduled Telegram notification")
    parser.add_argument("--schedule-list", action="store_true", help="List scheduled notifications")
    parser.add_argument("--schedule-remove", type=str, metavar="ID", help="Remove a scheduled notification by ID")
    parser.add_argument("--schedule-time", type=str, metavar="TIME", help="For add: HH:MM (daily) or ISO datetime (with --schedule-once)")
    parser.add_argument("--schedule-message", type=str, metavar="TEXT", help="Message to send")
    parser.add_argument("--schedule-once", action="store_true", help="One-shot at given time (use with --schedule-time)")
    parser.add_argument("--schedule-user", type=str, metavar="CHAT_ID", help="Telegram chat ID (default: all paired)")
    
    # We use parse_known_args to separate wrapper args from cursor-agent args/prompt
    args, unknown_args = parser.parse_known_args()

    if args.version:
        print(APP_VERSION)
        return

    # Handle reach-at-time commands (schedules to reach user at set times via Telegram/cron)
    if args.reach_fire:
        try:
            from reach_schedules import fire_due_schedules
            fired = fire_due_schedules()
            if fired:
                for s in fired:
                    print(f"Reach fired: {s.get('id')} -> {s.get('message', '')[:50]}...")
            # Exit 0 so cron doesn't flag errors when nothing was due
        except Exception as e:
            logger.error("reach-fire failed: %s", e)
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.reach_list:
        try:
            from reach_schedules import list_schedules
            schedules = list_schedules()
            if not schedules:
                print("No reach schedules. Add with: cursor-enhanced --reach-add --reach-time 09:00 --reach-message 'Your message'")
                return
            print("Reach schedules (use --reach-fire from cron every minute):")
            for s in schedules:
                spec = s.get("time") or s.get("cron") or s.get("once_at") or "?"
                tz = s.get("timezone") or ""
                tz_str = f"  tz={tz}" if tz else ""
                enabled = "enabled" if s.get("enabled", True) else "disabled"
                print(f"  {s.get('id')}  {spec}{tz_str}  {enabled}  {s.get('channel', 'telegram')}  {s.get('message', '')[:60]}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.reach_add:
        try:
            from reach_schedules import add_schedule
            once_at = None
            if getattr(args, "reach_in_minutes", None) is not None:
                n = args.reach_in_minutes
                if n < 0:
                    raise ValueError("--reach-in-minutes must be >= 0")
                once_at = (datetime.now(timezone.utc) + timedelta(minutes=n)).isoformat()
                entry = add_schedule(once_at=once_at, message=args.reach_message or "", channel="telegram")
            elif getattr(args, "reach_once_at", None):
                once_at = args.reach_once_at.strip()
                entry = add_schedule(once_at=once_at, message=args.reach_message or "", channel="telegram")
            else:
                default_tz = load_config().get("reach_timezone", "Europe/Kyiv")
                entry = add_schedule(
                    time=args.reach_time,
                    cron=args.reach_cron,
                    message=args.reach_message or "",
                    channel="telegram",
                    timezone_name=getattr(args, "reach_timezone", None) or default_tz,
                )
            spec = entry.get("time") or entry.get("cron") or entry.get("once_at") or "?"
            print(f"Added: {entry['id']}  {spec}  {entry['message'][:50]}...")
            print("Add to crontab to run every minute: * * * * * cursor-enhanced --reach-fire")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.reach_remove:
        try:
            from reach_schedules import remove_schedule
            if remove_schedule(args.reach_remove):
                print(f"Removed schedule {args.reach_remove}")
            else:
                print(f"Schedule not found: {args.reach_remove}", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Scheduled notifications (in-process scheduler when --telegram runs)
    if args.schedule_list:
        try:
            from telegram_integration import schedule_list, SCHEDULE_STORE_PATH
            entries = schedule_list(SCHEDULE_STORE_PATH)
            if not entries:
                print("No scheduled notifications. Add with: cursor-enhanced --schedule-add --schedule-time 09:00 --schedule-message 'Your message'")
                return
            print("Scheduled notifications (fire when Telegram bot is running):")
            for e in entries:
                st = e.get("schedule_type") or "?"
                spec = (e.get("time") or e.get("once_at") or "?")
                en = "enabled" if e.get("enabled", True) else "disabled"
                msg = (e.get("message") or "")[:50]
                print(f"  {e.get('id')}  {st}  {spec}  {en}  {msg}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.schedule_remove:
        try:
            from telegram_integration import schedule_remove, SCHEDULE_STORE_PATH
            if schedule_remove(args.schedule_remove, SCHEDULE_STORE_PATH):
                print(f"Removed scheduled notification {args.schedule_remove}")
            else:
                print(f"Schedule not found: {args.schedule_remove}", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.schedule_add:
        try:
            from telegram_integration import schedule_add, SCHEDULE_STORE_PATH
            time_spec = (args.schedule_time or "").strip()
            message = (args.schedule_message or "").strip()
            if not time_spec or not message:
                print("Error: --schedule-time and --schedule-message are required", file=sys.stderr)
                sys.exit(1)
            chat_id = "all"
            if args.schedule_user is not None:
                try:
                    chat_id = int(args.schedule_user)
                except ValueError:
                    chat_id = args.schedule_user
            if args.schedule_once:
                # ISO datetime (support with or without T/Z)
                schedule_type = "once"
                if "T" not in time_spec and " " in time_spec:
                    time_spec = time_spec.replace(" ", "T", 1)
                if time_spec and time_spec[-1] not in "Zz+-" and len(time_spec) <= 19:
                    time_spec = time_spec + "Z"
            else:
                schedule_type = "daily"
            uid = schedule_add(schedule_type=schedule_type, message=message, time_spec=time_spec, telegram_chat_id=chat_id)
            print(f"Added: {uid}  {schedule_type}  {time_spec}  {message[:40]}...")
            print("Notifications fire when the Telegram bot is running: cursor-enhanced --telegram")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return
    
    # Handle Telegram-specific commands
    if args.telegram_list_pending:
        # List pending pairings
        pairing_file = os.path.expanduser("~/.cursor-enhanced/telegram-pairings.json")
        if os.path.exists(pairing_file):
            try:
                with open(pairing_file, 'r') as f:
                    data = json.load(f)
                    pending = data.get("pending_pairings", {})
                    if pending:
                        print("Pending pairing codes:")
                        for chat_id, code in pending.items():
                            print(f"  Chat {chat_id}: {code}")
                    else:
                        print("No pending pairings found.")
            except Exception as e:
                print(f"Error reading pairing file: {e}")
        else:
            print("No pairing file found. Send /start to your bot to generate a pairing code.")
        return
    
    if args.telegram_list_paired:
        # List paired users
        pairing_file = os.path.expanduser("~/.cursor-enhanced/telegram-pairings.json")
        if os.path.exists(pairing_file):
            try:
                with open(pairing_file, 'r') as f:
                    data = json.load(f)
                    paired = data.get("paired_users", [])
                    if paired:
                        print("Paired users:")
                        for user_id in paired:
                            print(f"  User ID: {user_id} (type: {type(user_id).__name__})")
                    else:
                        print("No paired users found.")
            except Exception as e:
                print(f"Error reading pairing file: {e}")
        else:
            print("No pairing file found.")
        return
    
    if args.telegram_debug:
        # Show full debug info
        pairing_file = os.path.expanduser("~/.cursor-enhanced/telegram-pairings.json")
        if os.path.exists(pairing_file):
            try:
                with open(pairing_file, 'r') as f:
                    data = json.load(f)
                    print("=== Telegram Pairing Debug Info ===")
                    print(f"File path: {pairing_file}")
                    print(f"Raw JSON data:")
                    print(json.dumps(data, indent=2))
                    print(f"\nPaired users: {data.get('paired_users', [])}")
                    print(f"Pending pairings: {data.get('pending_pairings', {})}")
            except Exception as e:
                print(f"Error reading pairing file: {e}")
        else:
            print(f"No pairing file found at: {pairing_file}")
        return
    
    if args.telegram_approve:
        # Approve Telegram pairing
        try:
            from telegram_integration import load_telegram_config, TelegramBot, TelegramConfig
            import json as json_module
            
            config = load_telegram_config()
            if not config:
                print("Error: Telegram not configured. Set TELEGRAM_BOT_TOKEN or configure in ~/.cursor-enhanced-config.json")
                sys.exit(1)
            
            # Show pending codes for debugging
            pairing_file = os.path.expanduser("~/.cursor-enhanced/telegram-pairings.json")
            if os.path.exists(pairing_file):
                try:
                    with open(pairing_file, 'r') as f:
                        pairing_data = json_module.load(f)
                        pending = pairing_data.get("pending_pairings", {})
                        if pending:
                            print(f"Pending pairings found: {list(pending.values())}")
                except:
                    pass
            
            # Create a temporary bot instance to approve pairing
            # We don't need to start it, just use the approve method
            bot = TelegramBot(config, openclaw_integration=None)
            code_upper = args.telegram_approve.upper()
            if bot.approve_pairing(code_upper):
                print(f"✅ Pairing code {args.telegram_approve} approved successfully!")
                print("You can now send messages to the bot.")
                sys.exit(0)
            else:
                print(f"❌ Pairing code {args.telegram_approve} not found or already used.")
                print("Make sure you're using the exact code shown by the bot.")
                if os.path.exists(pairing_file):
                    try:
                        with open(pairing_file, 'r') as f:
                            data = json_module.load(f)
                            pending = data.get("pending_pairings", {})
                            if pending:
                                print(f"\nAvailable pending codes: {', '.join(pending.values())}")
                            else:
                                print("\nNo pending pairings found in file.")
                    except Exception as e:
                        print(f"\nCould not read pairing file: {e}")
                else:
                    print(f"\nPairing file does not exist: {pairing_file}")
                    print("The bot may not have saved the pairing code yet.")
                    print("Make sure the bot is running and you've sent /start to it.")
                sys.exit(1)
        except ImportError as e:
            print(f"Telegram integration not available: {e}")
            print("Install with: pip install python-telegram-bot")
            sys.exit(1)
        except Exception as e:
            print(f"Error approving pairing: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
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
                    app_config = load_config()
                    openclaw = get_openclaw_integration(config=app_config)
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
            openclaw = get_openclaw_integration(config=config)
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
    meta_file = get_history_meta_file(args.chat)

    if args.clear_history:
        if os.path.exists(history_file):
            os.remove(history_file)
        if os.path.exists(meta_file):
            os.remove(meta_file)
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
    
    # Add --force flag if not already present
    # This allows tool execution (like web_fetch) without explicit user approval
    has_force = any(arg in ["--force", "-f"] for arg in cursor_flags)
    if not has_force:
        cursor_flags.append("--force")
        logger.debug("Added --force flag to cursor-agent command for automatic tool execution")
            
    user_prompt = " ".join(user_prompt_parts)
    
    if not user_prompt:
        # Use direct path for compatibility
        cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
        subprocess.run(
            ["bash", cursor_agent_path] + unknown_args,
            env=_env_for_cursor_agent(),
        )
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
    history_meta = load_history_meta(meta_file)
    
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

    # Pre-compaction memory flush (OpenClaw-style)
    memory_flush_settings = resolve_memory_flush_settings(config)
    total_history_text = format_history_for_prompt(history)
    total_estimated_all = estimate_tokens(total_history_text) + system_prompt_tokens + user_prompt_tokens
    if memory_flush_settings and should_run_memory_flush(total_estimated_all, memory_flush_settings, history_meta):
        if run_memory_flush(history, cursor_flags, memory_flush_settings, logger):
            compaction_count = int(history_meta.get("compaction_count", 0) or 0)
            history_meta["memory_flush_compaction_count"] = compaction_count + 1
            history_meta["memory_flush_at"] = int(time.time() * 1000)
            save_history_meta(history_meta, meta_file)
    
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
            logger.info("Token limit exceeded in fixed-limit mode. Triggering summarization.")
            history, summarized = summarize_history(history, cursor_flags)
            if summarized:
                history_meta["compaction_count"] = int(history_meta.get("compaction_count", 0) or 0) + 1
                save_history_meta(history_meta, meta_file)
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
            history, summarized = summarize_history(history, cursor_flags)
            if summarized:
                history_meta["compaction_count"] = int(history_meta.get("compaction_count", 0) or 0) + 1
                save_history_meta(history_meta, meta_file)
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
    if os.environ.get("CURSOR_ENHANCED_CHANNEL") == "telegram":
        full_prompt_parts.append(TELEGRAM_SYSTEM_PROMPT_REACH + "\n")
        full_prompt_parts.append(TELEGRAM_SYSTEM_PROMPT_PROJECT + "\n")
        # Smart delegation is especially important for Telegram: announce model choices to the user
        full_prompt_parts.append(
            "\n=== Telegram: Smart Delegation ===\n"
            "When delegating complex tasks via smart delegate, ALWAYS announce the model choice "
            "and reasoning in the response. The user on Telegram should clearly see:\n"
            "- What task is being delegated\n"
            "- Which model was selected and why\n"
            "- The complexity assessment\n"
            "This transparency is a core cursor-enhanced feature.\n"
        )
    
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
            full_prompt_parts.append("\n**TOOL USAGE FORMAT:**\n")
            full_prompt_parts.append("To use a tool, describe what you want to do in your response. The system will automatically detect and execute tools based on your description.\n")
            full_prompt_parts.append("For example, if you want to fetch a webpage, say: 'I'll fetch the webpage at [URL]' or 'Let me search the web for [query]'.\n")
            full_prompt_parts.append("The system will execute the appropriate tool and provide you with the results.\n\n")
        
        # Add smart delegation instructions if the tool is available
        if "smart_delegate" in (openclaw.tool_registry.tools if hasattr(openclaw.tool_registry, 'tools') else {}):
            full_prompt_parts.append(SMART_DELEGATE_SYSTEM_PROMPT)
    
    full_prompt_parts.append(formatted_history)
    full_prompt_parts.append("User Current Request: " + user_prompt)
    
    full_prompt = "".join(full_prompt_parts)
    
    # Construct command
    # Use direct path to cursor-agent via bash for compatibility
    cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
    cmd = ["bash", cursor_agent_path] + cursor_flags + [full_prompt]
    
    # Run and capture output (env includes MCP config so main agent can use e.g. Home Assistant)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=_env_for_cursor_agent(),
    )
    
    agent_response = ""
    agent_stderr = ""
    
    # Stream stdout
    if process.stdout:
        for line in process.stdout:
            print(line, end="")
            agent_response += line
            
    # Capture stderr
    if process.stderr:
        for line in process.stderr:
            print(line, end="", file=sys.stderr)
            agent_stderr += line
            
    process.wait()
    
    # Execute tools mentioned in agent response
    if process.returncode == 0 and openclaw and args.enable_openclaw:
        try:
            from tool_executor import execute_tool_from_response
            updated_response, tool_results = asyncio.run(
                execute_tool_from_response(agent_response, openclaw, last_user_message=user_prompt)
            )
            if tool_results:
                logger.info(f"Executed {len(tool_results)} tools from agent response")
                # Use updated response with tool results
                agent_response = updated_response
        except Exception as e:
            logger.warning(f"Failed to execute tools from response: {e}")
    
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
        # Print error info to stdout so callers (e.g. Telegram subprocess) can relay it
        stderr_text = agent_stderr.strip()
        if agent_response.strip():
            # Agent produced partial output before failing - already printed via streaming
            error_msg = f"\n\n[Error: agent exited with code {process.returncode}]"
        elif stderr_text:
            error_msg = f"Sorry, I encountered an error processing your request: {stderr_text[:500]}"
        else:
            error_msg = f"Sorry, the agent encountered an error (exit code {process.returncode}). Please try again."
        print(error_msg)
        logger.error(f"Agent stderr: {stderr_text[:500]}")
        # Update presence on error
        if openclaw and session_entry:
            try:
                openclaw.presence_manager.set_typing(session_entry.session_key, False)
            except:
                pass
        sys.exit(process.returncode)

if __name__ == "__main__":
    main()
