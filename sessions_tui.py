#!/usr/bin/env python3
"""Interactive TUI to browse and resume Claude Code sessions.

Reuses the scanning logic in sessions.py, then puts an arrow-key picker on top:
navigate, type to filter, Enter to resume the highlighted session in its own
working directory.

Keys:
    Up/Down or k/j   move selection
    PgUp/PgDn        jump a page
    type any text    filter by name / directory / summary
    Backspace        delete a filter character
    Tab              cycle sort: recent <-> directory
    Ctrl+X           toggle --dangerously-skip-permissions for the resume
    Ctrl+D           delete the selected session (asks to confirm)
    Enter            resume the selected session
    Esc / Ctrl+C     quit

Usage:
    python3 sessions_tui.py
    python3 sessions_tui.py myproject                    # start with a filter
    python3 sessions_tui.py --dangerously-skip-permissions   # start with skip-perms ON
"""
from __future__ import annotations

import curses
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Reuse the single source of truth for parsing/scanning.
from sessions import collect, human_size

SKIP_FLAG = "--dangerously-skip-permissions"


def label_for(s: dict) -> str:
    if s["name"]:
        return s["name"]
    if s["summary"]:
        return f"({s['summary']})"
    return "—"


def matches(s: dict, query: str) -> bool:
    q = query.lower()
    return (
        q in s["name"].lower()
        or q in s["cwd"].lower()
        or q in s["summary"].lower()
    )


SORT_MODES = ("recent", "directory")


def sort_key(mode: str):
    if mode == "directory":
        # group by directory, newest first within each
        return lambda s: (s["cwd"].lower(), -s["mtime"])
    return lambda s: -s["mtime"]


def delete_session(s: dict) -> None:
    """Remove the session log and its subagent transcripts, if any."""
    p = Path(s["path"])
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    sub = p.parent / s["id"]  # <project>/<sessionId>/subagents/...
    if sub.is_dir():
        shutil.rmtree(sub, ignore_errors=True)


def safe_addnstr(stdscr, y, x, text, n, attr=0):
    """addnstr that won't crash on the bottom-right cell or a tiny terminal."""
    height, width = stdscr.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    # Writing into the last column of the last row moves the cursor off-screen
    # and raises ERR, so cap the run length to keep one cell free there.
    limit = width - x - (1 if y == height - 1 else 0)
    n = min(n, max(0, limit))
    if n <= 0:
        return
    try:
        stdscr.addnstr(y, x, text, n, attr)
    except curses.error:
        pass


def draw(stdscr, sessions, selected, top, query, skip_perms, sort_mode):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    home = str(Path.home())

    # --- header ---
    title = f" Claude Code Sessions   (sort: {sort_mode}) "
    safe_addnstr(stdscr, 0, 0, title.ljust(width), width, curses.A_REVERSE)

    perms_txt = f" SKIP-PERMS: {'ON' if skip_perms else 'off'} (Ctrl+X) "
    perms_attr = curses.color_pair(1) | curses.A_BOLD if skip_perms else curses.A_DIM
    safe_addnstr(stdscr, 1, 0, f"Filter: {query}_", max(0, width - len(perms_txt) - 1))
    if width > len(perms_txt):
        safe_addnstr(stdscr, 1, width - len(perms_txt), perms_txt, len(perms_txt), perms_attr)

    # --- column header ---
    col = f"  {'LAST USED':16}  {'NAME / SUMMARY':32}  {'DIRECTORY':30}  {'ID':8}"
    safe_addnstr(stdscr, 2, 0, col.ljust(width), width, curses.A_UNDERLINE)

    # --- list ---
    list_top = 3
    list_h = height - list_top - 1  # leave a footer line
    visible = sessions[top : top + list_h]
    for i, s in enumerate(visible):
        idx = top + i
        when = datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
        name = label_for(s)[:32]
        cwd = s["cwd"].replace(home, "~", 1)[:30]
        row = f"  {when:16}  {name:32}  {cwd:30}  {s['id'][:8]}"
        attr = curses.A_REVERSE if idx == selected else curses.A_NORMAL
        safe_addnstr(stdscr, list_top + i, 0, row.ljust(width), width, attr)

    # --- footer ---
    footer = (
        f" {len(sessions)} match  |  Enter resume  |  Tab sort  |  "
        f"Ctrl+X skip-perms  |  Ctrl+D delete  |  Esc quit "
    )
    safe_addnstr(stdscr, height - 1, 0, footer.ljust(width), width, curses.A_REVERSE)
    stdscr.refresh()


def confirm(stdscr, message) -> bool:
    """Draw a prompt on the footer line; return True only on 'y'."""
    height, width = stdscr.getmaxyx()
    safe_addnstr(stdscr, height - 1, 0, message.ljust(width), width,
                 curses.color_pair(1) | curses.A_BOLD)
    stdscr.refresh()
    try:
        ch = stdscr.get_wch()
    except KeyboardInterrupt:
        return False
    return isinstance(ch, str) and ch.lower() == "y"


def run(stdscr, all_sessions, start_query, start_skip):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    stdscr.keypad(True)

    query = start_query
    skip_perms = start_skip
    sort_mode = "recent"
    selected = 0
    top = 0

    while True:
        pool = [s for s in all_sessions if matches(s, query)] if query else list(all_sessions)
        filtered = sorted(pool, key=sort_key(sort_mode))
        if not filtered:
            selected = top = 0
        else:
            selected = max(0, min(selected, len(filtered) - 1))

        height, _ = stdscr.getmaxyx()
        list_h = max(1, height - 4)
        if selected < top:
            top = selected
        elif selected >= top + list_h:
            top = selected - list_h + 1

        draw(stdscr, filtered, selected, top, query, skip_perms, sort_mode)

        try:
            ch = stdscr.get_wch()
        except KeyboardInterrupt:
            return None

        if ch in (curses.KEY_UP,) or ch == "k":
            selected -= 1
        elif ch in (curses.KEY_DOWN,) or ch == "j":
            selected += 1
        elif ch == curses.KEY_PPAGE:
            selected -= list_h
        elif ch == curses.KEY_NPAGE:
            selected += list_h
        elif ch == "\t":  # Tab: cycle sort mode
            sort_mode = SORT_MODES[(SORT_MODES.index(sort_mode) + 1) % len(SORT_MODES)]
        elif ch == "\x18":  # Ctrl+X
            skip_perms = not skip_perms
        elif ch == "\x04":  # Ctrl+D: delete selected
            if filtered:
                s = filtered[selected]
                name = label_for(s)[:40]
                if confirm(stdscr, f" Delete session {s['id'][:8]} \"{name}\"? (y/N) "):
                    delete_session(s)
                    all_sessions.remove(s)
                    selected = max(0, selected - 1)
        elif ch in ("\n", "\r", curses.KEY_ENTER):
            if filtered:
                return (filtered[selected], skip_perms)
        elif ch in ("\x1b",):  # Esc
            return None
        elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            query = query[:-1]
            selected = 0
        elif isinstance(ch, str) and ch.isprintable():
            query += ch
            selected = 0

        if filtered:
            selected = max(0, min(selected, len(filtered) - 1))


def main() -> None:
    args = sys.argv[1:]
    skip = False
    if SKIP_FLAG in args:
        skip = True
        args.remove(SKIP_FLAG)
    query = args[0] if args else ""

    sessions = collect()
    if not sessions:
        sys.exit("No sessions found.")

    choice = curses.wrapper(run, sessions, query, skip)
    if not choice:
        print("No session selected.")
        return

    session, skip_perms = choice
    cwd = session["cwd"]
    cmd = ["claude", "--resume", session["id"]]
    if skip_perms:
        cmd.append(SKIP_FLAG)

    if not os.path.isdir(cwd):
        sys.exit(f"Session directory no longer exists: {cwd}")

    flag_note = f"  {SKIP_FLAG}" if skip_perms else ""
    print(f"Resuming {session['id'][:8]} in {cwd}{flag_note}\n")
    os.chdir(cwd)
    try:
        os.execvp("claude", cmd)  # replaces this process with claude
    except FileNotFoundError:
        sys.exit("`claude` not found on PATH.")


if __name__ == "__main__":
    main()
