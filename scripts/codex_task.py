#!/usr/bin/env python3
"""Render a Codex task comment from a contract, and refuse one that must not be posted.

docs/claude-to-codex.md makes every Codex task comment a contract with exact
rules: the mention first and nowhere else (section 3), every contract field
with a real commit SHA (section 5), a tracking marker that is never posted
twice (section 6), round caps and one active request per thread (section 16),
and lines an implementation request must always carry (section 19). Every one
of those rules is computable, so this script applies them rather than a model
-- see *Deterministic work is not agent work* in CLAUDE.md. It is the posting
half of the codex lane (docs/codex-lane.md); the review half is
scripts/codex_round_count.py.

    python3 scripts/codex_task.py validate contract.yaml
    python3 scripts/codex_task.py render contract.yaml --comments c.json --comments-after c2.json
    python3 scripts/codex_task.py render fix.yaml --comments c.json --reviews r.json --review-comments rc.json \
        --comments-after c2.json --reviews-after r2.json --review-comments-after rc2.json

`validate` checks the contract alone and prints it normalized. `render` also
reads the thread it will be posted in -- the Issue's or the PR's issue
comments, every page -- and prints the comment body to post only when:

  - no marker with the same task_id + mode + target_sha + round exists;
  - the round is the next one for this task_id + mode (no skipped round, and
    no second post of a round at another SHA);
  - the round is within lanes.codex.round_caps in agent-routing/policy.yaml;
  - no request of the same mode targets the same SHA (section 16), except the
    next round of the same read-only task;
  - a read-only correction round stays on the SHA its first round read;
  - every Codex request in the thread is answered (section 17) -- a task by
    its final report (a Summary and a Testing section), a merge command by
    the merge commit it reports, a review request by a review result; an
    "On it", a task link or a status line is still running -- and the newest
    task request did not end in a usage-limit or error notice (BLOCKED).

Modes are the three the codex lane posts: BRAINSTORM, IMPLEMENT and FIX. The
built-in review commands carry no contract (section 6) and are gated by
scripts/codex_round_count.py instead.

Contract file (YAML or JSON): task_id, mode, repository, base_branch,
target_branch (null allowed for BRAINSTORM only), target_sha (the full 40- or
64-character SHA from `git rev-parse`), goal, scope, out_of_scope,
write_policy, constraints, acceptance_criteria; optional round (default 1),
required_output (default: section 5's six items), lead (the sentence under
MODE), notes (free text after the contract: the approved design, the
confirmed finding). Lines that section 7 or 19 requires for the mode are
added to constraints when missing.

On a PR, also pass its reviews and review-thread comments: a review request
is answered by a PR review, so without them its answer is invisible, and a
thread whose issue comments hold a review request is refused without them.
Fetch every list once, in that order, then each once more (the `-after`
options): a post that appears or changes between the two fetches stops the
render, as it stops codex_round_count.py.
Lists are read as codex_round_count.py reads them: a `gh api --paginate
--slurp` list of pages proves it is complete; a flat list (an MCP page) is
accepted only with the empty page after it.

Exit status: 0 valid / safe to post (the body is on stdout), 1 refused -- do
not post, 2 the contract or the comments cannot be used.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    print("PyYAML is required. Install it with: python -m pip install -r requirements-eval.txt", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "agent-routing" / "policy.yaml"
ROUND_COUNT_PATH = ROOT / "scripts" / "codex_round_count.py"

STANDARD_OUTPUT = (
    "Summary",
    "Evidence",
    "Changed files, if applicable",
    "Tests performed",
    "Remaining risks",
    "Recommended next action",
)
# Per mode: the write_policy section 5's templates pair with it, whether the
# contract names a branch to write to, the sentence under MODE, and the
# constraints the templates (section 7) or section 19 make mandatory.
MODES: dict[str, dict[str, Any]] = {
    "BRAINSTORM": {
        "write_policy": "none",
        "branch": "optional",
        "lead": "Independently review the proposed solution and repository context.",
        "constraints": ("Do not modify files.", "Do not create commits.", "Do not push branches.",
                        "Do not expose secrets."),
    },
    "IMPLEMENT": {
        "write_policy": "feature_branch_only",
        "branch": "required",
        "lead": "Implement the approved task contract below.",
        "constraints": ("Work only on the approved branch.", "Preserve unrelated behavior.",
                        "Report all changed files and tests.", "Do not force-push.", "Do not expose secrets."),
    },
    "FIX": {
        "write_policy": "current_pr_branch",
        "branch": "required",
        "lead": "Fix the confirmed finding described in the task contract below.",
        "constraints": ("Work only on the approved branch.", "Preserve unrelated behavior.",
                        "Report all changed files and tests.", "Do not force-push.", "Do not expose secrets."),
    },
}
# Section 19: Codex is never asked to push to these, whatever the base branch is.
PROTECTED_BRANCHES = {"main", "master"}
FIELDS = ("task_id", "mode", "repository", "base_branch", "target_branch", "target_sha", "goal", "scope",
          "out_of_scope", "write_policy", "constraints", "acceptance_criteria", "required_output")
OPTIONAL = {"round", "required_output", "lead", "notes"}
LISTS = ("scope", "out_of_scope", "constraints", "acceptance_criteria", "required_output")

TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# Any mention, after NFKC folding so the fullwidth lookalike counts too. Wider
# than what GitHub would notify on: refusing a contract is the safe error.
MENTION = re.compile(r"(?<![\w@])@codex(?![\w-])", re.IGNORECASE)
MARKER = re.compile(r"<!--\s*claude-codex-task\b(?P<fields>.*?)-->", re.DOTALL)
# Section 20's built-in merge command: a request like any other while it runs.
MERGE_REQUEST = re.compile(r"(?<![\w@])@codex\s+merge\s+this\s+pull\s+request\b", re.IGNORECASE)
MARKER_TOKENS = ("<!--", "-->", "claude-codex-task")


class ContractError(ValueError):
    """The contract or the input cannot be used (exit 2)."""


class Refused(RuntimeError):
    """The comment is valid but must not be posted now (exit 1)."""


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def round_caps(policy_path: Path = POLICY_PATH) -> dict[str, int]:
    try:
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        caps = policy["lanes"]["codex"]["round_caps"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise ContractError(f"cannot read lanes.codex.round_caps from {policy_path}: {exc}") from exc
    for mode in MODES:
        cap = caps.get(mode)
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ContractError(f"lanes.codex.round_caps.{mode} must be a positive integer, got {cap!r}")
    return {mode: caps[mode] for mode in MODES}


def clean_text(field: str, value: Any) -> str:
    """A non-empty string that can neither trigger Codex again nor forge a marker."""
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    folded = unicodedata.normalize("NFKC", value)
    if MENTION.search(folded):
        raise ContractError(f"{field} mentions Codex; the comment may carry the mention once, first, and nowhere "
                            "else (section 3) -- write it as `@`+`codex` if it must be referred to")
    for token in MARKER_TOKENS:
        if token in folded:
            raise ContractError(f"{field} contains {token!r}, which could forge or break the tracking marker")
    return value.strip()


def text_list(field: str, value: Any, required: bool) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{field} must be a list of strings")
    items = [clean_text(f"{field}[{i}]", item) for i, item in enumerate(value)]
    if required and not items:
        raise ContractError(f"{field} must not be empty")
    return items


def branch(field: str, value: Any) -> str:
    """A name `git check-ref-format --branch` accepts, within a conservative character set."""
    text = clean_text(field, value)
    parts = text.split("/")
    if (not BRANCH.fullmatch(text) or ".." in text or text.endswith(".")
            or any(not part or part.startswith(".") or part.endswith(".lock") for part in parts)):
        raise ContractError(f"{field} {text!r} is not a branch name")
    return text


def validate(document: Any, caps: dict[str, int]) -> dict[str, Any]:
    """The normalized contract, or ContractError naming the first rule it breaks."""
    if not isinstance(document, dict):
        raise ContractError("the contract must be a mapping")
    unknown = sorted(set(document) - set(FIELDS) - OPTIONAL)
    if unknown:
        raise ContractError(f"unknown contract field(s) {unknown}; expected {list(FIELDS)} plus {sorted(OPTIONAL)}")
    missing = [field for field in FIELDS if field not in document and field not in OPTIONAL]
    if missing:
        raise ContractError(f"missing contract field(s) {missing}")

    mode = document["mode"]
    if mode not in MODES:
        raise ContractError(f"mode {mode!r} is not one the codex lane posts: {sorted(MODES)}")
    rules = MODES[mode]
    task_id = clean_text("task_id", document["task_id"])
    if not TASK_ID.fullmatch(task_id):
        raise ContractError(f"task_id {task_id!r} must be 1-64 letters, digits, '.', '_' or '-'")
    repository = clean_text("repository", document["repository"])
    if not REPOSITORY.fullmatch(repository):
        raise ContractError(f"repository {repository!r} is not owner/name")
    base = branch("base_branch", document["base_branch"])

    target = document["target_branch"]
    if target is None or (isinstance(target, str) and target.strip().lower() == "null"):
        if rules["branch"] == "required":
            raise ContractError(f"{mode} writes to a branch, so target_branch must name it")
        target = None
    else:
        target = branch("target_branch", target)
        if rules["branch"] == "required" and (target == base or target in PROTECTED_BRANCHES):
            raise ContractError(f"target_branch {target!r} is the base or a protected branch; Codex never pushes "
                                "there (section 19) -- name the feature or PR branch")

    sha = document["target_sha"]
    if not isinstance(sha, str) or not FULL_SHA.fullmatch(sha.strip()):
        raise ContractError(f"target_sha {sha!r} is not an exact commit SHA; resolve it with `git rev-parse` -- "
                            "never latest, current, HEAD or a short SHA (section 5)")
    sha = sha.strip()

    policy = document["write_policy"]
    if policy != rules["write_policy"]:
        raise ContractError(f"{mode} takes write_policy {rules['write_policy']!r}, not {policy!r}")

    rnd = document.get("round", 1)
    if isinstance(rnd, bool) or not isinstance(rnd, int) or rnd < 1:
        raise ContractError(f"round must be a positive integer, got {rnd!r}")

    constraints = text_list("constraints", document["constraints"], required=False)
    present = {item.casefold() for item in constraints}
    constraints += [line for line in rules["constraints"] if line.casefold() not in present]
    required_output = text_list("required_output", document.get("required_output", list(STANDARD_OUTPUT)),
                                required=True)
    absent = [item for item in STANDARD_OUTPUT if item.casefold() not in {x.casefold() for x in required_output}]
    if absent:
        raise ContractError(f"required_output must keep section 5's items; missing {absent}")

    contract = {
        "task_id": task_id,
        "mode": mode,
        "round": rnd,
        "repository": repository,
        "base_branch": base,
        "target_branch": target,
        "target_sha": sha,
        "goal": clean_text("goal", document["goal"]),
        "scope": text_list("scope", document["scope"], required=True),
        "out_of_scope": text_list("out_of_scope", document["out_of_scope"], required=True),
        "write_policy": policy,
        "constraints": constraints,
        "acceptance_criteria": text_list("acceptance_criteria", document["acceptance_criteria"], required=True),
        "required_output": required_output,
        "lead": clean_text("lead", document.get("lead", rules["lead"])),
        "notes": clean_text("notes", document["notes"]) if document.get("notes") is not None else None,
    }
    if rnd > caps[mode]:
        raise Refused(f"round {rnd} of a {mode} task is past section 16's cap of {caps[mode]}; do not post -- "
                      "resolve what is left in the main session or escalate to the repository owner")
    return contract


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def render(contract: dict[str, Any]) -> str:
    """The comment body: mention, MODE, contract, notes, marker -- in section 7-13's layout."""
    groups = (
        ("task_id", "mode", "repository", "base_branch", "target_branch", "target_sha"),
        ("goal",), ("scope",), ("out_of_scope",), ("write_policy",), ("constraints",),
        ("acceptance_criteria",), ("required_output",),
    )
    blocks = [
        yaml.dump({key: contract[key] for key in keys}, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                  default_flow_style=False, width=4096).rstrip()
        for keys in groups
    ]
    parts = ["@codex", f"MODE: {contract['mode']}", contract["lead"], "Task contract:", "\n\n".join(blocks)]
    if contract["notes"]:
        parts.append(contract["notes"])
    parts.append(
        "<!--\nclaude-codex-task\n"
        f"task_id: {contract['task_id']}\nmode: {contract['mode']}\n"
        f"target_sha: {contract['target_sha']}\nround: {contract['round']}\n-->"
    )
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# The thread it will be posted in
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def round_counter():
    """codex_round_count.py, imported by path: one reading of GitHub's lists, not two."""
    spec = importlib.util.spec_from_file_location("codex_round_count", ROUND_COUNT_PATH)
    if spec is None or spec.loader is None:
        raise ContractError(f"cannot load {ROUND_COUNT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_marker(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


LIST_ROLES = {"comment": "--comments", "review": "--reviews", "inline": "--review-comments"}


def load_list(role: str, paths: list[Path], seen: set[Path], posts: list[dict[str, Any]]) -> dict[str, str]:
    """Append one fetch of a list to `posts` and return its snapshot, or ContractError when it is incomplete."""
    counter = round_counter()
    snapshot: dict[str, str] = {}
    last = None
    for path in paths:
        if str(path) == "-" or not path.is_file():
            raise ContractError(f"{path} is not a regular file; every page is its own file")
        resolved = path.resolve()
        if resolved in seen:
            raise ContractError(f"{path} is named twice; a page read twice proves nothing")
        seen.add(resolved)
        try:
            data = counter.decode(path.read_bytes(), str(path))
            for item_role, item in counter.role_items(role, data, str(path)):
                ident, key = counter.identity(item, item_role)
                if key is None:
                    raise ContractError(f"{path}: a post with neither an id nor a URL cannot be compared across the "
                                        "two fetches")
                post = counter.to_post(item, item_role, f"{path}: {ident}", len(posts))
                login = counter.login_of(item) or ""
                body = item.get("body") if isinstance(item.get("body"), str) else ""
                snapshot[":".join(key)] = counter.content_of(item)
                posts.append({"when": post.when, "order": len(posts), "kind": post.kind, "ident": ident,
                              "bot": login.casefold() in counter.BOT_LOGINS, "body": body})
        except counter.InputError as exc:
            raise ContractError(str(exc)) from exc
        last = data
    if not counter.proves_last_page(role, last):
        raise ContractError(f"{LIST_ROLES[role]} is not provably complete: pass a `gh api --paginate --slurp` list, "
                            "or a flat page followed by the empty page after it")
    return snapshot


def load_thread(lists: dict[str, tuple[list[Path], list[Path]]]) -> list[dict[str, Any]]:
    """Every post of the thread, in time order, from two fetches of every list that agree.

    `lists` maps a role to its (first fetch, second fetch) pages. The issue
    comments carry the tracking markers and every task reply. A PR's reviews
    and review-thread comments are where a review request is answered or
    posted, so they come together or not at all, and a thread that holds a
    review request without them is refused: its answer would be invisible.
    Every list is fetched once in order and then once more: a post that
    appears or changes between the two stops the render, as in
    codex_round_count.py, so a request that landed between two fetches is
    not missed.
    """
    comments, _ = lists.get("comment", ([], []))
    if not comments:
        raise ContractError("give the thread's issue comments with --comments (a new thread: a file holding [])")
    if bool(lists.get("review", ([], []))[0]) != bool(lists.get("inline", ([], []))[0]):
        raise ContractError("--reviews and --review-comments come together: a PR's review requests and their "
                            "answers live in both")
    roles = [role for role in LIST_ROLES if lists.get(role, ([], []))[0]]
    for role, (first, second) in lists.items():
        if bool(first) != bool(second):
            raise ContractError(f"fetch {LIST_ROLES[role]} twice: give the second fetch with {LIST_ROLES[role]}-after, "
                                "after fetching every list once")
    seen: set[Path] = set()
    posts: list[dict[str, Any]] = []
    firsts = {role: load_list(role, lists[role][0], seen, posts) for role in roles}
    seconds = {role: load_list(role, lists[role][1], seen, []) for role in roles}
    for role in roles:
        first, second = firsts[role], seconds[role]
        changed = sorted(first.keys() ^ second.keys()) + sorted(
            f"{key} (edited)" for key in first.keys() & second.keys() if first[key] != second[key])
        if changed:
            raise ContractError(f"{LIST_ROLES[role]} changed between its two fetches ({', '.join(changed)}); fetch "
                                "every list again, then each once more")
    if "review" not in roles and any(post["kind"] == "request" for post in posts):
        raise ContractError("this thread holds a Codex review request, whose answer is a PR review: pass the PR's "
                            "reviews (--reviews) and review comments (--review-comments) too")
    # Within one second, people's posts come first, then bot posts that are not
    # review results, then review results -- codex_round_count.py's order, in
    # which a request is never processed after the notice or result answering it.
    return sorted(posts, key=lambda post: (post["when"], round_counter().TIE_RANK.get(post["kind"], 1), post["order"]))


def markers(posts: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, str]]]:
    """Tracking markers in posts by anyone but the bot: a marker Codex quotes back is not a request."""
    found = []
    for post in posts:
        if post["bot"]:
            continue
        for match in MARKER.finditer(post["body"]):
            found.append((post, parse_marker(match.group("fields"))))
    return found


def refuse_unsafe_post(contract: dict[str, Any], posts: list[dict[str, Any]]) -> None:
    """Refused when posting the contract now would break section 6 or 16."""
    task, mode, sha, rnd = contract["task_id"], contract["mode"], contract["target_sha"], contract["round"]
    writes = MODES[mode]["write_policy"] != "none"
    found = markers(posts)
    posted: dict[int, str] = {}
    for post, fields in found:
        if fields.get("task_id") not in (None, task):
            continue  # another task's marker
        if fields.get("task_id") is None or fields.get("mode") is None:
            raise ContractError(f"{post['ident']} carries a tracking marker without task_id or mode; it may be "
                                "this task's -- fix it before posting anything")
        if fields["mode"] != mode:
            continue
        try:
            posted_round = int(fields.get("round", ""))
        except ValueError as exc:
            raise ContractError(f"{post['ident']}: marker for {task} {mode} has no integer round") from exc
        posted_sha = fields.get("target_sha", "")
        if posted_round == rnd and posted_sha == sha:
            raise Refused(f"{task} {mode} round {rnd} at {sha[:10]} was already posted ({post['ident']}); "
                          "section 6 forbids posting it again")
        if posted_round == rnd:
            raise Refused(f"{task} {mode} round {rnd} was already posted at {posted_sha[:10] or '?'} "
                          f"({post['ident']}); the next request is round {rnd + 1}")
        if not writes and posted_sha != sha:
            raise Refused(f"{task} {mode} round {posted_round} read {posted_sha[:10] or '?'}; a read-only correction "
                          "round stays on that SHA (section 16) -- a moved base is a new task")
        posted.setdefault(posted_round, posted_sha)
    expected = max(posted, default=0) + 1
    if rnd != expected:
        raise Refused(f"{task} {mode} is at round {expected - 1}; round {rnd} would skip a round -- post round "
                      f"{expected}")

    # Section 16: never the same mode twice for one SHA. Its one task-mode
    # exception is the next round of the same read-only task.
    for post, fields in found:
        if fields.get("mode") != mode or fields.get("target_sha") != sha:
            continue
        if fields.get("task_id") == task and not writes:
            continue
        raise Refused(f"a {mode} request at {sha[:10]} is already in this thread ({post['ident']}, task "
                      f"{fields.get('task_id')}); section 16 never triggers one mode twice for one SHA"
                      + (" -- the next round targets the commit the last one produced" if writes else ""))
    refuse_while_active(posts)


def request_kind(post: dict[str, Any]) -> str | None:
    """What a post asks Codex for: a task (it carries a marker), a review, a merge, or nothing."""
    if post["bot"]:
        return None
    if MARKER.search(post["body"]):
        return "task"
    if post["kind"] == "request":
        return "review"
    if MERGE_REQUEST.search(unicodedata.normalize("NFKC", post["body"])):
        return "merge"
    return None


# Section 17: a task is answered by its final report, a merge by the merge
# commit it reports. A status line ("running the tests now", "preparing the
# merge") is a reply with content but answers nothing; the run is still going.
REPORT_SUMMARY = re.compile(r"^[\s#*_>-]*summary\b", re.IGNORECASE | re.MULTILINE)
REPORT_TESTS = re.compile(r"^[\s#*_>-]*(?:testing|tests?(?:\s+performed)?)\b", re.IGNORECASE | re.MULTILINE)
MERGE_REPORT = re.compile(r"\bmerged\b.*?\b[0-9a-f]{7,40}\b|\b[0-9a-f]{7,40}\b.*?\bmerged\b", re.IGNORECASE | re.DOTALL)


def final_for(body: str) -> tuple[str, ...]:
    """The request kinds a bot post with content is a final answer to."""
    kinds = []
    if REPORT_SUMMARY.search(body) and REPORT_TESTS.search(body):
        kinds.append("task")
    if MERGE_REPORT.search(body):
        kinds.append("merge")
    return tuple(kinds)


def answers(post: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    """Which open requests a bot post can answer, most likely first.

    A final report answers a task, a reported merge commit a merge. A review
    result answers a review; a task report that quotes a finding's badge or a
    reviewed commit reads as a result too, so a result that is also a final
    report answers a task or a merge when no review is open. A notice answers
    whatever is oldest. An "On it", a task link or a status line answers nothing.
    """
    kind = post["kind"]
    if kind in ("quota", "error"):
        return (("task", "review", "merge"),)
    if kind == "result":
        return (("review",), final_for(post["body"]))
    if kind == "unknown":
        return (final_for(post["body"]),)
    return ()


def refuse_while_active(posts: list[dict[str, Any]]) -> None:
    """Refused while any Codex request in the thread is unanswered, or the newest task ended in a notice.

    `posts` is in time order, so `pending` is too, whichever list each request
    came from: every answer goes to the oldest open request it can answer, as
    codex_round_count.py matches them.
    """
    pending: list[tuple[str, dict[str, Any]]] = []
    outcome: dict[int, str] = {}
    newest_task = None
    for post in posts:
        kind = request_kind(post)
        if kind is not None:
            pending.append((kind, post))
            if kind == "task":
                newest_task = post
            continue
        if not post["bot"]:
            continue
        for kinds in answers(post):
            index = next((i for i, (open_kind, _) in enumerate(pending) if open_kind in kinds), None)
            if index is not None:
                open_kind, request = pending.pop(index)
                if open_kind == "task":
                    outcome[request["order"]] = "notice" if post["kind"] in ("quota", "error") else "answered"
                break
    if newest_task is not None and outcome.get(newest_task["order"]) == "notice":
        raise Refused(f"Codex answered the newest task request ({newest_task['ident']}) with a usage-limit or error "
                      "notice; section 17: do not repost -- set the task BLOCKED and report it to the repository owner")
    if pending:
        kind, request = pending[0]
        raise Refused(f"the {kind} request {request['ident']} has no Codex answer yet (an acknowledgement or a "
                      "task link is not one); section 16 allows one active request per thread -- wait for it, and "
                      "after 30 minutes with nothing treat it as stalled (section 17)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_contract(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))  # JSON is YAML
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", type=Path, default=POLICY_PATH, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    checking = sub.add_parser("validate", help="check a contract and print it normalized")
    checking.add_argument("contract", type=Path)
    rendering = sub.add_parser("render", help="print the comment to post, or refuse")
    rendering.add_argument("contract", type=Path)
    rendering.add_argument("--comments", type=Path, action="append", default=[], metavar="FILE",
                           help="the thread's issue comments, one file per page; repeatable")
    rendering.add_argument("--reviews", type=Path, action="append", default=[], metavar="FILE",
                           help="on a PR: its reviews, one file per page; repeatable")
    rendering.add_argument("--review-comments", type=Path, action="append", default=[], metavar="FILE",
                           help="on a PR: its review-thread comments, one file per page; repeatable")
    for name in ("comments", "reviews", "review-comments"):
        rendering.add_argument(f"--{name}-after", type=Path, action="append", default=[], metavar="FILE",
                               help=f"the second fetch of --{name}, made after every list was fetched once")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        contract = validate(load_contract(args.contract), round_caps(args.policy))
        if args.command == "validate":
            print(yaml.safe_dump(contract, sort_keys=False, allow_unicode=True).rstrip())
            return 0
        lists = {
            "comment": (args.comments, args.comments_after),
            "review": (args.reviews, args.reviews_after),
            "inline": (args.review_comments, args.review_comments_after),
        }
        refuse_unsafe_post(contract, load_thread(lists))
        sys.stdout.write(render(contract))
        return 0
    except Refused as exc:
        print(f"codex_task: refused: {exc}", file=sys.stderr)
        return 1
    except ContractError as exc:
        print(f"codex_task: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
