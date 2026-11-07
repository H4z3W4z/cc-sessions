#!/usr/bin/env python3
"""List all Claude Code and Codex sessions across every project directory.

Reads the session logs each agent writes on disk and prints one row per
session, newest first, with the exact resume command for each:

  * Claude Code  ~/.claude/projects/*/*.jsonl        -> claude --resume <id>
  * Codex        ~/.codex/sessions/*/*/*/rollout-*.jsonl -> codex resume <id>

Usage:
    python3 sessions.py            # all sessions, newest first
    python3 sessions.py -n 15      # only the 15 most recent
    python3 sessions.py vast       # name/dir/summary/agent matches "vast"
    python3 sessions.py @codex     # only Codex sessions (@claude/@cl/@cx too)
    python3 sessions.py @cx vast   # Codex sessions matching "vast"
    python3 sessions.py --json     # machine-readable output

Environment overrides (handy for testing against a copied store):
    CC_CLAUDE_DIR   override ~/.claude/projects
    CC_CODEX_DIR    override ~/.codex/sessions (session_index.jsonl is read
                    from its parent directory, mirroring ~/.codex)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


def _dir(env: str, default: Path) -> Path:
    v = os.environ.get(env)
    return Path(v).expanduser() if v else default


PROJECTS_DIR = _dir("CC_CLAUDE_DIR", Path.home() / ".claude" / "projects")
CODEX_DIR = _dir("CC_CODEX_DIR", Path.home() / ".codex" / "sessions")
# The index sits beside sessions/ in ~/.codex; derive it so CC_CODEX_DIR moves both.
CODEX_INDEX = CODEX_DIR.parent / "session_index.jsonl"

# UUID embedded in a Codex rollout filename: rollout-<ISO8601>-<uuid>.jsonl
_CODEX_ID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


# ---------------------------------------------------------------------------
# filtering
# ---------------------------------------------------------------------------
AGENT_ALIASES = {"claude": "claude", "cl": "claude", "codex": "codex", "cx": "codex"}
SEARCH_FIELDS = ("name", "cwd", "summary", "agent")


def split_agent_query(query: str) -> tuple[str | None, str]:
    """Split '@cx vast' into ('codex', 'vast').

    An @agent token (@claude/@cl/@codex/@cx, any case, anywhere in the query)
    restricts by agent exactly; the remaining words are a substring match.
    Unknown @tokens are left in the text so '@foo' still searches for '@foo'.
    """
    agent, rest = None, []
    for tok in query.split():
        alias = AGENT_ALIASES.get(tok[1:].lower()) if tok.startswith("@") else None
        if alias:
            agent = alias
        else:
            rest.append(tok)
    return agent, " ".join(rest)


def matches(s: dict, query: str, fields: tuple[str, ...] = SEARCH_FIELDS) -> bool:
    """Case-insensitive match of a session against a filter query."""
    agent, text = split_agent_query(query)
    if agent and s["agent"] != agent:
        return False
    q = text.lower()
    return not q or any(q in s[f].lower() for f in fields)


def first_user_text(message) -> str | None:
    """Pull plain text out of a Claude user message, whatever shape it's in."""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text")
    return None


def is_noise(text: str) -> bool:
    """Skip harness/agent wrappers so the auto-summary reflects what the user typed."""
    t = text.lstrip()
    return (
        t.startswith("<system-reminder>")
        or t.startswith("<command-")
        or t.startswith("# AGENTS.md")
        or t.startswith("<INSTRUCTIONS>")
        or t.startswith("<user-prompt-submit-hook>")
        or t.startswith("<environment_context>")
        or t.startswith("<user_instructions>")
        # Codex resume/fork injects the prior transcript as the first "user" turn
        or t.startswith("The following is the Codex agent history")
    )


# ---------------------------------------------------------------------------
# Claude Code sessions
# ---------------------------------------------------------------------------
def parse_session(path: Path) -> dict:
    title = None
    cwd = None
    session_id = path.stem
    summary = None  # first real user message, used when there's no custom name
    first_full = None  # full first user message, for the TUI preview panel
    msgs = 0  # count of user + assistant turns

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
                if not isinstance(d, dict):  # valid JSON, but not an event object
                    continue

                if d.get("customTitle"):
                    title = d["customTitle"]
                if not cwd and d.get("cwd"):
                    cwd = d["cwd"]
                if d.get("sessionId"):
                    session_id = d["sessionId"]
                if d.get("type") in ("user", "assistant"):
                    msgs += 1
                if summary is None and d.get("type") == "user":
                    text = first_user_text(d.get("message", {}))
                    if text and not is_noise(text):
                        flat = " ".join(text.split())
                        summary = flat[:60]
                        first_full = flat[:2000]
    except OSError:
        pass

    # Fall back to decoding the directory name if no cwd line was found.
    if not cwd:
        cwd = "/" + path.parent.name.lstrip("-").replace("-", "/")

    return {
        "id": session_id,
        "agent": "claude",
        "name": title or "",
        "summary": summary or "",
        "first": first_full or "",
        "msgs": msgs,
        "cwd": cwd,
        "mtime": path.stat().st_mtime,
        "size": path.stat().st_size,
        "path": str(path),
    }


def collect_claude() -> list[dict]:
    if not PROJECTS_DIR.is_dir():
        return []
    out = []
    for p in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            out.append(parse_session(p))
        except OSError:  # vanished between glob and stat (e.g. deleted mid-scan)
            continue
    return out


# ---------------------------------------------------------------------------
# Codex sessions
# ---------------------------------------------------------------------------
def _codex_index_names() -> dict[str, str]:
    """Best-effort {session_id: thread_name} from Codex's partial index.

    The index has at most one line per (id, rename) and may lag the on-disk
    rollouts, so it is advisory only — a nice title when present.
    """
    names: dict[str, str] = {}
    if not CODEX_INDEX.is_file():
        return names
    try:
        with CODEX_INDEX.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):  # valid JSON, but not an event object
                    continue
                sid, name = d.get("id"), d.get("thread_name")
                if sid and name:
                    names[sid] = name  # last write wins -> most recent rename
    except OSError:
        pass
    return names


def _codex_user_text(payload: dict) -> str | None:
    """Extract plain text from a Codex user-message payload.

    Content may be a bare string or a list of blocks like
    [{"type": "input_text", "text": "..."}].
    """
    content = payload.get("content")
    if content is None:
        content = payload.get("message")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                "input_text", "text", "output_text"
            ):
                t = block.get("text")
                if t:
                    parts.append(t)
        if parts:
            return "\n".join(parts)
    return None


def parse_codex_session(path: Path, names: dict[str, str]) -> dict:
    m = _CODEX_ID_RE.search(path.name)
    session_id = m.group(1) if m else None  # filename is authoritative when it has one
    cwd = None
    summary = None
    first_full = None
    msgs = 0  # count of message events (user + assistant)

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
                if not isinstance(d, dict):  # valid JSON, but not an event object
                    continue

                typ = d.get("type")
                payload = d.get("payload")
                if not isinstance(payload, dict):
                    payload = {}

                if typ == "session_meta":
                    if not cwd and payload.get("cwd"):
                        cwd = payload["cwd"]
                    if not session_id and payload.get("id"):
                        session_id = payload["id"]
                # Conversation turns are `response_item` lines carrying a
                # message payload; key off the payload type, not the wrapper.
                elif payload.get("type") == "message":
                    role = payload.get("role")
                    if role == "assistant":
                        msgs += 1
                    elif role == "user":
                        # Codex injects AGENTS.md / environment context as
                        # "user" turns; those are neither counted nor summarised.
                        text = _codex_user_text(payload)
                        if text and is_noise(text):
                            continue
                        msgs += 1
                        if summary is None and text:
                            flat = " ".join(text.split())
                            summary = flat[:60]
                            first_full = flat[:2000]
    except OSError:
        pass

    if not session_id:
        session_id = path.stem
    if not cwd:
        cwd = str(Path.home())

    return {
        "id": session_id,
        "agent": "codex",
        "name": names.get(session_id, ""),
        "summary": summary or "",
        "first": first_full or "",
        "msgs": msgs,
        "cwd": cwd,
        "mtime": path.stat().st_mtime,
        "size": path.stat().st_size,
        "path": str(path),
    }


def collect_codex() -> list[dict]:
    if not CODEX_DIR.is_dir():
        return []
    names = _codex_index_names()
    out = []
    for p in CODEX_DIR.glob("*/*/*/rollout-*.jsonl"):
        try:
            out.append(parse_codex_session(p, names))
        except OSError:
            continue
    return out


# ---------------------------------------------------------------------------
# merged
# ---------------------------------------------------------------------------
def collect() -> list[dict]:
    sessions = collect_claude() + collect_codex()
    if not sessions and not PROJECTS_DIR.is_dir() and not CODEX_DIR.is_dir():
        sys.exit(
            f"No session stores found (looked in {PROJECTS_DIR} and {CODEX_DIR})."
        )
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            # bytes/KiB read cleaner as integers; MiB+ get one decimal (2.1M)
            if unit in ("B", "K"):
                return f"{size:.0f}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024


def main() -> None:
    ap = argparse.ArgumentParser(
        description="List all Claude Code and Codex sessions."
    )
    ap.add_argument(
        "filter", nargs="*",
        help="case-insensitive match on name/dir/summary/agent; @claude/@codex (or @cl/@cx) restricts by agent",
    )
    ap.add_argument("-n", "--limit", type=int, help="show only the N most recent")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args()

    sessions = collect()

    query = " ".join(args.filter)
    if query:
        sessions = [s for s in sessions if matches(s, query)]
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
        rows.append((when, s["agent"], label, cwd, human_size(s["size"]), s["id"][:8]))

    headers = ("LAST USED", "AGENT", "NAME / SUMMARY", "DIRECTORY", "SIZE", "RESUME ID")
    widths = [
        max(len(headers[i]), max(len(r[i]) for r in rows)) for i in range(len(headers))
    ]
    # Keep the variable-width columns from running away.
    widths[2] = min(widths[2], 40)
    widths[3] = min(widths[3], 50)

    def fmt(cols):
        return "  ".join(str(c)[: widths[i]].ljust(widths[i]) for i, c in enumerate(cols))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))

    print(
        f"\n{len(sessions)} session(s). "
        "Resume with:  claude --resume <ID>   |   codex resume <ID>"
    )


if __name__ == "__main__":
    main()
