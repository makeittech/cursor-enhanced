# Telegram Integration Setup

## Quick Start

1. **Create a Telegram Bot**
   - Open Telegram and chat with [@BotFather](https://t.me/BotFather)
   - Send `/newbot` and follow the prompts
   - Copy the bot token you receive

2. **Configure the Bot Token**
   
   Option A: Environment Variable
   ```bash
   export TELEGRAM_BOT_TOKEN="your_bot_token_here"
   ```
   
   Option B: Config File (`~/.cursor-enhanced-config.json`)
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

3. **Install Dependencies**
   ```bash
   pip install python-telegram-bot
   ```

4. **Start the Telegram Bot**
   ```bash
   cursor-enhanced --telegram
   ```

5. **Pair with the Bot**
   - Send `/start` to the bot in Telegram
   - You'll receive a pairing code
   - Approve it:
     ```bash
     cursor-enhanced --telegram-approve YOUR_CODE
     ```

## Configuration Options

### DM Policy

- `"pairing"` (default): Users must be approved via pairing code
- `"open"`: All users can interact (requires `allowFrom` with `"*"`)

### Allow List

```json
{
  "channels": {
    "telegram": {
      "botToken": "your_token",
      "dmPolicy": "open",
      "allowFrom": ["*"]  // Allow all users
      // Or specific users:
      // "allowFrom": ["123456789", "@username"]
    }
  }
}
```

### Group Support

```json
{
  "channels": {
    "telegram": {
      "botToken": "your_token",
      "groups": {
        "*": {
          "requireMention": true  // Bot only responds when mentioned
        }
      }
    }
  }
}
```

## Commands

- `/start` - Start the bot and get pairing code (if needed)
- `/help` - Show help message
- `/status` - Show bot status and available tools

## Features

- ✅ Direct messages (DMs)
- ✅ Group messages (with mention support)
- ✅ Pairing system for security
- ✅ Integration with OpenClaw tools
- ✅ Session management
- ⚠️ Webhook support (coming soon)

## Security

By default, the bot uses a **pairing** policy:
- Unknown users receive a pairing code
- You must approve them with `--telegram-approve CODE`
- Once approved, they can interact freely

For open access, set `dmPolicy: "open"` and `allowFrom: ["*"]` in config.

## Troubleshooting

**Bot not responding:**
- Check that the bot token is correct
- Verify the bot is running: `cursor-enhanced --telegram`
- Check logs in `~/.cursor-enhanced/logs/cursor-enhanced.log`

**Pairing not working:**
- Make sure you're using the exact code shown
- Check that the bot process is running
- Verify config file permissions

**Import errors:**
- Install: `pip install python-telegram-bot`
- Check Python version (requires 3.9+)
