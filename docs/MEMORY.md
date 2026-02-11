# Memory

Cursor Enhanced uses Runtime-style Markdown memory files stored under:

```
~/.cursor-enhanced/workspace/
  MEMORY.md
  memory/YYYY-MM-DD.md
```

## Memory flush (pre-compaction)

When the session history approaches the token limit, a silent memory flush
turn runs before summarization. The flush prompts the model to store durable
memories to disk while preserving role separation between User and Agent
notes.

Configuration is under:

```
agents.defaults.compaction.memoryFlush
```

See `docs/CONFIGURATION.md` for details.
