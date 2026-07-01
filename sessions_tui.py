#!/usr/bin/env python3
"""Interactive TUI to browse and resume Claude Code sessions.

A keyboard-first (and mouse-aware) curses picker over every Claude Code session
on disk. Reuses the scanning logic in sessions.py. Implements the cc-sessions
TUI design: header with sort tabs + skip-perms chip, live filter, dense session
list with recency-coloured times, an inline expandable preview per session,
directory grouping, toasts and inline confirms.

Keys:
    Up/Down          move selection        PgUp/PgDn   jump 10
    type any text    filter (name/dir/summary/first message)
    Backspace        delete a filter char
    Right / Left     open / close the preview for the selected row
    Tab              cycle sort: recent <-> directory
    Ctrl+X           toggle --dangerously-skip-permissions
    Ctrl+D           delete the selected session (confirm y/N)
    Enter            resume the selected session
    Esc              clear filter / close preview; again to quit
    mouse            click a row to select, click again (or the caret) to expand;
                     click the sort tabs, skip-perms chip, or preview buttons

Usage:
    python3 sessions_tui.py
    python3 sessions_tui.py myproject                    # start with a filter
    python3 sessions_tui.py --dangerously-skip-permissions   # start with skip-perms ON
"""
from __future__ import annotations

import curses
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sessions import collect, human_size

SKIP_FLAG = "--dangerously-skip-permissions"
SORT_MODES = ("recent", "directory")
HOME = str(Path.home())

# glyphs
G_BAR = "▌"      # ▌ selection marker / header accent
G_PROMPT = "❯"   # ❯ filter prompt
G_OPEN = "▾"     # ▾ expanded caret
G_CLOSED = "▸"   # ▸ collapsed caret / group marker
G_HRULE = "─"    # ─


# ---------------------------------------------------------------------------
# data helpers
# ---------------------------------------------------------------------------
def matches(s: dict, query: str) -> bool:
    q = query.lower()
    return (
        q in s["name"].lower()
        or q in s["cwd"].lower()
        or q in s["summary"].lower()
        or q in s["first"].lower()
    )


def sort_key(mode: str):
    if mode == "directory":
        return lambda s: (s["cwd"].lower(), -s["mtime"])
    return lambda s: -s["mtime"]


def label_for(s: dict) -> str:
    if s["name"]:
        return s["name"]
    if s["summary"]:
        return f"({s['summary']})"
    return "—"  # —


def rel_time(mtime: float, now: float) -> str:
    mins = max(0, int((now - mtime) / 60))
    if mins < 60:
        return f"{mins}m ago"
    if mins < 1440:
        return f"{mins // 60}h ago"
    if mins < 2880:
        return "yesterday"
    if mins < 10080:
        return f"{mins // 1440}d ago"
    return f"{mins // 10080}w ago"


def short_dir(cwd: str) -> tuple[str, str]:
    """Return (parent_with_trailing_slash, leaf)."""
    disp = cwd.replace(HOME, "~", 1)
    parts = disp.rstrip("/").split("/")
    leaf = parts[-1] if parts else disp
    parent = "/".join(parts[:-1])
    if parent:
        parent += "/"
    return parent, leaf


def delete_session(s: dict) -> None:
    p = Path(s["path"])
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    sub = p.parent / s["id"]
    if sub.is_dir():
        shutil.rmtree(sub, ignore_errors=True)


def copy_to_clipboard(text: str) -> bool:
    for tool in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["pbcopy"]):
        if shutil.which(tool[0]):
            try:
                subprocess.run(tool, input=text.encode(), check=True)
                return True
            except Exception:
                pass
    return False


# ---------------------------------------------------------------------------
# colours
# ---------------------------------------------------------------------------
class Theme:
    """Named curses attributes, degrading gracefully to 8-colour terminals."""

    def __init__(self):
        curses.start_color()
        curses.use_default_colors()
        c256 = curses.COLORS >= 256

        def pair(idx, fg):
            curses.init_pair(idx, fg, -1)
            return curses.color_pair(idx)

        if c256:
            self.accent = pair(1, 74)
            self.green = pair(2, 114)
            self.red = pair(3, 167)
            self.bright = pair(4, 255)
            self.text = pair(5, 251)
            self.sec = pair(6, 245)
            self.dim = pair(7, 240)
            self.vdim = pair(8, 238)
            self.leaf = pair(9, 109)
        else:
            self.accent = pair(1, curses.COLOR_CYAN)
            self.green = pair(2, curses.COLOR_GREEN)
            self.red = pair(3, curses.COLOR_RED)
            self.bright = curses.A_BOLD
            self.text = curses.A_NORMAL
            self.sec = pair(6, curses.COLOR_WHITE)
            self.dim = curses.A_DIM
            self.vdim = curses.A_DIM
            self.leaf = pair(9, curses.COLOR_CYAN)


# ---------------------------------------------------------------------------
# low-level drawing
# ---------------------------------------------------------------------------
def put(stdscr, y, x, text, attr, right):
    """Draw text at (y,x), clipped to column `right` (exclusive). Returns next x."""
    height, width = stdscr.getmaxyx()
    right = min(right, width)
    if y < 0 or y >= height or x >= right or x < 0:
        return x
    avail = right - x
    if y == height - 1:  # never touch the very last cell (curses wraps -> ERR)
        avail = min(avail, width - 1 - x)
    if avail <= 0:
        return x
    s = text[:avail]
    try:
        stdscr.addnstr(y, x, s, avail, attr)
    except curses.error:
        pass
    return x + len(s)


def ellipsize(text, n):
    if n <= 0:
        return ""
    if len(text) <= n:
        return text
    if n == 1:
        return "…"
    return text[: n - 1] + "…"


def left_ellipsize(text, n):
    if len(text) <= n:
        return text
    if n <= 1:
        return "…"
    return "…" + text[-(n - 1):]


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------
def layout(width):
    lm = 1
    x_time = lm + 2
    x_name = x_time + 11
    caret_w = 2
    id_w = 9
    size_w = 7
    dir_w = 28
    x_caret_end = width - 1
    x_id_end = x_caret_end - caret_w
    x_size_end = x_id_end - id_w - 1
    x_dir = x_size_end - size_w - 1 - dir_w
    name_right = x_dir - 1
    name_w = name_right - x_name
    if name_w < 12:  # narrow terminal: steal from the directory column
        deficit = 12 - name_w
        dir_w = max(6, dir_w - deficit)
        x_dir = x_size_end - size_w - 1 - dir_w
        name_right = x_dir - 1
        name_w = max(4, name_right - x_name)
    return {
        "lm": lm, "x_time": x_time, "x_name": x_name, "name_w": name_w,
        "name_right": name_right, "x_dir": x_dir, "dir_w": dir_w,
        "x_size_end": x_size_end, "size_w": size_w,
        "x_id_end": x_id_end, "id_w": id_w, "x_caret": x_caret_end,
    }


# ---------------------------------------------------------------------------
# main view
# ---------------------------------------------------------------------------
class View:
    def __init__(self, stdscr, sessions, query, skip):
        self.stdscr = stdscr
        self.all = sessions
        self.query = query
        self.skip = skip
        self.sort = "recent"
        self.sel = 0
        self.expanded_id = None
        self.confirm_id = None
        self.toast = None          # (text, expiry)
        self.top = 0               # first visible display-line index
        # per-render scratch
        self.line_drawers = []     # list of callables f(screen_y)
        self.row_span = {}         # sel index -> (start_line, end_line inclusive)
        self.list_regions = []     # (line_index, x0, x1, kind, index, sess)
        self.click_regions = []    # (screen_y, x0, x1, kind, payload)

    # -- data --
    def filtered(self):
        pool = [s for s in self.all if matches(s, self.query)] if self.query else list(self.all)
        return sorted(pool, key=sort_key(self.sort))

    def n_dirs(self):
        return len({s["cwd"] for s in self.all})

    def set_toast(self, text):
        self.toast = (text, time.time() + 3.2)

    # -- render --
    def render(self, t):
        stdscr = self.stdscr
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        th = self.theme
        self.click_regions = []
        self.list_regions = []

        rows = self.filtered()
        total = len(self.all)
        if rows:
            self.sel = max(0, min(self.sel, len(rows) - 1))
        else:
            self.sel = 0

        self._header(width, th, rows, total)
        self._filter(width, th, rows, total, t)
        self._colheader(width, th)
        self._build_lines(rows, width, th)
        self._draw_list(height, width, th, rows)
        self._footer(height, width, th, rows, total)
        stdscr.refresh()

    def _header(self, width, th, rows, total):
        s = self.stdscr
        x = put(s, 0, 1, G_BAR, th.accent | curses.A_BOLD, width)
        x = put(s, 0, x + 1, "cc-sessions", th.bright | curses.A_BOLD, width)
        count = f"  {total} sessions across {self.n_dirs()} directories"
        put(s, 0, x, count, th.dim, width)

        # right cluster: sort tabs + skip chip
        skip_label = f"⌃X skip-perms {'ON' if self.skip else 'off'}"
        recent_txt, dir_txt = "recent", "directory"
        cluster = 5 + len(recent_txt) + 2 + len(dir_txt) + 3 + len(skip_label) + 2
        cx = max(x + 1, width - cluster - 1)
        cx = put(s, 0, cx, "sort ", th.dim, width)
        r_attr = (th.bright | curses.A_BOLD) if self.sort == "recent" else th.dim
        rx0 = cx
        cx = put(s, 0, cx, recent_txt, r_attr | (curses.A_UNDERLINE if self.sort == "recent" else 0), width)
        self.click_regions.append((0, rx0, cx, "sort", "recent"))
        cx = put(s, 0, cx, "  ", th.dim, width)
        d_attr = (th.bright | curses.A_BOLD) if self.sort == "directory" else th.dim
        dx0 = cx
        cx = put(s, 0, cx, dir_txt, d_attr | (curses.A_UNDERLINE if self.sort == "directory" else 0), width)
        self.click_regions.append((0, dx0, cx, "sort", "directory"))
        cx = put(s, 0, cx, "   ", th.dim, width)
        chip_attr = (th.red | curses.A_BOLD) if self.skip else th.dim
        sx0 = cx
        cx = put(s, 0, cx, skip_label, chip_attr, width)
        self.click_regions.append((0, sx0, cx, "skip", None))

    def _filter(self, width, th, rows, total, t):
        s = self.stdscr
        x = put(s, 1, 1, G_PROMPT, th.accent | curses.A_BOLD, width)
        x = put(s, 1, x + 1, self.query, th.bright, width)
        # blinking block cursor
        if int(t * 2) % 2 == 0:
            put(s, 1, x, " ", th.accent | curses.A_REVERSE, width)
        x += 1
        if not self.query:
            put(s, 1, x + 1, "type to filter by name, directory or message…", th.vdim, width)
        match = f"{len(rows)}/{total}"
        put(s, 1, width - len(match) - 1, match, th.sec, width)

    def _colheader(self, width, th):
        s = self.stdscr
        lo = layout(width)
        a = th.dim | curses.A_UNDERLINE
        # fill the row with underline so it reads as a rule
        put(s, 2, 0, " " * width, a, width)
        rc = " ▾" if self.sort == "recent" else ""
        dc = " ▾" if self.sort == "directory" else ""
        tx0 = lo["x_time"]
        tx1 = put(s, 2, tx0, "LAST USED" + rc, a, lo["x_name"])
        self.click_regions.append((2, tx0, tx1, "sort", "recent"))
        put(s, 2, lo["x_name"], "NAME / SUMMARY", a, lo["name_right"])
        dx0 = lo["x_dir"]
        dx1 = put(s, 2, dx0, "DIRECTORY" + dc, a, lo["x_size_end"] - lo["size_w"])
        self.click_regions.append((2, dx0, dx1, "sort", "directory"))
        put(s, 2, lo["x_size_end"] - len("SIZE"), "SIZE", a, lo["x_size_end"])
        put(s, 2, lo["x_id_end"] - len("ID"), "ID", a, lo["x_id_end"])

    # -- build the flat list of line drawers (rows, group headers, previews) --
    def _build_lines(self, rows, width, th):
        self.line_drawers = []
        self.row_span = {}
        lo = layout(width)
        now = time.time()
        last_dir = None

        for i, s in enumerate(rows):
            if self.sort == "directory" and s["cwd"] != last_dir:
                last_dir = s["cwd"]
                cnt = sum(1 for x in rows if x["cwd"] == s["cwd"])
                self.line_drawers.append(self._group_drawer(s["cwd"], cnt, width, th))

            start = len(self.line_drawers)
            self.line_drawers.append(self._row_drawer(i, s, lo, th, now))
            self.list_regions.append((start, 0, width, "row", i, s))
            self.list_regions.append((start, lo["x_caret"] - 1, width, "toggle", i, s))

            end = start
            if self.expanded_id == s["id"]:
                for drawer in self._preview_drawers(i, s, width, th, now):
                    end = len(self.line_drawers)
                    self.line_drawers.append(drawer)
            self.row_span[i] = (start, end)

    def _group_drawer(self, cwd, count, width, th):
        disp = cwd.replace(HOME, "~", 1)
        label = f"{count} session" + ("s" if count != 1 else "")

        def draw(y):
            s = self.stdscr
            x = put(s, y, 2, G_CLOSED, th.accent, width)
            x = put(s, y, x + 1, disp, th.sec, width)
            put(s, y, width - len(label) - 1, label, th.dim, width)
            rule_end = width - len(label) - 2
            if x + 1 < rule_end:
                put(s, y, x + 1, " " + G_HRULE * (rule_end - x - 2), th.vdim, width)
        return draw

    def _row_drawer(self, i, s, lo, th, now):
        selected = i == self.sel
        expanded = self.expanded_id == s["id"]
        tstr = rel_time(s["mtime"], now)
        age_min = (now - s["mtime"]) / 60
        if age_min < 60:
            tcol = th.green
        elif age_min < 1440:
            tcol = th.text
        else:
            tcol = th.dim
        label = label_for(s)
        if s["name"]:
            lcol, lattr = th.bright, curses.A_BOLD
        elif s["summary"]:
            lcol, lattr = th.sec, curses.A_ITALIC
        else:
            lcol, lattr = th.dim, 0
        if selected:
            lattr |= curses.A_BOLD
        parent, leaf = short_dir(s["cwd"])
        size_str = human_size(s["size"])
        id8 = s["id"][:8]

        def draw(y):
            scr = self.stdscr
            if selected:
                put(scr, y, lo["lm"], G_BAR, th.accent | curses.A_BOLD, lo["x_time"])
            put(scr, y, lo["x_time"], ellipsize(tstr, 10), tcol | (curses.A_BOLD if selected else 0), lo["x_name"])
            put(scr, y, lo["x_name"], ellipsize(label, lo["name_w"]), lcol | lattr, lo["name_right"])
            # directory: parent dim + leaf coloured, left-ellipsized so leaf stays
            dw = lo["dir_w"]
            if len(parent) + len(leaf) > dw:
                keep_parent = max(0, dw - len(leaf))
                p_disp = left_ellipsize(parent, keep_parent) if keep_parent else ""
                l_disp = ellipsize(leaf, dw - len(p_disp))
            else:
                p_disp, l_disp = parent, leaf
            dx = put(scr, y, lo["x_dir"], p_disp, th.vdim, lo["x_dir"] + dw)
            put(scr, y, dx, l_disp, th.leaf, lo["x_dir"] + dw)
            put(scr, y, lo["x_size_end"] - len(size_str), size_str, th.dim, lo["x_size_end"])
            put(scr, y, lo["x_id_end"] - len(id8), id8, th.vdim, lo["x_id_end"])
            car = G_OPEN if expanded else G_CLOSED
            put(scr, y, lo["x_caret"] - 1, car, th.accent if expanded else th.vdim, lo["x_caret"] + 1)
        return draw

    def _preview_drawers(self, i, s, width, th, now):
        left = 4
        right = width - 2
        inner = right - left - 3
        drawers = []

        def box_line(content_fn):
            def draw(y):
                scr = self.stdscr
                put(scr, y, left, "│", th.accent, width)      # accent left edge
                content_fn(y, left + 2, right - 1)
                put(scr, y, right - 1, "│", th.dim, width)     # dim right edge
            return draw

        def top(y):
            scr = self.stdscr
            put(scr, y, left, "╭" + "─" * (right - left - 1) + "╮", th.dim, width)

        def bottom(y):
            scr = self.stdscr
            put(scr, y, left, "╰" + "─" * (right - left - 1) + "╯", th.dim, width)

        drawers.append(top)
        drawers.append(box_line(lambda y, x, r: put(self.stdscr, y, x, "FIRST MESSAGE", th.dim, r)))

        # first message, clamped to 2 wrapped lines
        words = ('“' + (s["first"] or "—") + '”').split()
        wrapped, cur = [], ""
        for w in words:
            if len(cur) + len(w) + (1 if cur else 0) <= inner:
                cur = (cur + " " + w) if cur else w
            else:
                wrapped.append(cur)
                cur = w
            if len(wrapped) == 2:
                break
        if cur and len(wrapped) < 2:
            wrapped.append(cur)
        if not wrapped:
            wrapped = ['“—”']
        if len(wrapped) == 2 and (cur or len(words) > 0):
            wrapped[1] = ellipsize(wrapped[1] + " …", inner) if len(" ".join(words)) > sum(len(w) for w in wrapped) else wrapped[1]
        for wl in wrapped[:2]:
            drawers.append(box_line(lambda y, x, r, wl=wl: put(self.stdscr, y, x, wl, th.text, r)))

        meta = f"{s['msgs']} messages · {human_size(s['size'])} on disk · last active {rel_time(s['mtime'], now)}"
        drawers.append(box_line(lambda y, x, r: put(self.stdscr, y, x, ellipsize(meta, r - x), th.dim, r)))

        logp = s["path"].replace(HOME, "~", 1)
        drawers.append(box_line(lambda y, x, r: put(self.stdscr, y, x, left_ellipsize(logp, r - x), th.vdim, r)))

        def cmd_line(y, x, r):
            scr = self.stdscr
            cx = put(scr, y, x, "$ ", th.dim, r)
            cmd = f"claude --resume {s['id']}"
            cx = put(scr, y, cx, cmd, th.text, r)
            if self.skip:
                put(scr, y, cx + 1, SKIP_FLAG, th.red | curses.A_BOLD, r)
        drawers.append(box_line(cmd_line))

        # action chips (clickable)
        chip_line_index = len(self.line_drawers) + len(drawers)

        def chips(y, x, r):
            scr = self.stdscr
            b1 = "↵ resume"
            b2 = "copy command"
            b3 = "delete…"
            cx = x
            x0 = cx
            cx = put(scr, y, cx, b1, th.accent | curses.A_BOLD, r)
            self.list_regions.append((chip_line_index, x0, cx, "resume", i, s))
            cx = put(scr, y, cx, "   ", th.dim, r)
            x0 = cx
            cx = put(scr, y, cx, b2, th.sec, r)
            self.list_regions.append((chip_line_index, x0, cx, "copy", i, s))
            cx = put(scr, y, cx, "   ", th.dim, r)
            x0 = cx
            cx = put(scr, y, cx, b3, th.red, r)
            self.list_regions.append((chip_line_index, x0, cx, "delete", i, s))
        drawers.append(box_line(chips))
        drawers.append(bottom)
        return drawers

    def _draw_list(self, height, width, th, rows):
        list_top = 3
        list_h = height - list_top - 1
        if list_h < 1:
            return
        if not rows:
            msg = f"no sessions match “{self.query}” — esc to clear" if self.query else "no sessions found"
            put(self.stdscr, list_top + list_h // 2, max(1, (width - len(msg)) // 2), msg, th.dim, width)
            return

        total_lines = len(self.line_drawers)
        start, end = self.row_span.get(self.sel, (0, 0))
        # keep the selected row (and as much of its preview as fits) visible
        if start < self.top:
            self.top = start
        elif end > self.top + list_h - 1:
            self.top = min(end - list_h + 1, start)
        self.top = max(0, min(self.top, max(0, total_lines - list_h)))

        for idx in range(self.top, min(total_lines, self.top + list_h)):
            self.line_drawers[idx](list_top + idx - self.top)

        # map list-region line indices to on-screen y for click handling
        for (li, x0, x1, kind, i, s) in self.list_regions:
            if self.top <= li < self.top + list_h:
                self.click_regions.append((list_top + li - self.top, x0, x1, kind, (i, s)))

    def _footer(self, height, width, th, rows, total):
        y = height - 1
        s = self.stdscr
        if self.confirm_id:
            cs = next((x for x in self.all if x["id"] == self.confirm_id), None)
            if cs:
                name = cs["name"] or cs["summary"] or cs["id"][:8]
                txt = (f"Delete session {cs['id'][:8]} “{name}” and its subagent "
                       f"transcripts? This cannot be undone.  (y/N)")
                put(s, y, 1, ellipsize(txt, width - 2), th.red | curses.A_BOLD, width)
            return
        if self.toast and self.toast[1] > time.time():
            put(s, y, 1, ellipsize(self.toast[0], width - 2), th.accent, width)
            return
        hints = [("↑↓", "navigate"), ("→", "preview"), ("↵", "resume"),
                 ("⇥", "sort"), ("⌃X", "skip-perms"), ("⌃D", "delete"),
                 ("esc", "quit")]
        x = 1
        for key, desc in hints:
            x = put(s, y, x, key, th.sec | curses.A_REVERSE, width)
            x = put(s, y, x, " " + desc + "   ", th.dim, width)
        status = f"{len(rows)}/{total} · sort: {self.sort}"
        put(s, y, width - len(status) - 1, status, th.dim, width)

    # -- input --
    def handle_click(self, my, mx):
        for (y, x0, x1, kind, payload) in self.click_regions:
            if y == my and x0 <= mx < x1:
                if kind == "sort":
                    if self.sort != payload:
                        self.sort = payload
                        self.sel = 0
                        self.top = 0
                elif kind == "skip":
                    self.skip = not self.skip
                elif kind == "row":
                    i, s = payload
                    if i == self.sel:
                        self.expanded_id = None if self.expanded_id == s["id"] else s["id"]
                    else:
                        self.sel = i
                elif kind == "toggle":
                    i, s = payload
                    self.sel = i
                    self.expanded_id = None if self.expanded_id == s["id"] else s["id"]
                elif kind == "resume":
                    return ("resume", payload[1])
                elif kind == "copy":
                    self._do_copy(payload[1])
                elif kind == "delete":
                    self.confirm_id = payload[1]["id"]
                return None
        return None

    def _do_copy(self, s):
        cmd = f"claude --resume {s['id']}" + (f" {SKIP_FLAG}" if self.skip else "")
        if copy_to_clipboard(cmd):
            self.set_toast(f"copied: {cmd}")
        else:
            self.set_toast(f"no clipboard tool — {cmd}")

    def loop(self):
        stdscr = self.stdscr
        curses.curs_set(0)
        self.theme = Theme()
        stdscr.keypad(True)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
        except curses.error:
            pass
        stdscr.timeout(500)

        while True:
            rows = self.filtered()
            self.render(time.time())

            try:
                ch = stdscr.get_wch()
            except curses.error:
                continue  # idle tick: redraw for blink / toast expiry
            except KeyboardInterrupt:
                return None

            # confirm mode swallows all keys
            if self.confirm_id:
                if isinstance(ch, str) and ch.lower() == "y":
                    victim = next((x for x in self.all if x["id"] == self.confirm_id), None)
                    if victim:
                        delete_session(victim)
                        self.all.remove(victim)
                        self.sel = max(0, self.sel - 1)
                        self.set_toast(f"deleted session {victim['id'][:8]}")
                self.confirm_id = None
                continue

            rows = self.filtered()
            cur = rows[self.sel] if rows and self.sel < len(rows) else None

            if ch == curses.KEY_UP:
                self.sel -= 1
            elif ch == curses.KEY_DOWN:
                self.sel += 1
            elif ch == curses.KEY_PPAGE:
                self.sel -= 10
            elif ch == curses.KEY_NPAGE:
                self.sel += 10
            elif ch == curses.KEY_RIGHT:
                if cur:
                    self.expanded_id = cur["id"]
            elif ch == curses.KEY_LEFT:
                self.expanded_id = None
            elif ch == "\t":
                self.sort = SORT_MODES[(SORT_MODES.index(self.sort) + 1) % len(SORT_MODES)]
                self.sel = 0
                self.top = 0
            elif ch == "\x18":  # Ctrl+X
                self.skip = not self.skip
            elif ch == "\x04":  # Ctrl+D
                if cur:
                    self.confirm_id = cur["id"]
            elif ch in ("\n", "\r", curses.KEY_ENTER):
                if cur:
                    return (cur, self.skip)
            elif ch == "\x1b":  # Esc: clear filter / close preview, else quit
                if self.query or self.expanded_id:
                    self.query = ""
                    self.expanded_id = None
                    self.sel = 0
                else:
                    return None
            elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                self.query = self.query[:-1]
                self.sel = 0
            elif ch == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                except curses.error:
                    continue
                if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED):
                    result = self.handle_click(my, mx)
                    if result and result[0] == "resume":
                        return (result[1], self.skip)
            elif isinstance(ch, str) and ch.isprintable():
                self.query += ch
                self.sel = 0

            if rows:
                self.sel = max(0, min(self.sel, len(rows) - 1))


def run(stdscr, sessions, query, skip):
    return View(stdscr, sessions, query, skip).loop()


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
        os.execvp("claude", cmd)
    except FileNotFoundError:
        sys.exit("`claude` not found on PATH.")


if __name__ == "__main__":
    main()
