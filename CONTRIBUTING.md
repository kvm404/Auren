# Contributing

Auren should work on any Omarchy machine without code edits. Read [docs/architecture.md](docs/architecture.md)
first.

## Rules

- **No personal values in code.** No browser, folder, app, project, person or agent of your own as a
  default. Detect it, or read it from `~/.config/auren/` (config, routines, `vocab.txt`,
  `instructions.md`). If your setup needs something, make it configurable.
- **Optional things stay optional.** herdr, Open Browser Use, Claude Code, Codex and screen control can
  be missing; Auren must still start, and `auren doctor` must say what's missing and how to fix it.
- **One change per pull request**, with tests for new tools and for bugs you fix.
- Keep [docs/ipc.md](docs/ipc.md) in step with the state file, socket commands and files.

## Working on it

```bash
./install.sh --dev                  # editable install: code changes need only a restart
uv run pytest -q
uv run ruff check src tests
systemctl --user restart auren      # only while Auren is idle
auren doctor                        # what works on this machine; paste it into PRs that touch hands/ or install
```

CI runs ruff and pytest on every pull request.
