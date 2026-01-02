# Cursor Enhanced

A wrapper for `cursor-agent` that provides enhanced functionality such as persistent conversation history, context management, auto-summarization, and configurable system prompts.

## Features

- **Persistent History**: Remembers your conversation across sessions.
- **Context Injection**: Automatically feeds recent conversation history to the agent.
- **Auto-Summarization**: Compresses history when it exceeds token limits (default ~100k tokens).
- **Multiple Chats**: Support for separate chat sessions using `--chat <name>`.
- **System Prompts**: Configurable system prompts via `~/.cursor-enhanced-config.json` or `--system-prompt`.
- **History Viewer**: View past conversations with `--view-history`.

## Installation

1. Clone this repository:
   ```bash
   git clone git@github.com:makeittech/cursor-enhanced.git
   ```

2. Add the `bin` directory to your PATH or create a symlink:
   ```bash
   ln -s /path/to/cursor-enhanced/bin/cursor-enhanced ~/.local/bin/cursor-enhanced
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
```

## Configuration

Create `~/.cursor-enhanced-config.json`:

```json
{
  "history_limit": 5,
  "system_prompts": {
    "default": "You are a helpful AI assistant.",
    "coder": "You are an expert software engineer. Focus on clean code.",
    "pirate": "You are a pirate. Arrr!"
  }
}
```

Then use:
```bash
cursor-enhanced --system-prompt pirate -p "Hello"
```

### Configuration Options

- `history_limit` (integer): Number of previous messages to send as context (default: 10). Can also be set via `CURSOR_ENHANCED_HISTORY_LIMIT` env var or `--history-limit` flag.
- `system_prompts` (dict): Named system prompts.
