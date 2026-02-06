# Integrations

## MCP tools

Cursor Enhanced can discover MCP tools from Cursor's MCP configuration
(`~/.cursor/mcp.json`). MCP tool execution is currently a placeholder; extend
`mcp_tools.py` to connect to MCP servers directly.

## Telegram

Telegram integration allows you to run Cursor Enhanced as a Telegram bot.

Required:

- `python-telegram-bot`
- `TELEGRAM_BOT_TOKEN` environment variable or config entry.

See `TELEGRAM_SETUP.md` for setup steps.
