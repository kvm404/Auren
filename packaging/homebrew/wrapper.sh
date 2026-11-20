#!/bin/sh
# @COMMAND@, as Homebrew installs it (packaging/homebrew/auren.rb fills in the @...@ values).
# Auren runs from its own environment in ~/.local/share/auren/venv, which `auren setup` builds
# (install-macos.sh --brew, from Homebrew's copy of the source). This only finds it.
version="@VERSION@"
libexec="@LIBEXEC@"

case "${XDG_DATA_HOME:-}" in            # a relative XDG_DATA_HOME is ignored, as Auren ignores it
  /*) share="$XDG_DATA_HOME/auren" ;;
  *) share="$HOME/.local/share/auren" ;;
esac

# Homebrew's tools (uv, and brew itself) first, whatever PATH this was started with
PATH="@BREW_BIN@:$PATH"
export PATH

if [ "@COMMAND@" = auren ] && [ "${1:-}" = setup ]; then
  shift
  exec /bin/bash "$libexec/install-macos.sh" --brew "$@"
fi

venv="$share/venv/bin/@COMMAND@"
if [ ! -x "$venv" ]; then
  echo "Auren isn't set up yet: run auren setup" >&2
  exit 1
fi

# After brew upgrade, the environment is still the version it was built from, and still works.
info="$share/install.json"
if [ -f "$info" ] && grep -q '"mode": "brew"' "$info"; then
  built=$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$info")
  [ "$built" = "$version" ] || echo "Homebrew updated Auren to $version: run auren setup to finish" >&2
fi
exec "$venv" "$@"
