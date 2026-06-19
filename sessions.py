#!/usr/bin/env python3
"""List all Claude Code sessions across every project directory.

Reads ~/.claude/projects/*/*.jsonl (the session logs Claude Code writes on
disk) and prints one row per session sorted by most-recently used, with the
exact `claude --resume` command for each.

Usage:
    python3 sessions.py            # all sessions, newest first
    python3 sessions.py -n 15      # only the 15 most recent
    python3 sessions.py vast       # only sessions whose name/dir/summary matches "vast"
    python3 sessions.py --json     # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def first_user_text(message) -> str | None:
    """Pull plain text out of a user message, whatever shape it's in."""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text")
    return None


def is_noise(text: str) -> bool:
    """Skip harness wrappers so the auto-summary reflects what the user typed."""
    t = text.lstrip()
    return t.startswith("<system-reminder>") or t.startswith("<command-")


def parse_session(path: Path) -> dict:
    title = None
    cwd = None
    session_id = path.stem
    summary = None  # first real user message, used when there's no custom name

    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if d.get("customTitle"):
                    title = d["customTitle"]
                if not cwd and d.get("cwd"):
                    cwd = d["cwd"]
                if d.get("sessionId"):
                    session_id = d["sessionId"]
                if summary is None and d.get("type") == "user":
                    text = first_user_text(d.get("message", {}))
                    if text and not is_noise(text):
                        summary = " ".join(text.split())[:60]
    except OSError:
        pass

    # Fall back to decoding the directory name if no cwd line was found.
    if not cwd:
        cwd = "/" + path.parent.name.lstrip("-").replace("-", "/")

    return {
        "id": session_id,
        "name": title or "",
        "summary": summary or "",
        "cwd": cwd,
        "mtime": path.stat().st_mtime,
        "size": path.stat().st_size,
        "path": str(path),
    }


def collect() -> list[dict]:
    if not PROJECTS_DIR.is_dir():
        sys.exit(f"No Claude Code projects directory found at {PROJECTS_DIR}")
    sessions = [parse_session(p) for p in PROJECTS_DIR.glob("*/*.jsonl")]
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}T"


def main() -> None:
    ap = argparse.ArgumentParser(description="List all Claude Code sessions.")
    ap.add_argument("filter", nargs="?", help="case-insensitive match on name/dir/summary")
    ap.add_argument("-n", "--limit", type=int, help="show only the N most recent")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args()

    sessions = collect()

    if args.filter:
        q = args.filter.lower()
        sessions = [
            s for s in sessions
            if q in s["name"].lower() or q in s["cwd"].lower() or q in s["summary"].lower()
        ]
    if args.limit:
        sessions = sessions[: args.limit]

    if args.json:
        print(json.dumps(sessions, indent=2))
        return

    if not sessions:
        print("No sessions found.")
        return

    home = str(Path.home())
    rows = []
    for s in sessions:
        when = datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
        label = s["name"] or (f"({s['summary']})" if s["summary"] else "—")
        cwd = s["cwd"].replace(home, "~", 1)
        rows.append((when, label, cwd, human_size(s["size"]), s["id"][:8]))

    headers = ("LAST USED", "NAME / SUMMARY", "DIRECTORY", "SIZE", "RESUME ID")
    widths = [
        max(len(headers[i]), max(len(r[i]) for r in rows)) for i in range(len(headers))
    ]
    # Keep the variable-width columns from running away.
    widths[1] = min(widths[1], 40)
    widths[2] = min(widths[2], 50)

    def fmt(cols):
        return "  ".join(str(c)[: widths[i]].ljust(widths[i]) for i, c in enumerate(cols))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))

    print(f"\n{len(sessions)} session(s). Resume with:  claude --resume <RESUME ID>")


if __name__ == "__main__":
    main()
