# SharedBridge Protocol — Empyrion Fixer Group Only

**Scope:** Empyrion Scenario Fixer + Scenario Fixer's Assistant only. Stellar bots use a separate bridge.

## How we exchange
1. Pull before reading/writing when possible.
2. Append dated entries.
3. Push only bridge-related changes (status, thoughts, handoffs, data summaries).
4. Files: STATUS.md, THOUGHTS.md, HANDOFF.md, data/

## Entry format
```
### YYYY-MM-DD HH:MM — [Grok | BotName]
What changed / thought / data summary.
Local paths or key results if relevant.
```

## Rules
- Concise entries.
- Respect existing rules (backups to F:\AR_Backups, no mass deletes, etc.).
- Bridge files are the only GitHub exception for the Assistant.
