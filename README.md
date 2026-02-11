# Cursor Enhanced

A production-ready wrapper for `cursor-agent` with persistent history, context
management, Runtime-style tooling, and configurable system prompts.

## Features

- **Persistent History**: Remembers your conversation across sessions, stored in `~/.cursor-enhanced-history.json` (or per-chat files).
- **Smart Context Management**: Automatically selects conversation history that fits within token limits (~100k tokens default) using intelligent token-based selection.
- **Auto-Summarization**: Automatically compresses old conversation history when it exceeds token limits, preserving recent messages and creating a summary of older ones.
- **Memory Flush**: Runtime-style pre-compaction memory flush to store durable notes in `MEMORY.md` and daily logs.
- **Tooling**: Runtime tool registry with memory search, web fetch, and session tools.
- **MCP Discovery**: Discovers MCP tools from Cursor's MCP configuration.
- **Telegram Bot**: Optional Telegram integration with pairing-based access control.
- **Multiple Chats**: Support for separate chat sessions using `--chat <name>`, each with its own history file.
- **System Prompts**: Configurable system prompts via `~/.cursor-enhanced-config.json` or `--system-prompt` flag.
- **History Management**: View past conversations with `--view-history` and clear history with `--clear-history`.
- **Logging**: Comprehensive logging to `~/.cursor-enhanced/logs/cursor-enhanced.log` with automatic rotation (5MB max, 5 backups).

## Installation

Requirements:

- Python 3.9+

### From source

```bash
git clone git@github.com:makeittech/cursor-enhanced.git
cd cursor-enhanced
pip install -r requirements.txt
pip install -e .
```

### CLI entrypoint

```bash
./bin/cursor-enhanced -p "Hello world"
```

### Telegram bot (optional)

Install dependencies and export your bot token:

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
```

Run the bot:

```bash
cursor-enhanced --telegram
```

Pair a user after receiving a code from `/start`:

```bash
cursor-enhanced --telegram-approve YOUR_CODE
```

Alternatively, configure the token in `~/.cursor-enhanced-config.json`:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "your_bot_token_here",
      "dmPolicy": "pairing"
    }
  }
}
```

## Usage

Use `cursor-enhanced` exactly like you would use `cursor-agent`, but with extra powers.

```bash
# Basic usage
cursor-enhanced -p "Hello world"

# With specific model
cursor-enhanced --model gpt-4 -p "Complex question"

# Using a specific chat session
cursor-enhanced --chat project-alpha -p "What is the status?"

# View history
cursor-enhanced --chat project-alpha --view-history

# Clear history
cursor-enhanced --chat project-alpha --clear-history

# Use a specific system prompt
cursor-enhanced --system-prompt coder -p "Review this code"

# Start Telegram bot
cursor-enhanced --telegram
```

## Configuration

Create `~/.cursor-enhanced-config.json`:

```json
{
  "history_limit": 10,
  "system_prompts": {
    "default": "You are a helpful AI assistant.",
    "coder": "You are an expert software engineer. Focus on clean code.",
    "pirate": "You are a pirate. Arrr!"
  }
}
```

### Configuration Options

- `history_limit` (integer, optional): Number of previous messages to send as context when using fixed-count mode. If not set, the tool uses smart token-based selection (default behavior). Can also be set via `CURSOR_ENHANCED_HISTORY_LIMIT` environment variable or `--history-limit` flag.
- `system_prompts` (dict): Named system prompts that can be referenced with `--system-prompt <name>`.

### History Management

By default, `cursor-enhanced` uses **smart token-based history selection**, automatically including as much conversation history as possible while staying within the ~100k token limit. This ensures optimal context without manual tuning.

If you prefer a fixed number of messages, set `history_limit` in the config file, use the `CURSOR_ENHANCED_HISTORY_LIMIT` environment variable, or pass `--history-limit <number>`.

When history exceeds the token limit, the tool automatically summarizes older messages while preserving recent context, ensuring you always have relevant conversation history available.

### Logging

All interactions are logged to `~/.cursor-enhanced/logs/cursor-enhanced.log` with automatic log rotation:
- Maximum log file size: 5MB
- Number of backup files: 5
- Logs include timestamps, user requests, agent responses, and summarization events

## Documentation

- `docs/ARCHITECTURE.md` — module layout and flow
- `docs/CONFIGURATION.md` — config options
- `docs/MEMORY.md` — memory workflow
- `docs/DEVELOPMENT.md` — local dev and testing
- `docs/INTEGRATIONS.md` — MCP + Telegram
