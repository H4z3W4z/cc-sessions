"""Tests for cc-sessions: parsing, filtering, and the agent-aware launch path.

Stdlib only. Builds throwaway stores in a temp dir and stubs exec, so nothing
real is read, deleted, or launched.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sessions  # noqa: E402
import sessions_tui as tui  # noqa: E402

CODEX_ID = "01a1b397-e40d-7943-ab92-b8894d014b38"
CLAUDE_ID = "cf82e625-12a1-4ae2-9add-9b72ebb493a4"


def write_jsonl(path: Path, lines: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(d) + "\n" for d in lines))
    return path


def codex_msg(role: str, text: str) -> dict:
    return {"type": "response_item",
            "payload": {"type": "message", "role": role,
                        "content": [{"type": "input_text", "text": text}]}}


class StoreCase(unittest.TestCase):
    """Points the module at a temp ~/.claude + ~/.codex for each test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude_dir = self.tmp / "claude" / "projects"
        self.codex_dir = self.tmp / "codex" / "sessions"
        patches = [
            mock.patch.object(sessions, "PROJECTS_DIR", self.claude_dir),
            mock.patch.object(sessions, "CODEX_DIR", self.codex_dir),
            mock.patch.object(sessions, "CODEX_INDEX", self.codex_dir.parent / "session_index.jsonl"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def rollout(self, lines, sid=CODEX_ID, name=None) -> Path:
        name = name or f"rollout-2026-11-06T20-20-38-{sid}.jsonl"
        return write_jsonl(self.codex_dir / "2026" / "11" / "06" / name, lines)


class CodexParserTests(StoreCase):
    def test_injected_turns_not_counted_or_summarised(self):
        p = self.rollout([
            {"type": "session_meta", "payload": {"id": CODEX_ID, "cwd": "/work"}},
            {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": "sys"}},
            codex_msg("user", "# AGENTS.md instructions\n\n<INSTRUCTIONS>…"),
            codex_msg("user", "<environment_context>cwd=/work</environment_context>"),
            codex_msg("user", "fix the   flaky test"),
            codex_msg("assistant", "done"),
        ])
        s = sessions.parse_codex_session(p, {})
        self.assertEqual(s["msgs"], 2)
        self.assertEqual(s["summary"], "fix the flaky test")
        self.assertEqual(s["cwd"], "/work")
        self.assertEqual(s["id"], CODEX_ID)

    def test_id_falls_back_to_session_meta_when_filename_has_no_uuid(self):
        p = self.rollout([{"type": "session_meta", "payload": {"id": "meta-id", "cwd": "/w"}}],
                         name="rollout-odd.jsonl")
        self.assertEqual(sessions.parse_codex_session(p, {})["id"], "meta-id")

    def test_meta_only_session_degrades_gracefully(self):
        p = self.rollout([{"type": "session_meta", "payload": {"id": CODEX_ID}}])
        s = sessions.parse_codex_session(p, {CODEX_ID: "test1"})
        self.assertEqual((s["msgs"], s["summary"], s["name"]), (0, "", "test1"))
        self.assertEqual(s["cwd"], str(Path.home()))

    def test_garbage_lines_are_skipped(self):
        p = self.codex_dir / "2026" / "11" / "06" / f"rollout-x-{CODEX_ID}.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text('not json\n\n{"type": "session_meta", "payload": {"cwd": "/w"}}\n[1,2]\n')
        # a bare JSON array line has no .get(); it must not crash the scan
        try:
            s = sessions.parse_codex_session(p, {})
        except AttributeError as e:
            self.fail(f"non-object JSON line crashed the parser: {e}")
        self.assertEqual(s["cwd"], "/w")

    def test_index_is_read_beside_overridden_store(self):
        self.rollout([{"type": "session_meta", "payload": {"id": CODEX_ID}}])
        write_jsonl(self.codex_dir.parent / "session_index.jsonl",
                    [{"id": CODEX_ID, "thread_name": "old"},
                     {"id": CODEX_ID, "thread_name": "renamed"}])
        [s] = sessions.collect_codex()
        self.assertEqual(s["name"], "renamed")


class MergeTests(StoreCase):
    def test_one_store_absent_is_fine(self):
        self.rollout([{"type": "session_meta", "payload": {"id": CODEX_ID}}])
        self.assertEqual([s["agent"] for s in sessions.collect()], ["codex"])

    def test_both_absent_exits(self):
        with self.assertRaises(SystemExit):
            sessions.collect()

    def test_merged_newest_first(self):
        old = self.rollout([{"type": "session_meta", "payload": {"id": CODEX_ID}}])
        os.utime(old, (1_000, 1_000))
        write_jsonl(self.claude_dir / "-work" / f"{CLAUDE_ID}.jsonl",
                    [{"type": "user", "cwd": "/work", "message": {"content": "hi"}}])
        self.assertEqual([s["agent"] for s in sessions.collect()], ["claude", "codex"])


class FilterTests(unittest.TestCase):
    cx = {"agent": "codex", "name": "", "cwd": "/home/m/claude-tools", "summary": "vast thing", "first": ""}
    cl = {"agent": "claude", "name": "vast", "cwd": "/home/m/app", "summary": "", "first": ""}

    def test_split(self):
        self.assertEqual(sessions.split_agent_query("@CX vast"), ("codex", "vast"))
        self.assertEqual(sessions.split_agent_query("foo @cl"), ("claude", "foo"))
        self.assertEqual(sessions.split_agent_query("@foo"), (None, "@foo"))

    def test_agent_token_is_exact_not_substring(self):
        # "claude" appears in the codex session's cwd; @claude must still exclude it
        self.assertFalse(sessions.matches(self.cx, "@claude"))
        self.assertTrue(sessions.matches(self.cl, "@claude"))
        self.assertTrue(sessions.matches(self.cx, "@cx vast"))
        self.assertFalse(sessions.matches(self.cl, "@cx vast"))

    def test_plain_agent_word_still_matches(self):
        self.assertTrue(sessions.matches(self.cx, "codex"))


class LaunchTests(unittest.TestCase):
    def session(self, agent):
        return {"id": CODEX_ID, "agent": agent, "cwd": tempfile.gettempdir()}

    def run_launch(self, choice):
        with mock.patch.object(tui.os, "execvp") as execvp, \
             mock.patch.object(tui.os, "chdir") as chdir, \
             mock.patch("builtins.print"):
            tui.launch(*tui.resolve_launch(choice))
        chdir.assert_called_once_with(tempfile.gettempdir())
        return execvp.call_args.args

    def test_codex_resume_execs_codex(self):
        prog, argv = self.run_launch(("resume", self.session("codex"), True))
        self.assertEqual(prog, "codex")
        self.assertEqual(argv, ["codex", "resume", CODEX_ID])  # no skip-perms flag

    def test_claude_resume_execs_claude_with_skip(self):
        prog, argv = self.run_launch(("resume", self.session("claude"), True))
        self.assertEqual(prog, "claude")
        self.assertEqual(argv, ["claude", "--resume", CODEX_ID, tui.SKIP_FLAG])

    def test_new_session_execs_claude(self):
        prog, argv = self.run_launch(("new", tempfile.gettempdir(), False))
        self.assertEqual((prog, argv), ("claude", ["claude"]))

    def test_missing_binary_names_the_right_agent(self):
        with mock.patch.object(tui.os, "execvp", side_effect=FileNotFoundError), \
             mock.patch.object(tui.os, "chdir"), mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                tui.launch(*tui.resolve_launch(("resume", self.session("codex"), False)))
        self.assertIn("`codex`", str(cm.exception))

    def test_vanished_directory_exits_before_exec(self):
        s = {"id": CODEX_ID, "agent": "codex", "cwd": "/nonexistent/xyz"}
        with mock.patch.object(tui.os, "execvp") as execvp, mock.patch("builtins.print"):
            with self.assertRaises(SystemExit):
                tui.launch(*tui.resolve_launch(("resume", s, False)))
        execvp.assert_not_called()


class DeleteTests(unittest.TestCase):
    def test_codex_delete_leaves_neighbouring_dirs(self):
        root = Path(tempfile.mkdtemp())
        log = write_jsonl(root / f"rollout-x-{CODEX_ID}.jsonl", [{}])
        (root / CODEX_ID).mkdir()  # pathological same-named dir must survive
        tui.delete_session({"agent": "codex", "id": CODEX_ID, "path": str(log)})
        self.assertFalse(log.exists())
        self.assertTrue((root / CODEX_ID).is_dir())

    def test_claude_delete_removes_subagent_dir(self):
        root = Path(tempfile.mkdtemp())
        log = write_jsonl(root / f"{CLAUDE_ID}.jsonl", [{}])
        (root / CLAUDE_ID / "subagents").mkdir(parents=True)
        tui.delete_session({"agent": "claude", "id": CLAUDE_ID, "path": str(log)})
        self.assertFalse(log.exists() or (root / CLAUDE_ID).exists())

    def test_prompt_wording_per_agent(self):
        base = {"id": CODEX_ID, "name": "n", "summary": ""}
        self.assertIn("subagent", tui.delete_prompt({**base, "agent": "claude"}))
        self.assertNotIn("subagent", tui.delete_prompt({**base, "agent": "codex"}))


if __name__ == "__main__":
    unittest.main()
