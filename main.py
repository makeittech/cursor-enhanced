import sys
import os
import json
import subprocess
import argparse
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

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

def format_history_for_prompt(history, limit):
    relevant_history = history[-limit:] if limit > 0 else []
    
    if not relevant_history:
        return ""

    formatted_context = "=== START OF CONVERSATION HISTORY ===\n"
    for item in relevant_history:
        role = "User" if item['role'] == 'user' else "Agent"
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
    
    if env_limit and env_limit.isdigit():
        default_limit = int(env_limit)
    elif "history_limit" in config and isinstance(config["history_limit"], int):
        default_limit = config["history_limit"]
    else:
        default_limit = DEFAULT_HISTORY_LIMIT

    parser = argparse.ArgumentParser(description="Wrapper for cursor-agent with history context")
    parser.add_argument("--history-limit", type=int, default=default_limit, help=f"Number of previous messages to include (default: {default_limit})")
    parser.add_argument("--clear-history", action="store_true", help="Clear conversation history")
    parser.add_argument("--view-history", action="store_true", help="View conversation history")
    parser.add_argument("--system-prompt", type=str, default="default", help="Name of the system prompt configuration to use")
    parser.add_argument("--chat", type=str, default=None, help="Name of the chat session to use")
    parser.add_argument("--model", type=str, default=None, help="Model to use (e.g., gpt-5, sonnet-4)")
    
    # We use parse_known_args to separate wrapper args from cursor-agent args/prompt
    args, unknown_args = parser.parse_known_args()

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

    # Load config and history
    # config is already loaded at start of main
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
    
    # Check Token Limit
    formatted_history = format_history_for_prompt(history, len(history)) # Get all history
    total_text = system_prompt_content + formatted_history
    
    if estimate_tokens(total_text) > TOKEN_LIMIT:
        logger.info(f"Token limit exceeded ({estimate_tokens(total_text)} > {TOKEN_LIMIT}). Triggering summarization.")
        history = summarize_history(history, cursor_flags)
        save_history(history, history_file)
        # Re-format
        formatted_history = format_history_for_prompt(history, len(history))
    
    # Prepare final context for this request
    context = format_history_for_prompt(history, args.history_limit)
    
    full_prompt_parts = []
    if system_prompt_content:
        full_prompt_parts.append(f"System: {system_prompt_content}\n")
    
    full_prompt_parts.append(context)
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
        
        # Log final result
        logger.info(f"Agent Response: {agent_response.strip()}")
    else:
        logger.error(f"Agent execution failed with return code {process.returncode}")

if __name__ == "__main__":
    main()
