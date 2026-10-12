#!/usr/bin/env python3
"""Count the Codex review rounds a pull request has used.

docs/claude-to-codex.md section 16 sets no cap on review rounds per PR (the
repository owner removed the limit of three on 2026-10-12). The count is still
a script, per `deterministic_work` in agent-routing/policy.yaml, because it
still proves the three lists are complete and tells a review from a notice, a
summary comment or the opening run. It no longer gates on a number: what bounds
the loop is that each review command is requested once per head SHA, one request at a
time, after the previous result was validated (section 16). Before the limit was
removed the post a model left out of the count by eye was the round that
reopened the loop it closed, as on vohoailinh90/Shipping_Inspection#194 (see
#196 and #197 there).

What counts: every review result Codex posted -- a review the bot submitted,
or a bot comment carrying a `Reviewed commit:` line, a no-findings verdict or
a P0-P3 badge. Standard, focused and security reviews alike, requested or not:
Codex also runs on comments that never mention it (Shipping_Inspection PR 105),
and that run is as real as any other.

What does not count:
  - the opening run: the PR's first review result when nothing came before it
    but the summary comment -- no request, no other post by anyone -- Codex's own
    review when the PR is opened, or a draft marked ready. Only that one; a
    request racing it makes it count, and an opening run that ended in an
    error or a task reply uses the exemption up -- both err towards counting.
    A request deleted after Codex answered it cannot be seen, so its review
    passes for the opening run; section 16 forbids deleting one;
  - the review-status summary comment: a body that starts with
    <!-- codex-pull-request-review-summary --> and, its About footer set
    aside, has only lines that are blank, the marker, "## Codex Review
    Summary", "This comment shows the latest Codex review activity on this
    pull request." or a table row (starting with "|") -- anything more counts;
  - a usage-limit message or an error or setup notice: a short post (at most
    SHORT_NOTICE characters, the About block aside) recognized by how it
    opens, and carrying no result marker -- never by a phrase somewhere inside
    it, and never a longer post that opens with an error and goes on to report
    a review. The openings, in English, case aside, after any leading
    punctuation or emoji, any run of whitespace between words: usage limit --
    "You've/You have reached|hit|exceeded [your] [Codex] usage|rate limit[s]",
    "Codex usage limit[s] reached|exceeded", "Usage limit[s] reached|exceeded";
    error or setup -- "To use Codex here[,] create a Codex account",
    "Something went wrong", "Codex encountered|ran into an error",
    "An error occurred". Anything else opening a short post counts;
  - a post that is nothing but an "On it" acknowledgement and/or a
    chatgpt.com/codex/tasks/ or chatgpt.com/s/ link -- a task reply with
    content of its own is not one of these;
  - the bot's inline review comments, which belong to its review -- one newer
    than every review in the reviews list means that review is missing from
    it, and the count stops there.
Any other bot post is UNRECOGNIZED. It counts, and the output says so: a new
kind of bot post is a rule question for the repository owner, never a reason
to request one more review.

Requests: any post by someone other than the bot -- an issue comment, a review
or a review-thread comment -- that asks for a review: @codex review (or review
for ..., or security review), in any case and anywhere in its text: Codex
reacts to the mention wherever it appears, quoted in backticks or not. A
mention that starts another task (a FIX on the PR, say) is not a review
request: Codex answers it with a task link and commits, never a review, so as a
request it would stay open forever; like any post, it still ends the opening
run's exemption. A longer handle (@codexfan, @codex-bot) or an address
(x@codex.dev) is another account, not a mention. The PR description is not read, so it must never mention Codex, and a
request is never deleted or edited to drop its mention. Requests decide two
things: whether a review result is the opening run, and the runs still in
progress -- a request nothing has answered yet (no review result, no
usage-limit or error notice) counts as a round, since its run may land after
the lists were fetched or leave only a reaction. An "On it", a task link or an
unrecognized post is no answer; a notice answers the oldest open request, which
can hide a run in progress if the notice belonged to another run -- the price
of never counting a notice. Posts are walked in time order; within one second,
people's posts come first, then bot posts that are not review results, then
review results -- the order in which no tie can exempt a result -- and each
review result is reported against the oldest request still open. Within each of
those three groups, input order cannot change the count. Every doubt resolves
towards counting, the safe direction: over-counting stops one review early,
under-counting reopens the loop.

Input, every list complete (all pages), fetched in this order:
  --reviews FILE          1. the PR's reviews (gh api --paginate --slurp
                          repos/O/R/pulls/N/reviews, or the GitHub MCP
                          pull_request_read get_reviews result)
  --comments FILE         2. the PR's issue comments (.../issues/N/comments,
                          or the MCP get_comments result)
  --review-comments FILE  3. the PR's review-thread comments
                          (.../pulls/N/comments, or the MCP
                          get_review_comments result)
  --reviews-after FILE    4-6. the same three lists fetched once more, in the
  --comments-after FILE        same order, after all three
  --review-comments-after FILE
All six are required. A count without the reviews cannot see Codex's results,
one without the comments cannot see the requests, and one without the review
comments cannot see a request posted in a thread -- whose review would then
pass for the uncounted opening run. The second pass makes the first one
snapshot: a post that appears in any list between its two fetches, or whose
author, time, commit or text was edited between them, stops the
count -- fetch all six again -- so a request older than a result the count sees
was seen too, and a run whose request and result both came while the lists were
fetched is caught. A run that ends after the last fetch still counts through
its request, above; one Codex starts on its own after the last fetch is not
seen. Each option may be repeated, one file per page; a --paginate --slurp list
of pages is flattened. Every fetch is its own file: one file named twice (two
options, or two pages; another spelling of its path or a hard link included)
stops the count, as a second fetch that rereads the first proves nothing.
Every list must show it is complete: a --paginate --slurp list does (gh
fetched every page), an MCP review-thread result does through its pageInfo,
and a flat list does only when the empty page after it is passed too -- a flat
page of any length can have another behind it; the last
file of each option decides. That empty page is the operator's word that
nothing was skipped, not a proof the count can check; --paginate --slurp is the
proof. A review thread must carry its comment count (total_count): one
without it, or holding fewer comments than it, is incomplete too. Every FILE must be a regular file on disk: stdin (-),
/dev/stdin, a pipe, a FIFO or a device stops the count, as none can show which
fetch it holds. A copy (cp) of one file still passes: the script can prove two
paths are two files, not that they came from two fetches -- that part is the
operator's word. Files are read as UTF-8 (BOM or not), or as UTF-16 when they
start with a UTF-16 BOM (PowerShell 5.1 `>` writes that).

Usage:
    python3 scripts/codex_round_count.py --reviews r.json --comments c.json \\
        --review-comments rc.json --reviews-after r2.json \\
        --comments-after c2.json --review-comments-after rc2.json

A login that looks like Codex but is not the bot, or another bot whose post
carries Codex's summary marker, its About footer or a review result marker,
stops the count: Codex's reviews may be posted under it. Timestamps are taken to the whole second.

Exit status: 0 the lists are complete and were counted -- there is no cap, so a
review may be requested if section 16's other rules allow it (a command not yet run on the head,
no request still unanswered); 1 is unused, kept free so it can never be read as
success; 2 the input cannot be counted (or the script failed). Output is ASCII
only, so it prints on any console encoding.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import stat
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

MAX_REVIEW_ROUNDS = None  # no limit (owner's decision, 2026-10-12); policy.yaml's `review: null` is tied to it by a test
BOT = "chatgpt-codex-connector"
BOT_LOGINS = frozenset({BOT, BOT + "[bot]", "app/" + BOT})
SUMMARY_MARKER = "<!-- codex-pull-request-review-summary -->"
DOC = "docs/claude-to-codex.md section 16"
TIE_RANK = {"request": 0, "human": 0, "result": 2}  # every other bot kind: 1
SHORT_NOTICE = 400  # characters, About block aside: a usage-limit or error notice is no longer than this
# Codex stamps a review's inline comments a moment after the review itself: on
# this repository's PR 31 the review was submitted at 15:59:16 and its inline
# comments created at 15:59:17. Two Codex review runs never land this close
# together -- each takes minutes -- so an inline comment within this lag of a
# review is that review's, not a sign of a review missing from the list.
INLINE_LAG = timedelta(seconds=60)
# The bot's standard footer; only this block is set aside when a notice is measured.
ABOUT_BLOCK = re.compile(r"<details>\s*<summary>[^<]*About Codex in GitHub[^<]*</summary>.*?</details>",
                         re.IGNORECASE | re.DOTALL)
# What Codex posts and nobody else does: the summary marker and the About footer.
CODEX_SIGNATURE = re.compile(re.escape(SUMMARY_MARKER) + r"|<summary>[^<]*About Codex in GitHub", re.IGNORECASE)
# The lines a summary comment holds: its marker, headings, the one sentence under
# the heading, and the status table. Anything else in it may be a result.
SUMMARY_LINE = re.compile(r"\s*(?:" + re.escape(SUMMARY_MARKER) + r"|## Codex Review Summary"
                          r"|This comment shows the latest Codex review activity on this pull request\.|\|.*)?\s*")
INLINE_KEYS = ("path", "diff_hunk", "pull_request_review_id")
AFTER = {"review_after": "review", "comment_after": "comment", "inline_after": "inline"}  # the second pass
LISTS = {"review": "reviews", "comment": "issue comments", "inline": "review comments"}

# A review request: review, review for ..., or security review. Other mentions start a task (a FIX, say,
# section 13), which Codex answers with a task, never with a review. The space after the
# mention keeps out longer handles (@codexfan, @codex-review) and the lookbehind an address (x@codex review).
REVIEW_REQUEST = re.compile(r"(?<![\w@])@codex\s+(?:security\s+)?review\b", re.IGNORECASE)
RESULT = re.compile(
    r"reviewed\s+commit"
    r"|did(?:n['\u2019]?t|\s+not)\s+find\s+any\s+major\s+issues"
    r"|\bP[0-3]\s+badge\b"
    r"|shields\.io/badge/P[0-3]\b",
    re.IGNORECASE,
)
REVIEWED_COMMIT = re.compile(r"reviewed\s+commit\W*([0-9a-f]{7,40})", re.IGNORECASE)
# Non-results are matched only at the very start of the bot's post: a phrase
# such as "rate limit" inside a finding must never make a review vanish.
QUOTA = re.compile(
    r"\A\W*(?:you(?:'ve|\u2019ve|\s+have)\s+(?:reached|hit|exceeded)\s+(?:your\s+)?(?:codex\s+)?(?:usage|rate)\s+limits?"
    r"|codex\s+usage\s+limits?\s+(?:reached|exceeded)"
    r"|usage\s+limits?\s+(?:reached|exceeded))",
    re.IGNORECASE,
)
ERROR = re.compile(
    r"\A\W*(?:to\s+use\s+codex\s+here,?\s+create\s+a\s+codex\s+account"
    r"|something\s+went\s+wrong"
    r"|codex\s+(?:encountered|ran\s+into)\s+an\s+error"
    r"|an\s+error\s+occurred)",
    re.IGNORECASE,
)
# A Codex task link: chatgpt.com/codex/tasks/..., or the chatgpt.com/s/... share link
# Codex posts as a bare "View task" (Shipping_Inspection PR 205; the owner ruled it a task link).
TASK_LINK = re.compile(r"\[[^\]]*\]\(https://chatgpt\.com/(?:codex/tasks|s)/[^)\s]*\)"
                       r"|https://chatgpt\.com/(?:codex/tasks|s)/\S*", re.IGNORECASE)
ACKNOWLEDGEMENT = re.compile(r"\b(?:on\s+it|view\s+task)\b", re.IGNORECASE)
TIMESTAMP = re.compile(
    r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$"
)
URL_ID = re.compile(r"#(issuecomment|pullrequestreview|discussion_r)-?(\d+)$")

NOT_COUNTED = {
    "summary": "review-status summary comment",
    "quota": "usage-limit message",
    "error": "error or setup notice, no result",
    "task": "acknowledgement or task link, no review result",
}


class InputError(Exception):
    """The input cannot be counted. Never report rounds left when this is raised."""


class Post(NamedTuple):
    kind: str  # request | human | result | summary | quota | error | task | unknown
    when: datetime
    ident: str
    commit: str | None
    order: int


def login_of(item: dict) -> str | None:
    for key in ("user", "author"):
        value = item.get(key)
        if isinstance(value, dict) and isinstance(value.get("login"), str):
            return value["login"]
        if isinstance(value, str):
            return value
    return None


def identity(item: dict, role: str) -> tuple[str, tuple[str, str] | None]:
    """Return the display id and the dedup key, or None when there is nothing to key on.

    The key keeps the id's namespace: issue comment 777 and review comment 777
    are two posts, and merging them would drop a request from the count.
    """
    for field in ("html_url", "url"):
        url = item.get(field)
        if isinstance(url, str):
            match = URL_ID.search(url)
            if match:
                return "gh:" + match.group(2), (match.group(1), match.group(2))
    value = item.get("id")
    if value is None:
        return "gh:?", None
    return "gh:" + str(value), (role, str(value))


def parse_time(value: object, ident: str) -> datetime:
    match = TIMESTAMP.match(value) if isinstance(value, str) else None
    try:
        if not match:
            raise ValueError("not an ISO-8601 UTC or offset timestamp")
        # A fraction of a second is dropped: ties are decided per whole second, as
        # GitHub stamps posts, so 05.9 and 05 are one second, not two.
        year, month, day, hour, minute, second, _fraction, zone = match.groups()
        tz = timezone.utc
        if zone != "Z":
            sign = 1 if zone[0] == "+" else -1
            tz = timezone(sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), 0, tz)
    except ValueError as exc:
        raise InputError(f"{ident} has no readable timestamp ({value!r}: {exc}); "
                         "the order of posts decides the count") from exc


def classify_bot(role: str, body: str) -> str:
    """Return the kind of a bot post."""
    if role == "inline":
        return "inline"  # part of the bot's review: counted through that review, never on its own
    if role == "review" or RESULT.search(body):
        return "result"
    if body.startswith(SUMMARY_MARKER):
        return "summary" if is_plain_summary(body) else "unknown"
    # A notice is short; a post that opens with an error sentence and goes on to
    # report a review is a review, markers or not.
    if len(ABOUT_BLOCK.sub("", body).strip()) <= SHORT_NOTICE:
        for kind, pattern in (("quota", QUOTA), ("error", ERROR)):
            if pattern.search(body):
                return kind
    if is_task_only(body):
        return "task"
    return "unknown"


def is_plain_summary(body: str) -> bool:
    """The summary comment holding nothing but its heading, status table and About footer."""
    return all(SUMMARY_LINE.fullmatch(line) for line in ABOUT_BLOCK.sub("", body).splitlines())


def is_bot(item: dict, login: str) -> bool:
    folded = login.casefold()
    if folded.endswith("[bot]") or folded.startswith("app/"):
        return True
    return any(isinstance(item.get(key), dict) and (item[key].get("type") == "Bot" or item[key].get("is_bot") is True)
               for key in ("user", "author"))


def is_task_only(body: str) -> bool:
    """An "On it" or a task link and nothing else. A task reply with content counts as a post of its own."""
    if not (TASK_LINK.search(body) or ACKNOWLEDGEMENT.search(body)):
        return False
    return not re.search(r"\w", ACKNOWLEDGEMENT.sub("", TASK_LINK.sub("", body)))


def commit_of(item: dict, body: str) -> str | None:
    commit = item.get("commit_id")
    if not isinstance(commit, str) and isinstance(item.get("commit"), dict):
        commit = item["commit"].get("oid")
    if not isinstance(commit, str):
        match = REVIEWED_COMMIT.search(body)
        commit = match.group(1) if match else None
    return commit[:10] if commit else None


def to_post(item: dict, role: str, ident: str, order: int) -> Post:
    login = login_of(item)
    if login is None:
        raise InputError(f"{ident} has no user.login or author.login; a post nobody can attribute cannot be counted")
    body = item.get("body") if isinstance(item.get("body"), str) else ""
    if login.casefold() in BOT_LOGINS:
        kind = classify_bot(role, body)
    else:
        if "codex" in login.casefold():
            raise InputError(f"{ident}: login {login!r} looks like Codex but is not {BOT}; the count cannot tell "
                             "whether its posts are reviews -- check the login before counting")
        if is_bot(item, login) and (CODEX_SIGNATURE.search(body) or RESULT.search(body)):
            raise InputError(f"{ident}: bot {login!r} posts what Codex posts but is not {BOT}; its reviews would go "
                             "uncounted -- check which login Codex reviews under before counting")
        kind = "request" if REVIEW_REQUEST.search(body) else "human"
    stamp = item.get("submitted_at", item.get("submittedAt", item.get("created_at", item.get("createdAt"))))
    commit = commit_of(item, body) if kind == "result" else None
    return Post(kind, parse_time(stamp, ident), ident, commit, order)


def decode(raw: bytes, source: str) -> object:
    if not raw.strip():
        raise InputError(f"{source} is empty; nothing to count is not the same as zero rounds")
    try:
        if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(f"{source} is neither UTF-8 nor UTF-16 with a BOM ({exc.reason})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{source} is not valid JSON ({exc.msg}, line {exc.lineno}); "
                         "for gh api --paginate add --slurp") from exc


def is_slurped(data: object) -> bool:
    """A list of pages, as gh api --paginate --slurp prints it: every page was fetched."""
    return isinstance(data, list) and bool(data) and all(isinstance(page, list) for page in data)


def as_list(data: object, source: str, what: str) -> list:
    if not isinstance(data, list):
        raise InputError(f"{source}: expected a list of {what}, got {type(data).__name__}")
    if is_slurped(data):
        data = [item for page in data for item in page]
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise InputError(f"{source}: entry {i} is a {type(item).__name__}, not one of the {what}")
    return data


# The fields that decide how a post counts: who wrote it, when, for which commit, and what it says.
CONTENT_FIELDS = ("user", "author", "body", "submitted_at", "submittedAt", "created_at", "createdAt", "commit_id",
                  "commit")


def content_of(item: dict) -> str:
    return json.dumps({field: item.get(field) for field in CONTENT_FIELDS}, sort_keys=True, default=str)


def thread_comments(data: object, source: str) -> list:
    if isinstance(data, dict) and "review_threads" in data:
        threads = as_list(data["review_threads"], source, "review threads")
        items = []
        for i, thread in enumerate(threads):
            if not isinstance(thread.get("comments"), list):
                raise InputError(f"{source}: review thread {i} has no list of comments")
            comments = as_list(thread["comments"], source, "review comments")
            total = thread.get("total_count", thread.get("totalCount"))
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise InputError(f"{source}: review thread {i} has no comment count (total_count) -- the page of "
                                 "threads can be complete while a thread's own comments are not")
            if len(comments) < total:
                raise InputError(f"{source}: review thread {i} holds {len(comments)} of its {total} comments -- "
                                 "fetch the thread in full")
            items.extend(comments)
        return items
    return as_list(data, source, "review comments")


def role_items(role: str, data: object, source: str) -> list[tuple[str, dict]]:
    """Return (role, item) pairs, refusing a file that holds the wrong kind of post."""
    if role in AFTER:
        return [(role, item) for _, item in role_items(AFTER[role], data, source)]
    if role == "inline":
        items = thread_comments(data, source)
        for i, item in enumerate(items):
            url = str(item.get("html_url", item.get("url", "")))
            if not (any(key in item for key in INLINE_KEYS) or "#discussion_r" in url) or "state" in item:
                raise InputError(f"{source}: entry {i} is not one of the review-thread comments -- check which file "
                                 "went to which option")
        return [("inline", item) for item in items]
    what = "issue comments" if role == "comment" else "PR reviews"
    items = as_list(data, source, what)
    for i, item in enumerate(items):
        inline = any(key in item for key in INLINE_KEYS)
        if inline or ("state" in item) != (role != "comment"):
            raise InputError(f"{source}: entry {i} is not one of the {what} -- check which file went to which option")
    return [(role, item) for item in items]


def proves_last_page(role: str, data: object) -> bool:
    """Whether this file shows that no page after it exists.

    gh fetched every page of a --paginate --slurp list; an MCP review-thread
    result says so in pageInfo; a flat list proves nothing unless
    it is empty -- the page after the last one. A flat page of any length can
    have another page behind it, so a flat list is accepted only when an empty
    page follows it.
    """
    if is_slurped(data):
        return True
    if isinstance(data, dict) and "review_threads" in data:
        info = data.get("pageInfo")
        return isinstance(info, dict) and info.get("hasNextPage") is False
    return data == []


def load(sources: list[tuple[str, str]]) -> tuple[list[Post], list[str]]:
    posts: list[Post] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    last_page: dict[str, tuple[str, bool]] = {}
    newest: dict[str, Post] = {}  # the bot's newest review submission and newest inline comment
    passes: dict[str, tuple[dict, dict]] = {base: ({}, {}) for base in LISTS}  # each list's two fetches: key -> content
    order = 0
    for role, source in sources:
        raw = Path(source).read_bytes()
        data = decode(raw, source)
        pairs = role_items(role, data, source)
        last_page[role] = (source, proves_last_page(role, data))
        for item_role, item in pairs:
            order += 1
            base = AFTER.get(item_role, item_role)
            ident, key = identity(item, base)
            if key is None:
                raise InputError(f"{source}: a post with neither an id nor a URL cannot be compared across the two "
                                 "fetches of its list")
            passes[base][item_role in AFTER][key] = content_of(item)
            if item_role in AFTER:
                continue  # the second pass is read only to compare with the first
            if key in seen:
                continue  # the same post passed twice, e.g. the same page given twice
            post = to_post(item, item_role, ident, order)
            seen.add(key)
            if post.kind == "inline" or (post.kind == "result" and item_role == "review"):
                if post.kind not in newest or post.when > newest[post.kind].when:
                    newest[post.kind] = post
            if post.kind != "inline":
                posts.append(post)
    for role, (source, complete) in last_page.items():
        if not complete:
            raise InputError(f"{source} does not show it is the last page -- pass the empty page after it with the "
                             "same option, or use gh api --paginate --slurp or an MCP result with pageInfo")
    for base, (first, second) in passes.items():
        changed = sorted(":".join(key) for key in first.keys() ^ second.keys())
        changed += sorted(":".join(key) + " (edited)" for key in first.keys() & second.keys() if first[key] != second[key])
        if changed:
            raise InputError(f"the {LISTS[base]} changed while the lists were fetched ({', '.join(changed)}) -- fetch "
                             "all six again: the reviews, the issue comments and the review comments, then each once "
                             "more in the same order")
    # Codex posts a review and its inline comments together, within INLINE_LAG.
    # An inline comment newer than that means the reviews were fetched first and
    # the review it belongs to is missing.
    if "inline" in newest and ("result" not in newest
                               or newest["inline"].when > newest["result"].when + INLINE_LAG):
        raise InputError(f"Codex inline comment {newest['inline'].ident} is newer than every Codex review in the "
                         "reviews list, so a review landed between the fetches -- fetch all six again: the reviews, "
                         "the issue comments and the review comments, then each once more in the same order")
    # Within a second: people's posts, then bot posts that are not results, then
    # results -- anything placed before a result only ever makes that result count.
    posts.sort(key=lambda p: (p.when, TIE_RANK.get(p.kind, 1), p.order))
    return posts, warnings


def count(posts: list[Post]) -> tuple[int, int, list[str]]:
    """Return (rounds used, unrecognized posts counted, report lines)."""
    pending: deque[Post] = deque()
    used = unknown = 0
    opening_run_seen = False
    lines = []
    for post in posts:
        if post.kind == "request":
            pending.append(post)
        elif post.kind == "human":
            # Codex also runs on comments that do not mention it, so after anyone
            # has posted, an unrequested review is no longer the opening run.
            opening_run_seen = True
        elif post.kind == "result":
            request = pending.popleft() if pending else None
            # A request that came first is still open here, so request is None
            # only when nothing asked for this run.
            if request is None and not opening_run_seen:
                opening_run_seen = True
                lines.append(f"  not counted: {post.ident} commit {post.commit or '?'} -- the opening run"
                             " (Codex's own review when the PR was opened or marked ready, before any request)")
                continue
            opening_run_seen = True
            used += 1
            answers = f"answers request {request.ident}" if request else "no request pending -- Codex ran on its own, counted"
            lines.append(f"  round {used}: {post.ident} commit {post.commit or '?'} -- {answers}")
        elif post.kind == "unknown":
            # It closes no request: if it is not the answer, the run still in
            # progress counts too. Both counting only over-counts.
            opening_run_seen = True
            used += 1
            unknown += 1
            lines.append(f"  round {used}: {post.ident} -- UNRECOGNIZED bot post, counted; ask the repository owner")
        else:
            if post.kind != "summary":
                opening_run_seen = True  # the opening run happened, even if it produced no review
            note = NOT_COUNTED[post.kind]
            if post.kind in ("quota", "error") and pending:
                # Codex answered the oldest request with a notice instead of a review.
                note += f", answers request {pending.popleft().ident}"
            lines.append(f"  not counted: {post.ident} -- {note}")
    # A request nothing has answered yet -- no result, no notice -- is a run that
    # may still land: one that finishes after the lists were fetched, or a clean
    # review that leaves only a reaction. It counts.
    for request in pending:
        used += 1
        lines.append(f"  round {used}: {request.ident} -- request with no answer yet, counted as a run in progress")
    return used, unknown, lines


def file_identity(path: str) -> object:
    """A file's identity: (device, inode) when it can be stat'ed, else its normalized path, so two spellings of one
    file (relative and absolute, or a hard link) compare equal."""
    try:
        st = os.stat(path)
    except OSError:
        st = None
    if st is not None and st.st_ino:
        return (st.st_dev, st.st_ino)
    return os.path.normcase(os.path.realpath(path))


def parse_args(argv: list[str] | None) -> list[tuple[str, str]]:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reviews", action="append", default=[], metavar="FILE", help="the PR's reviews")
    parser.add_argument("--comments", action="append", default=[], metavar="FILE", help="the PR's issue comments")
    parser.add_argument("--review-comments", action="append", default=[], metavar="FILE",
                        help="the PR's review-thread comments (always required)")
    parser.add_argument("--reviews-after", action="append", default=[], metavar="FILE",
                        help="the PR's reviews fetched again, after all three lists (always required)")
    parser.add_argument("--comments-after", action="append", default=[], metavar="FILE",
                        help="the PR's issue comments fetched again, after --reviews-after (always required)")
    parser.add_argument("--review-comments-after", action="append", default=[], metavar="FILE",
                        help="the PR's review-thread comments fetched again, last (always required)")
    args = parser.parse_args(argv)
    if not (args.reviews and args.comments):
        parser.error("give both --reviews and --comments: a count missing either cannot see every round")
    if not args.review_comments:
        parser.error("give --review-comments too: a request posted in a review thread that the count cannot see "
                     "turns its review into the uncounted opening run")
    if not (args.reviews_after and args.comments_after and args.review_comments_after):
        parser.error("give --reviews-after, --comments-after and --review-comments-after too: the three lists fetched "
                     "again show whether anything was posted while they were fetched")
    sources = ([("review", f) for f in args.reviews] + [("comment", f) for f in args.comments]
               + [("inline", f) for f in args.review_comments] + [("review_after", f) for f in args.reviews_after]
               + [("comment_after", f) for f in args.comments_after]
               + [("inline_after", f) for f in args.review_comments_after])
    if any(source == "-" for _, source in sources):
        parser.error("stdin (-) is not accepted: write every fetch to its own file, as a pipe cannot show which fetch "
                     "it holds")
    seen: dict[object, str] = {}
    for _, source in sources:
        try:
            mode = os.stat(source).st_mode
        except OSError:
            mode = None  # cannot be stat'ed: reading it fails as an input error
        if mode is not None and not stat.S_ISREG(mode):
            parser.error(f"{source!r} is not a regular file (a pipe, /dev/stdin, a FIFO or a device): write every "
                         "fetch to its own file on disk")
        identity = file_identity(source)
        if identity in seen:
            parser.error(f"{source!r} and {seen[identity]!r} are the same file: write every fetch to its own file, "
                         "or the second fetch rereads the first and proves nothing")
        seen[identity] = source
    return sources


def main(argv: list[str] | None = None) -> int:
    sources = parse_args(argv)
    try:
        posts, warnings = load(sources)
        used, unknown, lines = count(posts)
    except (InputError, OSError) as exc:
        print(f"codex_round_count: INPUT ERROR -- {exc}".encode("ascii", "replace").decode("ascii"))
        return 2
    except Exception as exc:  # a bug must read neither as the limit (1) nor as rounds left (0)
        print(f"codex_round_count: INTERNAL ERROR -- {type(exc).__name__}: {exc}".encode("ascii", "replace")
              .decode("ascii"))
        return 2
    report = [f"codex_round_count: {used} review round(s) used, no limit ({DOC})", *lines, *warnings]
    if unknown:
        report.append(f"WARNING: {unknown} unrecognized bot post(s) counted -- a rule question for the repository owner")
    report.append("OK: no review-round limit -- request each review command once per head SHA, with no request still unanswered")
    print("\n".join(report).encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
