# Architecture

Cursor Enhanced is a lightweight wrapper around `cursor-agent` with OpenClaw-style
tooling, session management, and memory handling.

## High-level flow

1. The CLI parses arguments and loads configuration.
2. History is loaded from disk and summarized if needed.
3. A prompt is composed with system prompt, history, and tooling context.
4. `cursor-agent` is invoked with the composed prompt.
5. Tool mentions in the response are parsed and executed.
6. The response and updated history are written to disk.

## Key modules

- `main.py` — primary CLI flow and prompt orchestration.
- `openclaw_core.py` — session store, tool registry, memory tools.
- `openclaw_integration.py` — integration layer for OpenClaw-style features.
- `tool_executor.py` — tool detection and execution.
- `mcp_tools.py` — MCP tools discovery client.
- `telegram_integration.py` — optional Telegram bot integration.

## Data layout

Default paths under `~/.cursor-enhanced/`:

- `history` files: conversation logs (per chat).
- `history-meta` files: compaction + memory flush metadata.
- `logs/`: rotating log files.
- `workspace/`: memory + skills files (OpenClaw-style).

## Memory

Memory is stored as Markdown in the workspace:

- `MEMORY.md` — durable, long-term notes.
- `memory/YYYY-MM-DD.md` — daily log entries.

See `docs/MEMORY.md` for details.
