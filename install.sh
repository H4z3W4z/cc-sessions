#!/usr/bin/env bash
#
# Installer for cc-sessions: the `sessions` lister and `resume` TUI picker
# for Claude Code sessions. Self-contained, no third-party dependencies
# (just python3, which Claude Code already needs).
#
# Usage:
#   ./install.sh                  # install to ~/.local
#   PREFIX=/usr/local sudo ./install.sh   # system-wide
#
set -euo pipefail

# --- resolve paths ----------------------------------------------------------
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
LIBDIR="${CC_SESSIONS_LIB:-$PREFIX/share/cc-sessions}"
BINDIR="${CC_SESSIONS_BIN:-$PREFIX/bin}"

# --- preflight --------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found on PATH. Install Python 3 first." >&2
  exit 1
fi

echo "Installing cc-sessions"
echo "  library : $LIBDIR"
echo "  commands: $BINDIR/sessions, $BINDIR/resume"
echo

# --- copy the tools ---------------------------------------------------------
mkdir -p "$LIBDIR" "$BINDIR"
cp "$SRC_DIR/sessions.py" "$SRC_DIR/sessions_tui.py" "$LIBDIR/"

# --- generate wrapper commands ----------------------------------------------
# Wrappers (rather than shell aliases) work across bash/zsh/fish and from
# scripts, and keep all logic in one installed copy.
cat > "$BINDIR/sessions" <<EOF
#!/usr/bin/env bash
exec python3 "$LIBDIR/sessions.py" "\$@"
EOF

cat > "$BINDIR/resume" <<EOF
#!/usr/bin/env bash
exec python3 "$LIBDIR/sessions_tui.py" "\$@"
EOF

chmod +x "$BINDIR/sessions" "$BINDIR/resume"

echo "Installed."
echo

# --- post-install checks ----------------------------------------------------
case ":$PATH:" in
  *":$BINDIR:"*) ;;  # already on PATH
  *)
    echo "NOTE: $BINDIR is not on your PATH. Add this to your shell rc:"
    echo "      export PATH=\"$BINDIR:\$PATH\""
    echo
    ;;
esac

# Warn about older alias-based installs that would shadow the new commands.
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if [ -f "$rc" ] && grep -qE "alias (sessions|resume)=" "$rc"; then
    echo "NOTE: $rc defines an alias for 'sessions' or 'resume' that will"
    echo "      override these commands. Remove the old alias line(s):"
    grep -nE "alias (sessions|resume)=" "$rc" | sed 's/^/        /'
    echo
  fi
done

echo "Try it:  sessions        (static list)"
echo "         resume          (interactive picker)"
