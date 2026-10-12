#!/usr/bin/env python3
"""Session budget guardrails for Claude Code.

The routing policy in `agent-routing/policy.yaml` budgets *agents*. This script
budgets the *session*: it makes a run that is about to lose its context — to
auto-compaction or to a rate limit — leave behind enough state that the next run
resumes at delta cost instead of re-deriving everything from zero.

Subcommands map 1:1 to hook events wired in `.claude/settings.example.json`:

    pre-tool-use    PreToolUse     nudge the session to hand off as context fills
    pre-compact     PreCompact     capture state before auto-compaction summarizes it
    stop-failure    StopFailure    capture state when a rate limit ends the turn
    session-start   SessionStart   replay the captured handoff into the new session
    capture         (manual)       capture on demand: `... capture < /dev/null`

Every subcommand reads the hook payload as JSON on stdin and is written to fail
open. A guardrail that breaks the session it protects is worse than no guardrail,
so unexpected errors exit 0 with a diagnostic on stderr.

The handoff file holds two blocks. `model-notes` is authored by the session and
preserved verbatim across every regeneration — it is the part a script cannot
write, because "what is left to do" is judgement. `auto-state` is regenerated on
every capture from git and the transcript, and is exactly the part that must not
be judgement: see the *Deterministic work is not agent work* rule in CLAUDE.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default assumes a 200K context. Override for a 1M-context model with
# CLAUDE_SESSION_BUDGET_CONTEXT_WINDOW; hook payloads do not carry the size the
# way the status line payload does.
DEFAULT_CONTEXT_WINDOW = 200_000
WRITE_HANDOFF_AT = 0.70
WRAP_UP_AT = 0.85

# Only the tail of the transcript is read: it grows without bound and the most
# recent usage block is the only one that describes the current context.
TRANSCRIPT_TAIL_BYTES = 262_144

# `.claude/settings.example.json` gives PreCompact/StopFailure 20s total. collect_state
# runs several git commands serially, so they share one deadline rather than each
# getting its own timeout: on a slow or wedged repository the capture must still reach
# the write, since losing the checkpoint is the failure this script exists to prevent.
GIT_TOTAL_BUDGET_SECONDS = 8.0
GIT_MIN_CALL_SECONDS = 0.5
PROMPT_EXCERPT_CHARS = 500
MAX_LISTED_PATHS = 20
MAX_DIFFSTAT_LINES = 25

NOTES_START = "<!-- claude:model-notes:start -->"
NOTES_END = "<!-- claude:model-notes:end -->"
STATE_START = "<!-- claude:auto-state:start -->"
STATE_END = "<!-- claude:auto-state:end -->"

NOTES_TEMPLATE = f"""{NOTES_START}
## Model notes

_Authored by the session, preserved across captures. Keep it short and current._

- **Goal:** _(not recorded)_
- **Done:**
- **Remaining:**
- **Next command:**
- **Decisions worth not re-litigating:**
{NOTES_END}"""


def log(message: str) -> None:
    print(f"session_budget: {message}", file=sys.stderr)


def read_payload() -> dict[str, Any]:
    """Read the hook payload from stdin, tolerating an empty or malformed body."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def repo_root() -> Path:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[2]


def handoff_path(root: Path) -> Path:
    return root / "artifacts" / "handoff" / "SESSION-HANDOFF.md"


def nudge_state_path(root: Path) -> Path:
    return root / "artifacts" / "handoff" / ".nudge-state.json"


def git(root: Path, *args: str, deadline: float | None = None) -> str:
    """Run a read-only git command, returning "" when git is unavailable or too slow.

    `deadline` is a shared monotonic budget across all of a capture's git calls, so a
    slow repository cannot consume the whole hook timeout before the handoff is written.
    """
    if deadline is None:
        timeout = GIT_TOTAL_BUDGET_SECONDS
    else:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ""
        timeout = max(GIT_MIN_CALL_SECONDS, remaining)
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    # rstrip only: `git status --porcelain` and `git diff --stat` carry meaningful
    # leading whitespace on their first line, and stripping it misaligns the block.
    return result.stdout.rstrip() if result.returncode == 0 else ""


def transcript_tail(transcript: Path) -> list[str]:
    """Return the tail of the JSONL transcript as raw lines, newest last.

    The transcript grows without bound, and every caller here wants the newest
    matching entry, so the tail is read raw and parsed lazily from the end
    rather than deserialized in full on every tool call.
    """
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as handle:
            if size > TRANSCRIPT_TAIL_BYTES:
                offset = size - TRANSCRIPT_TAIL_BYTES
                handle.seek(offset - 1 if offset else 0)
                # Discard only a line the seek actually split. Landing exactly on a
                # record boundary means the next line is whole, and dropping it can
                # throw away the only usage entry in the tail.
                split_line = offset > 0 and handle.read(1) != b"\n"
                if split_line:
                    handle.readline()
            body = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in body.splitlines() if line.strip()]


def newest_entries(lines: list[str]):
    """Yield parsed main-chain transcript objects, newest first.

    Entries marked `isSidechain` belong to a subagent, not to this session. A small
    subagent usage block arriving after a nearly-full main context would otherwise read
    as plenty of headroom and suppress the very warning it should trigger.
    """
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("isSidechain") is not True:
            yield entry


def _usage_of(entry: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in (entry.get("message"), entry):
        if isinstance(candidate, dict):
            usage = candidate.get("usage")
            if isinstance(usage, dict):
                return usage
    return None


def estimate_context_tokens(lines: list[str]) -> int | None:
    """Approximate current context occupancy from the newest usage block.

    An assistant turn is billed for everything it read plus what it wrote, so
    input + cache_read + cache_creation + output is the closest proxy a hook can
    compute. It is an estimate, and the thresholds tolerate that.
    """
    for entry in newest_entries(lines):
        usage = _usage_of(entry)
        if not usage:
            continue
        total = 0
        for field in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        ):
            value = usage.get(field)
            if isinstance(value, int):
                total += value
        if total:
            return total
    return None


def last_user_prompt(lines: list[str]) -> str:
    """The newest user text, truncated. Local state only — see docs/session-budget.md."""
    for entry in newest_entries(lines):
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "\n".join(part for part in parts if part)
        text = text.strip()
        if not text or text.startswith("<"):
            continue  # skip tool-result and system-reminder envelopes
        if len(text) > PROMPT_EXCERPT_CHARS:
            text = text[:PROMPT_EXCERPT_CHARS].rstrip() + " […]"
        return text
    return ""


def context_window() -> int:
    raw = os.environ.get("CLAUDE_SESSION_BUDGET_CONTEXT_WINDOW", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CONTEXT_WINDOW
    return value if value > 0 else DEFAULT_CONTEXT_WINDOW


def collect_state(payload: dict[str, Any], trigger: str, root: Path) -> dict[str, Any]:
    transcript_raw = payload.get("transcript_path")
    lines = (
        transcript_tail(Path(transcript_raw))
        if isinstance(transcript_raw, str) and transcript_raw
        else []
    )
    used = estimate_context_tokens(lines)
    deadline = time.monotonic() + GIT_TOTAL_BUDGET_SECONDS
    status = git(root, "status", "--porcelain=v1", deadline=deadline)
    changed = [line for line in status.splitlines() if line.strip()]
    return {
        "trigger": trigger,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "session_id": payload.get("session_id") or "unknown",
        "cwd": payload.get("cwd") or str(root),
        "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD", deadline=deadline) or "unknown",
        "head": git(root, "log", "-1", "--format=%h %s", deadline=deadline) or "unknown",
        "recent_commits": git(root, "log", "-5", "--format=%h %s", deadline=deadline),
        "changed_paths": changed[:MAX_LISTED_PATHS],
        "changed_total": len(changed),
        "diffstat": "\n".join(
            git(root, "diff", "--stat", "HEAD", deadline=deadline).splitlines()[:MAX_DIFFSTAT_LINES]
        ),
        "context_tokens": used,
        "context_window": context_window(),
        "last_user_prompt": last_user_prompt(lines),
    }


def render_auto_state(state: dict[str, Any]) -> str:
    used = state.get("context_tokens")
    window = state.get("context_window") or DEFAULT_CONTEXT_WINDOW
    if isinstance(used, int):
        context_line = f"{used:,} / {window:,} tokens (~{used / window:.0%} of the window)"
    else:
        context_line = "not observed in the transcript tail"

    lines = [
        STATE_START,
        "## Auto-captured state",
        "",
        "_Regenerated on every capture. Do not hand-edit — edits land in Model notes._",
        "",
        f"- **Trigger:** `{state['trigger']}`",
        f"- **Captured:** {state['captured_at']}",
        f"- **Session:** `{state['session_id']}`",
        f"- **Branch:** `{state['branch']}`",
        f"- **HEAD:** `{state['head']}`",
        f"- **Context at capture:** {context_line}",
        "",
        f"### Uncommitted changes ({state['changed_total']})",
        "",
    ]

    if state["changed_paths"]:
        lines.append("```")
        lines.extend(state["changed_paths"])
        if state["changed_total"] > len(state["changed_paths"]):
            lines.append(f"… {state['changed_total'] - len(state['changed_paths'])} more")
        lines.append("```")
        if state["diffstat"]:
            lines.extend(["", "```", state["diffstat"], "```"])
    else:
        lines.append("Working tree clean.")

    if state["recent_commits"]:
        lines.extend(["", "### Recent commits", "", "```", state["recent_commits"], "```"])

    if state["last_user_prompt"]:
        lines.extend(["", "### Last user request", "", "> " + state["last_user_prompt"].replace("\n", "\n> ")])

    lines.extend(["", STATE_END])
    return "\n".join(lines)


def extract_notes(existing: str) -> str:
    """Recover the model-notes block from an existing handoff file.

    The notes are the one part of this file a script cannot regenerate, so a
    file whose markers are missing, truncated or out of order is salvaged rather
    than replaced: dropping a block only a human or a session could have written
    is the worst thing this script could do.
    """
    if not existing.strip():
        return NOTES_TEMPLATE

    # Exactly one well-formed pair is the normal case and is taken verbatim. Anything
    # else — duplicated blocks, nested markers, a lost closing marker, reversed order —
    # falls through to salvage, which keeps every candidate note line. Selecting the
    # first syntactically closed pair would silently drop a second block, and this is
    # the one part of the file nothing can regenerate.
    starts, ends = existing.count(NOTES_START), existing.count(NOTES_END)
    if starts == 1 and ends == 1:
        start, end = existing.find(NOTES_START), existing.find(NOTES_END)
        if end > start:
            return existing[start:end + len(NOTES_END)]

    salvage = existing.split(STATE_START, 1)[0]
    salvage = salvage.replace(NOTES_START, "").replace(NOTES_END, "")
    salvage = salvage.strip().removeprefix("# Session handoff").strip()
    if not salvage:
        return NOTES_TEMPLATE
    return (
        f"{NOTES_START}\n## Model notes\n\n"
        "_Recovered: the block markers were missing or out of order._\n\n"
        f"{salvage}\n{NOTES_END}"
    )


def merge_handoff(existing: str, auto_block: str) -> str:
    """Replace the auto-state block, preserving model notes."""
    return f"# Session handoff\n\n{extract_notes(existing)}\n\n{auto_block}\n"


def write_atomically(path: Path, content: str) -> None:
    """Write via a temporary file in the same directory, then rename over the target.

    `Path.write_text` truncates before writing, so an interruption or a full filesystem
    can leave a half-written handoff — and the fail-open handler would report success
    over the wreckage of notes no salvage could reconstruct. Renaming a complete file
    into place means the destination only ever holds a whole handoff.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def capture(payload: dict[str, Any], trigger: str) -> Path:
    root = repo_root()
    path = handoff_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    state = collect_state(payload, trigger, root)
    write_atomically(path, merge_handoff(existing, render_auto_state(state)))
    return path


def cmd_capture(payload: dict[str, Any], trigger: str) -> int:
    path = capture(payload, trigger)
    log(f"captured handoff ({trigger}) -> {path}")
    return 0


def cmd_pre_tool_use(payload: dict[str, Any]) -> int:
    transcript_raw = payload.get("transcript_path")
    if not isinstance(transcript_raw, str) or not transcript_raw:
        return 0
    used = estimate_context_tokens(transcript_tail(Path(transcript_raw)))
    if used is None:
        return 0

    window = context_window()
    fraction = used / window
    level = "wrap-up" if fraction >= WRAP_UP_AT else "handoff" if fraction >= WRITE_HANDOFF_AT else None
    if level is None:
        return 0

    root = repo_root()
    session_id = str(payload.get("session_id") or "unknown")
    state_file = nudge_state_path(root)
    try:
        previous = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    if previous.get("session_id") == session_id and previous.get("level") == level:
        return 0  # already nudged at this level in this session

    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"session_id": session_id, "level": level}), encoding="utf-8"
        )
    except OSError:
        pass

    path = handoff_path(root)
    rel = path.relative_to(root) if path.is_relative_to(root) else path
    if level == "handoff":
        message = (
            f"Context is ~{fraction:.0%} full ({used:,}/{window:,} tokens). Update the "
            f"Model notes block of `{rel}` now — goal, what is done, what remains, the "
            "next command — so a restart resumes from it instead of re-deriving the "
            "session. Then continue working."
        )
    else:
        message = (
            f"Context is ~{fraction:.0%} full ({used:,}/{window:,} tokens). Finish or "
            f"park the current step, commit what is working, make sure the Model notes "
            f"block of `{rel}` is current, and stop cleanly rather than being cut off "
            "mid-step."
        )

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": message,
                }
            }
        )
    )
    return 0


def cmd_session_start(payload: dict[str, Any]) -> int:
    path = handoff_path(repo_root())
    if not path.exists():
        return 0
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    if not content:
        return 0
    source = payload.get("source") or payload.get("matcher") or "session start"
    print(
        f"A handoff from an earlier session is on disk ({source}). Read it before "
        f"re-deriving anything, and keep its Model notes block current as you work.\n\n"
        f"{content}"
    )
    return 0


HANDLERS = {
    "pre-tool-use": lambda payload: cmd_pre_tool_use(payload),
    "pre-compact": lambda payload: cmd_capture(payload, "pre-compact"),
    "stop-failure": lambda payload: cmd_capture(payload, "stop-failure:rate-limit"),
    "session-start": lambda payload: cmd_session_start(payload),
    "capture": lambda payload: cmd_capture(payload, "manual"),
}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in HANDLERS:
        log(f"usage: {Path(argv[0]).name} {{{'|'.join(HANDLERS)}}}")
        return 2
    payload = read_payload()
    try:
        return HANDLERS[argv[1]](payload)
    except Exception as exc:  # fail open: never break the session being protected
        log(f"{argv[1]} failed, continuing: {exc!r}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
