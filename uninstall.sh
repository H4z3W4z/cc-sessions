#!/usr/bin/env bash
#
# Uninstaller for cc-sessions. Removes the installed library and commands.
# Does NOT touch your Claude Code sessions in ~/.claude/projects.
#
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
LIBDIR="${CC_SESSIONS_LIB:-$PREFIX/share/cc-sessions}"
BINDIR="${CC_SESSIONS_BIN:-$PREFIX/bin}"

rm -f "$BINDIR/sessions" "$BINDIR/resume"
rm -rf "$LIBDIR"

echo "Removed cc-sessions commands and library."
echo "(Your Claude Code sessions in ~/.claude/projects were left untouched.)"
