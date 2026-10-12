#!/usr/bin/env python3
"""Report, and optionally set, the branch protection `docs/claude-to-codex.md` §20 requires.

    CI_ADMIN_TOKEN=... python3 scripts/protect_branches.py --owner vohoailinh90
    CI_ADMIN_TOKEN=... python3 scripts/protect_branches.py --owner vohoailinh90 --apply

§20 permits merging a pull request without waiting for a human only where the
base branch requires branches to be up to date before merging, AND the identity
doing the merge cannot bypass that rule. Both halves are settings, not code, so
nothing in this repository can assert them -- and the GitHub MCP toolset a
Claude session runs with exposes no branch-protection endpoint at all, which is
why this exists as a script the operator runs rather than something the session
does for itself.

Two fields carry the whole requirement:

    required_status_checks.strict = true   the "up to date" half
    enforce_admins               = true    the "cannot bypass" half

`strict` alone is not enough, and neither is a protection rule in general:
`strict` has no effect unless at least one status check is required, because
"up to date with respect to nothing" is vacuous. So the contexts below are part
of the requirement, not decoration.

**This deliberately does not require pull-request reviews.** Adding
`required_pull_request_reviews` would make §20's autonomous merge impossible --
no approval can arrive without a human, which is the thing §20 exists to avoid
waiting for. Protection here means "CI passed, against the current base, and
nobody skipped it", not "somebody signed off".

Reporting is the default and writing needs `--apply`, the opposite of
`sync_ci_runner.py`'s default, because enabling `enforce_admins` stops the
repository owner pushing to `main` directly. That is the intended effect and it
should not happen because somebody forgot a flag.

The token is read from the environment only, never from a flag: an argument is
visible to every process on the box through `ps`. It needs `administration:write`
on each repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
TIMEOUT = 30

# The check names each repository actually produced on the pull requests merged
# on 2026-09-19, read off the runs themselves rather than off the workflow
# files -- a job's `name:` and the context branch protection matches are not
# always the same string, and guessing wrong requires a check that never
# arrives, which blocks every pull request in the repository.
#
# A context listed here that stops running blocks merges until it is removed,
# so this table is a live dependency, not a snapshot. `--apply` adds anything
# missing from a branch; it does NOT remove a required check the table does not
# name, because that check is as likely to be a control somebody added on
# purpose as a leftover from a rename. `--drop-unlisted` is the opt-in that
# clears them, and it is the only scripted way out of the rename deadlock.
REQUIRED_CHECKS: dict[str, tuple[str, ...]] = {
    "claude-agent-routing-template": ("build",),
    "Excel-to-planner": ("guard", "test (3.11)", "test (3.12)", "test (3.13)"),
    "office_translator": ("guard", "checks"),
    "Email_Bridge": ("guard", "safety-invariants", "regression"),
    "Shipping_Inspection": ("guard", "regression"),
    "Customer-tcd-sync": ("guard", "test"),
}

# Already protected when this was written, with settings this script has never
# been able to read. Reported, never written, unless named explicitly: their
# rules were set by hand and overwriting them blind would be a worse outcome
# than leaving a gap this report makes visible.
ALREADY_PROTECTED = ("trading-dashboard", "DocumentAIEditor")


def request(url: str, token: str, method: str = "GET", body: dict | None = None):
    """Return (status, parsed-json-or-None). Never raises on an HTTP error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, (json.loads(raw) if raw else None)
        except json.JSONDecodeError:
            return exc.code, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  {method} {url} -> {exc}", file=sys.stderr)
        return 0, None


def desired_protection(contexts: tuple[str, ...] | list[str]) -> dict:
    """The exact payload §20 needs, and nothing beyond it.

    `required_pull_request_reviews` and `restrictions` are explicitly null
    rather than omitted: the REST API treats a missing key on this endpoint as
    "clear it", so writing them out states the intent instead of relying on
    that. Reviews stay off for the reason in the module docstring.
    """
    return {
        "required_status_checks": {"strict": True, "contexts": list(contexts)},
        "enforce_admins": True,
        "required_pull_request_reviews": None,
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


def configured_contexts(protection: dict | None) -> list[str]:
    """The checks a branch actually requires, in either spelling GitHub uses."""
    checks = (protection or {}).get("required_status_checks")
    if not isinstance(checks, dict):
        return []
    named = checks.get("contexts") or [c.get("context") for c in (checks.get("checks") or [])]
    return sorted(c for c in named if c)


def context_drift(protection: dict | None,
                  expected: tuple[str, ...] | list[str] | None) -> tuple[list[str], list[str]]:
    """(still required but not in the table, in the table but not required).

    Kept apart from `shortfalls` because they answer different questions. A
    branch requiring a renamed check still satisfies §20 -- it is strict, it
    binds admins, and it requires something -- while being deadlocked, because
    a context whose job no longer runs never arrives and every pull request
    sits pending forever. Folding this into the §20 verdict would make that
    verdict say something §20 does not.

    `expected` of None means there is no list to compare against, so nothing is
    reported rather than every configured context being called stale.
    """
    if expected is None:
        return [], []
    have = set(configured_contexts(protection))
    want = set(expected)
    return sorted(have - want), sorted(want - have)


def branch_exists(owner: str, repo: str, branch: str, token: str) -> bool | None:
    """True, False, or None when the answer could not be established.

    The protection endpoint answers 404 both for "this branch has no
    protection" and for "no such repository or branch, or none you can see".
    Only this second call tells them apart.
    """
    status, _ = request(f"{API}/repos/{owner}/{repo}/branches/{branch}", token)
    if status == 200:
        return True
    if status == 404:
        return False
    return None


def shortfalls(protection: dict | None) -> list[str]:
    """Why this protection does not satisfy §20. Empty means it does.

    Written against the shape GitHub returns for GET .../protection, which
    nests both answers one level down (`enforce_admins.enabled`, not
    `enforce_admins`) -- reading the PUT payload's shape here would report
    every correctly protected branch as unprotected.
    """
    if not protection:
        return ["no branch protection at all"]
    checks = protection.get("required_status_checks")
    if not isinstance(checks, dict):
        return ["no required status checks, so there is nothing to be up to date with"]
    reasons = []
    if not checks.get("strict"):
        reasons.append("required status checks are not strict: a branch behind its base can merge")
    if not configured_contexts(protection):
        reasons.append("strict is set but no check is required, which makes it vacuous")
    admins = protection.get("enforce_admins")
    enabled = admins.get("enabled") if isinstance(admins, dict) else bool(admins)
    if not enabled:
        reasons.append("administrators are exempt, so the merging identity can bypass the rule")
    return reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--repo", action="append", default=[], metavar="NAME",
                        help="repeatable; defaults to every repository in REQUIRED_CHECKS")
    parser.add_argument("--apply", action="store_true",
                        help="write the protection. Without this, report only.")
    parser.add_argument("--drop-unlisted", action="store_true",
                        help="also remove required checks REQUIRED_CHECKS does not name. "
                             "Needed to clear a context left behind by a renamed job, which "
                             "blocks every pull request; it will equally remove a control "
                             "somebody added on purpose, because nothing here can tell them apart.")
    args = parser.parse_args(argv)

    token = os.environ.get("CI_ADMIN_TOKEN", "")
    if not token:
        print("CI_ADMIN_TOKEN is not set; refusing to run", file=sys.stderr)
        print("It needs administration:write on each repository.", file=sys.stderr)
        return 1

    repos = args.repo or list(REQUIRED_CHECKS)
    failures = 0
    for repo in repos:
        url = f"{API}/repos/{args.owner}/{repo}/branches/{args.branch}/protection"
        status, payload = request(url, token)
        if status == 403:
            print(f"{repo}: FORBIDDEN -- the token lacks administration rights, "
                  f"or this plan cannot protect a private repository")
            failures += 1
            continue
        if status == 404:
            # Reading this as "unprotected" without checking would describe a
            # mistyped --repo or --branch, or one this token cannot see, as
            # simply lacking protection -- in the mode whose entire job is
            # answering whether §20 holds, while counting no failure and
            # exiting zero.
            exists = branch_exists(args.owner, repo, args.branch, token)
            if exists is not True:
                detail = ("no such repository or branch, or none this token can see"
                          if exists is False else "could not confirm it exists")
                print(f"{repo}: cannot read {args.branch} -- {detail}")
                failures += 1
                continue
        elif status != 200:
            print(f"{repo}: could not read protection (HTTP {status})")
            failures += 1
            continue

        current = payload if status == 200 else None
        expected = REQUIRED_CHECKS.get(repo)
        before = shortfalls(current)
        stale, missing = context_drift(current, expected)
        # `--drop-unlisted` has to survive this return, or the one case it
        # exists for -- a branch requiring everything the table names PLUS a
        # context left behind by a rename -- short-circuits here and the flag
        # can never clear anything.
        if not before and not missing and not (stale and args.drop_unlisted):
            print(f"{repo}: already satisfies §20")
            if stale:
                # Not an error, and not this script's to remove. See the note
                # on --drop-unlisted: a context the table does not name is as
                # likely to be a control somebody added on purpose as one left
                # behind by a rename, and nothing here can tell them apart.
                print(f"    also requires {', '.join(stale)}, which the table does not name -- kept")
                print(f"    if one of those no longer runs it blocks every pull request here; "
                      f"--drop-unlisted removes them")
            continue
        if before:
            print(f"{repo}: {'; '.join(before)}")
        else:
            # Satisfies §20 and is still short of the table. Returning
            # "already satisfies" for this case skipped the write, so the one
            # tool meant to reconcile a branch reported success and changed
            # nothing -- while the comment above REQUIRED_CHECKS promised the
            # opposite.
            print(f"{repo}: satisfies §20, but its required checks have drifted from the table")
        if stale:
            print(f"    required, not in the table: {', '.join(stale)} "
                  f"-- kept unless --drop-unlisted")
        if missing:
            print(f"    in the table, not required: {', '.join(missing)}")

        if expected is None:
            print(f"    no required-check list for {repo}; add one to REQUIRED_CHECKS first")
            failures += 1
            continue

        # Union, not replacement. The PUT sends the whole contexts array, so
        # sending only the table's list deletes every check the table does not
        # name -- including a security scan or deploy gate somebody added
        # deliberately. Preserving them is the safe default precisely because
        # the script CANNOT tell a deliberate extra from a renamed leftover:
        # both are simply "required, and not in the table". Removal is real
        # work with real consequences, so it is opt-in.
        target = (sorted(expected) if args.drop_unlisted
                  else sorted(set(expected) | set(configured_contexts(current))))
        if not args.apply:
            print(f"    would require: {', '.join(target)}  (re-run with --apply)")
            continue

        status, payload = request(url, token, method="PUT",
                                  body=desired_protection(target))
        if status != 200:
            message = (payload or {}).get("message", "")
            print(f"    could not protect (HTTP {status}) {message}", file=sys.stderr)
            failures += 1
            continue
        after = shortfalls(payload)
        written = configured_contexts(payload)
        if written != target:
            # Compared against `target`, not against the table: preserving an
            # unlisted context is the intended outcome, so checking against
            # REQUIRED_CHECKS would report every successful preserving write
            # as a failure.
            after = after + [f"required checks are now {', '.join(written) or '(none)'}, "
                             f"not {', '.join(target)}"]
        if after:
            # Written, and still short. GitHub accepted the call and produced
            # something other than what was asked for, so reporting success
            # from the 200 alone would certify a gap that is still open.
            print(f"    WROTE, BUT STILL SHORT: {'; '.join(after)}", file=sys.stderr)
            failures += 1
            continue
        print(f"    protected: strict checks {', '.join(target)}; admins included")

    for repo in ALREADY_PROTECTED:
        if repo in repos:
            continue
        print(f"{repo}: not in this run; it already had protection of unknown shape. "
              f"Check it with --repo {repo}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
