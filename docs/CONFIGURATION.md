# Configuration

Cursor Enhanced reads configuration from:

- `~/.cursor-enhanced-config.json`
- Environment variables (where applicable)

## Example

```json
{
  "history_limit": 10,
  "system_prompts": {
    "default": "You are a helpful AI assistant.",
    "coder": "You are an expert software engineer."
  },
  "agents": {
    "defaults": {
      "compaction": {
        "reserveTokensFloor": 20000,
        "memoryFlush": {
          "enabled": true,
          "softThresholdTokens": 4000,
          "systemPrompt": "Session nearing compaction. Store durable memories now.",
          "prompt": "Write any lasting notes to memory/YYYY-MM-DD.md; reply with NO_REPLY if nothing to store."
        }
      }
    }
  }
}
```

## Options

### History

- `history_limit` (int): Fixed number of messages to include in context.
- `CURSOR_ENHANCED_HISTORY_LIMIT` (env): Override for `history_limit`.

### System prompts

- `system_prompts` (dict): Named system prompts, referenced via `--system-prompt`.

### Memory flush

Runtime-style pre-compaction memory flush:

- `agents.defaults.compaction.reserveTokensFloor` (int): Reserved tokens at end of context.
- `agents.defaults.compaction.memoryFlush.enabled` (bool)
- `agents.defaults.compaction.memoryFlush.softThresholdTokens` (int)
- `agents.defaults.compaction.memoryFlush.systemPrompt` (str)
- `agents.defaults.compaction.memoryFlush.prompt` (str)
