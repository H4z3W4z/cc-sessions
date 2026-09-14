# cc-sessions

Two small tools for finding and resuming **Claude Code** sessions across *every*
directory you've launched it from — not just the current one.

Claude Code writes each session as a JSONL log under `~/.claude/projects/`.
These tools read those logs (read-only) and give you a single cross-directory
view, with the exact `claude --resume` command for each session.

- **`sessions`** — prints a static table of all sessions, newest first.
- **`resume`** — an interactive terminal picker: arrow keys, type-to-filter,
  Enter to resume the highlighted session in its own working directory, or
  **Ctrl+N to launch a brand-new session** in the directory you're looking at.

No third-party dependencies — just `python3` (which Claude Code already needs).

## Install

```bash
./install.sh                          # installs to ~/.local
PREFIX=/usr/local sudo ./install.sh   # system-wide
```

This copies the tools to `~/.local/share/cc-sessions/` and creates `sessions`
and `resume` commands in `~/.local/bin/`. If that bin directory isn't on your
`PATH`, the installer tells you the line to add.

To move it to another machine, copy this whole folder (or `git clone` /
`scp -r` it) and run `./install.sh` there.

## Usage

```bash
sessions                 # all sessions, newest first
sessions -n 15           # 15 most recent
sessions myproject       # filter by name / directory / first message
sessions --json          # machine-readable

resume                   # interactive picker
resume myproject         # start pre-filtered
resume --dangerously-skip-permissions   # start with skip-perms toggled ON
resume --new             # skip the picker: new session in the current directory
resume --new ~/code/app  # new session in a specific directory
```

### Starting a new session

From the picker, **Ctrl+N** (or the `＋new` chip in the header) launches a fresh
`claude` session — no `--resume`. The directory is chosen as: an existing path
you've typed into the filter, otherwise the highlighted row's directory,
otherwise the directory you ran `resume` from. The skip-perms toggle (Ctrl+X)
applies to new sessions too. With no sessions on disk yet, the picker still
opens so you can start your first one with Ctrl+N.

### Picker keys (`resume`)

| Key            | Action                                            |
| -------------- | ------------------------------------------------- |
| Up/Down, k/j   | move selection                                    |
| PgUp/PgDn      | jump a page                                        |
| type any text  | filter by name / directory / summary              |
| Backspace      | delete a filter character                         |
| Tab            | cycle sort: recent ⇄ directory                    |
| Ctrl+X         | toggle `--dangerously-skip-permissions`           |
| Ctrl+N         | launch a new session (filter-path / selected dir / cwd) |
| Ctrl+D         | delete the selected session (asks to confirm)     |
| Enter          | resume the selected session                       |
| Esc / Ctrl+C   | quit                                              |

**Note:** `Ctrl+D` permanently deletes the session log and its subagent
transcripts — there is no undo. Resuming with `--dangerously-skip-permissions`
disables Claude Code's permission prompts for that session; it is off by default
and must be toggled deliberately.

## Uninstall

```bash
./uninstall.sh
```

Removes the commands and library. Your sessions in `~/.claude/projects` are
never touched.
