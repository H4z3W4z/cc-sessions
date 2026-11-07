# cc-sessions

Two small tools for finding and resuming **Claude Code** *and* **Codex**
sessions across *every* directory you've launched them from — not just the
current one, and not just one agent.

Each agent writes its sessions to disk, and these tools read those logs
(read-only) and give you a single, unified, newest-first view — every session
tagged by agent, with the exact resume command for each:

| Agent       | On-disk logs                               | Resume command         |
| ----------- | ------------------------------------------ | ---------------------- |
| Claude Code | `~/.claude/projects/*/*.jsonl`             | `claude --resume <id>` |
| Codex       | `~/.codex/sessions/*/*/*/rollout-*.jsonl`  | `codex resume <id>`    |

- **`sessions`** — prints a static table of all sessions, newest first, with an
  `AGENT` column.
- **`resume`** — an interactive terminal picker: arrow keys, type-to-filter
  (including by agent — type `codex` or `claude`), each row carrying a `cl`/`cx`
  agent badge; Enter resumes the highlighted session **with the right agent** in
  its own working directory, or **Ctrl+N to launch a brand-new session** in the
  directory you're looking at.

Either store can be absent — if you only run one agent, you just see that one.
No third-party dependencies — just `python3` (which both agents already need).

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
sessions                 # all sessions (both agents), newest first
sessions -n 15           # 15 most recent
sessions myproject       # filter by name / directory / first message / agent
sessions codex           # only Codex sessions (agent is matchable)
sessions --json          # machine-readable (includes the "agent" field)

resume                   # interactive picker
resume myproject         # start pre-filtered
resume --dangerously-skip-permissions   # start with skip-perms toggled ON
resume --new             # skip the picker: new session in the current directory
resume --new ~/code/app  # new session in a specific directory
```

### Multiple agents

Sessions from Claude Code and Codex are merged into one recency-sorted list,
each tagged by agent. Resuming always uses the correct agent and command
(`claude --resume` vs `codex resume`). Note that
`--dangerously-skip-permissions` is a **Claude Code** flag — it is only ever
applied to Claude sessions (and to new sessions, which launch Claude); Codex has
its own sandbox model and is never passed that flag. **Ctrl+N** starts a new
*Claude* session (Codex starts its own).

To point the tools at a store in a non-default location (e.g. for testing, or a
copied store from another host), set `CC_CLAUDE_DIR` and/or `CC_CODEX_DIR`:

```bash
CC_CODEX_DIR=/tmp/codex-copy/sessions sessions   # read Codex logs from elsewhere
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
| type any text  | filter by name / directory / summary / agent      |
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
