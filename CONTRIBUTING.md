# Contributing

Auren is an early Omarchy project. Bug reports, documentation fixes, tests, and focused improvements
are welcome. Read the [architecture](docs/architecture.md) and [roadmap](docs/roadmap.md) first.

## Development setup

```bash
git clone https://github.com/kvm404/Auren.git auren
cd auren
uv sync --frozen
./install.sh --dev
```

The development install links Python and UI files to this checkout. Python changes need a daemon
restart while idle; Quickshell can reload QML on save. Structural mascot changes can require a
mascot service restart. Follow the README to configure your own credentials and bindings.

Release updates only fast-forward a clean `main` to a published release. Manage feature branches
with Git and reinstall manually. Work in a branch and keep each PR focused.

## Checks

```bash
uv run ruff check src tests
uv run pytest -q
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software /usr/lib/qt6/bin/qmltestrunner -input tests/mascot
```

The Qt executable's path can vary by distribution. CI runs Python lint/tests and the Qt interaction
tests. Python tests replace configuration, notices, and the send judge so they do not use your API
keys or start the real judge. Live desktop, browser, microphone, and provider checks are separate.

For changes affecting tools or installation, run `auren doctor` on an Omarchy machine and include
relevant results in the PR. Strip personal details before sharing output. Do not restart services
while Auren is working.

## Implementation rules

- Detect machine-specific apps, browsers, folders, projects, and agents, or read them from the user's
  configuration. Do not hard-code your own setup.
- Keep optional integrations optional. Missing herdr, browser tooling, mouse access, or one brain
  must not prevent the daemon from starting. Diagnostics should explain what is missing.
- Keep tool state and confirmations in the daemon's runtime. Model adapters forward through MCP.
- Add regression tests for bugs and meaningful tests for new tools. Never run a live action against
  somebody's desktop as part of an automated test.
- Keep [docs/ipc.md](docs/ipc.md) synchronized with state fields, socket commands, and stored files.
- Update user documentation when behavior changes. Keep historical measurements clearly dated.
- Never commit credentials, recordings, personal routines, or private logs.

## Pull requests and releases

Describe the user-visible problem, resulting behavior, and validation. State any live checks you did
not run. Include screenshots when changing the mascot or panel.

Release preparation happens in a PR. Version tags and GitHub releases are published after merge and
passing CI; see [the release procedure](docs/releases.md#publishing-a-release).

Auren is [MIT licensed](LICENSE). Contributions are made under the same license.
