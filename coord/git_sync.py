#!/usr/bin/env python3
"""Link a Team Contract room to GitHub and publish committed task work."""

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))  # the coord folder
from tc_http import urlopen  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
REPOSITORY = r"[a-zA-Z0-9][a-zA-Z0-9-]{0,38}/[a-zA-Z0-9_.-]{1,100}"
CLI = "python3 coord/git_sync.py"


def notice(message: str) -> None:
    print(f"[Team Contract] {message}", flush=True)


def command(*args: str) -> str:
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        run = subprocess.run(args, cwd=PROJECT, capture_output=True, text=True, env=env, timeout=90)
    except FileNotFoundError:
        name = "GitHub CLI (gh)" if args[0] == "gh" else args[0]
        raise ValueError(f"Install {name}, then try again.") from None
    except subprocess.TimeoutExpired:
        raise ValueError(f"{args[0]} {args[1]} timed out. Check the remote before retrying.") from None
    if run.returncode:
        detail = re.sub(r"https?://[^\s/]+@", "https://[redacted]@", run.stderr.strip())
        if args[0] == "gh" and "gh auth login" in detail:
            raise ValueError("Connect your GitHub account with gh auth login, then try again.")
        raise ValueError(detail or f"{args[0]} {args[1]} failed.")
    return run.stdout.strip()


def github_repository(remote: str) -> str:
    match = re.fullmatch(
        rf"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)({REPOSITORY})/?",
        remote,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("Set origin to a github.com HTTPS or SSH remote.")
    repo = match[1].removesuffix(".git").lower()
    if not re.fullmatch(REPOSITORY, repo) or repo.split("/")[1] in {".", ".."}:
        raise ValueError("The origin repository name is invalid.")
    return repo


def origin_repository() -> str:
    if "origin" not in command("git", "remote").splitlines():
        raise ValueError(
            "This project is not connected to a GitHub repository yet. The room owner connects it "
            f"once in a terminal: {CLI} link for an existing repository, or {CLI} create OWNER/REPO "
            "for a new one. See the setup guide on the room dashboard."
        )
    remotes = command("git", "remote", "get-url", "--all", "origin").splitlines()
    push_remotes = command("git", "remote", "get-url", "--push", "--all", "origin").splitlines()
    if len(remotes) != 1 or len(push_remotes) != 1:
        raise ValueError("Origin must have one fetch URL and one push URL.")
    repo = github_repository(remotes[0])
    if github_repository(push_remotes[0]) != repo:
        raise ValueError("Origin fetch and push URLs must point to the same GitHub repository.")
    return repo


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(me: dict, method: str, path: str, body: dict | None = None, token: str = "", timeout: int = 15) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Contract-Token"] = token
    url = me["server"].rstrip("/") + path
    if token:
        target = urllib.parse.urlsplit(url)
        if target.scheme != "https" and target.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS to link a room on a remote server.")
    request = urllib.request.Request(
        url, method=method, headers=headers,
        data=json.dumps({"room": me["room"], **body}).encode() if body is not None else None,
    )
    try:
        with urlopen(request, timeout=timeout, handlers=(NoRedirect(),)) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(f"Team Contract request failed (HTTP {error.code}): {error.read().decode()[:300]}") from None
    except urllib.error.URLError as error:
        raise ValueError(f"Could not reach Team Contract: {error.reason}") from None


def approval_token() -> str:
    if not sys.stdin.isatty():
        raise ValueError("Run this command in a terminal to enter the team key.")
    token = getpass.getpass("Team key for this room: ")
    if not token:
        raise ValueError("Enter the room's team key (set on the dashboard) to link the room.")
    return token


def linked_repository(me: dict) -> dict | None:
    query = urllib.parse.urlencode({"room": me["room"]})
    return api(me, "GET", f"/git?{query}")["repository"]


def link(me: dict, repo: str, token: str) -> None:
    info = json.loads(command("gh", "repo", "view", f"github.com/{repo}", "--json", "defaultBranchRef"))
    branch = info.get("defaultBranchRef")
    if not branch:
        raise ValueError("Create an initial commit on GitHub before linking this repository.")
    base = branch["name"]
    api(me, "PUT", "/git", {"repository": repo, "base_branch": base}, token)
    notice(f"Room {me['room']} is connected to {repo}. Reviewed work will go into {base}.")
    print(f"  Next: run {CLI} start my-task to begin a task.")


def create_repository(me: dict, repo: str) -> None:
    if not re.fullmatch(REPOSITORY, repo) or repo.split("/")[1] in {".", ".."}:
        raise ValueError("Give the new private repository as OWNER/REPO.")
    if "origin" in command("git", "remote").splitlines():
        raise ValueError("This checkout already has an origin. Use link for an existing repo.")
    if linked_repository(me):
        raise ValueError("This room already has a linked repository.")
    branch = command("git", "branch", "--show-current")
    if not branch:
        raise ValueError("Switch to a named branch before creating a repository.")
    command("git", "rev-parse", "--verify", "HEAD")
    token = approval_token()
    query = urllib.parse.urlencode({"room": me["room"]})
    api(me, "GET", f"/git/link-access?{query}", token=token)
    command("gh", "repo", "create", repo, "--private", "--source", ".", "--remote", "origin", "--push")
    notice(f"Created private repository https://github.com/{repo}.")
    link(me, origin_repository(), token)


def edited_names(status_lines: list[str], limit: int = 3) -> str:
    """File names from `git status --porcelain` lines, for a short notice."""
    # Each line is two status letters, then the path. command() trims the
    # output, so the first line may have lost its leading space.
    names = ", ".join(line[2:].strip() for line in status_lines[:limit])
    extra = len(status_lines) - limit
    return names + (f" and {extra} more" if extra > 0 else "")


def open_pr(repo: str, branch: str, base: str) -> dict | None:
    if branch == base:
        return None
    matches = json.loads(command(
        "gh", "pr", "list", "--repo", f"github.com/{repo}", "--head", branch, "--base", base,
        "--state", "open", "--json", "number,url,isCrossRepository,isDraft,reviewDecision,latestReviews",
    ))
    return next((pr for pr in matches if not pr["isCrossRepository"]), None)


def publish(repo: str, branch: str, base: str, ahead: int, title: str | None, ready: bool = False) -> dict:
    if branch == base:
        raise ValueError(f"Switch to a task branch before publishing. {base} is the review target.")
    if ahead == 0:
        unsaved = command("git", "status", "--porcelain").splitlines()
        hint = f" You have uncommitted edits ({edited_names(unsaved)})." if unsaved else ""
        raise ValueError(f"There are no new commits to share.{hint} Commit the changes you want to publish, then try again.")
    pr = open_pr(repo, branch, base)
    unsaved = command("git", "status", "--porcelain").splitlines()
    if unsaved:
        notice(f"Not shared, because they are not committed: {edited_names(unsaved)}. "
               "This push includes your saved commits only.")
    try:
        command(
            "git", "push", "--set-upstream", "--no-follow-tags", "--recurse-submodules=no", "origin",
            f"refs/heads/{branch}:refs/heads/{branch}",
        )
    except ValueError as error:
        if any(reason in str(error) for reason in ("non-fast-forward", "(fetch first)")):
            raise ValueError(
                f"GitHub already has commits on {branch} that are missing from your copy. "
                f"Run git fetch origin, then compare your branch with origin/{branch} before publishing again. "
                f"If it belongs to someone else's task, rename your local branch to a unique name."
            ) from None
        raise
    notice(f"Your commits on {branch} are on GitHub.")
    if pr is None:
        title = title or command("git", "log", "-1", "--format=%s") or branch
        try:
            url = command(
                "gh", "pr", "create", "--repo", f"github.com/{repo}", "--base", base, "--head", branch,
                *([] if ready else ["--draft"]), f"--title={title}", "--body", "Task branch shared with Team Contract.",
            )
            return {"number": int(url.rstrip("/").rsplit("/", 1)[1]), "url": url, "isDraft": not ready}
        except ValueError as error:
            # Another publish may have created the PR between list and create.
            pr = open_pr(repo, branch, base)
            if pr is None:
                raise ValueError(f"Your commits are on GitHub. Run publish again to open the pull request. {error}") from error
    elif has_approval(pr):
        # A push may dismiss a previous approval. Read the current review state.
        pr = open_pr(repo, branch, base)
        if pr is None:
            raise ValueError("The pull request closed during the push. Check it on GitHub before publishing again.")
    if ready and pr.get("isDraft"):
        command("gh", "pr", "ready", str(pr["number"]), "--repo", f"github.com/{repo}")
        pr = {**pr, "isDraft": False}
    return pr


SHARE_DIFF_LIMIT = 12000  # characters of the change sent to the contract check


def contract_findings(me: dict, branch: str, ref: str) -> list[dict]:
    """Ask the server whether the commits about to be shared break a team decision.

    Returns the decisions that the code breaks. Returns nothing when the code
    fits, and also when the check cannot run: sharing must not depend on it.
    Sends the changed lines (up to SHARE_DIFF_LIMIT characters) to the server.
    Set "check_code": false in coord/me.json, or pass --no-check, to send nothing.
    """
    # Team Contract's own setup files are not project code.
    scope = ("--", ".", ":(exclude)coord", ":(exclude).claude", ":(exclude).mcp.json")
    files = command("git", "diff", "--name-only", f"{ref}...HEAD", *scope).splitlines()
    diff = command("git", "diff", "--no-color", "--unified=2", f"{ref}...HEAD", *scope)
    if not diff:
        return []
    try:
        reply = api(me, "POST", "/shares/check", {
            "author": me["name"], "branch": branch, "files": files[:200], "diff": diff[:SHARE_DIFF_LIMIT],
        }, timeout=45)
    except ValueError as error:
        notice(f"The contract check could not run, so your code was not checked. {str(error)[:160]}")
        return []
    check = str(reply.get("check", ""))
    if check.startswith("skipped"):
        notice(f"The contract check did not run ({check[9:]}), so your code was not checked.")
    elif check == "checked" and not reply.get("findings"):
        cut = " (the first part of a large change)" if len(diff) > SHARE_DIFF_LIMIT else ""
        notice(f"Contract check completed{cut}: no contradictions detected in the reviewed code.")
    return reply.get("findings") or []


def findings_text(findings: list[dict]) -> str:
    return "\n".join(
        f"  - {item['file']}: {item['explanation']}\n"
        f"    Team decision (by {item['decided_by']}): {item['decision']}"
        for item in findings
    )


def linked_base(me: dict, repo: str) -> str:
    linked = linked_repository(me)
    if not linked:
        raise ValueError(f"This room is not connected to GitHub. Ask the room owner to run {CLI} link.")
    if linked["repository"] != repo:
        raise ValueError("Origin does not match the room's linked repository.")
    base = linked["base_branch"]
    command("git", "check-ref-format", f"refs/heads/{base}")
    return base


def fetch_base(base: str) -> str:
    ref = f"refs/remotes/origin/{base}"
    command(
        "git", "fetch", "--no-tags", "--no-recurse-submodules", "origin",
        f"+refs/heads/{base}:{ref}",
    )
    return ref


def start_task(repo: str, base: str, branch: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,249}", branch):
        raise ValueError("Use a branch name like todo-api or ali/todo-api.")
    if branch == base:
        raise ValueError(f"Choose a new name. {base} is the shared branch.")
    command("git", "check-ref-format", "--branch", branch)
    current = command("git", "branch", "--show-current")
    edits = command("git", "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none")
    if edits and current != base:
        # Edits on another task branch belong to that task.
        raise ValueError(
            f"You have unsaved edits on {current or 'this commit'}. Commit them there first, "
            "or set them aside with git stash -u. Then start the new task."
        )
    if command("git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"):
        raise ValueError(f"{branch} already exists on {repo}. Pick a unique name for this task.")
    ref = fetch_base(base)
    stranded = int(command("git", "rev-list", "--count", f"{ref}..HEAD")) if current == base else 0
    if stranded:
        # Commits were made on the shared branch by mistake. The task takes them
        # along, and the local shared branch goes back to match GitHub. Nothing
        # is lost: the commits now live on the task branch.
        command("git", "switch", "--no-overwrite-ignore", "-c", branch)
        command("git", "branch", "-f", base, ref)
        notice(f"Started {branch}. It includes the {stranded} commit(s) you made on {base}.")
        print(f"  Your local {base} matches GitHub again.")
    else:
        try:
            command("git", "switch", "--no-track", "--no-overwrite-ignore", "-c", branch, ref)
        except ValueError as error:
            if not edits:
                raise
            raise ValueError(
                f"Your unsaved edits touch files that changed on GitHub, so {branch} was not started. "
                f"Set them aside with git stash -u, run {CLI} start {branch} again, "
                "then bring them back with git stash pop."
            ) from error
        notice(f"Started {branch} from the latest shared {base}.")
    if edits:
        print(f"  Your unsaved edits came along to {branch}.")
    print(f"  Next: make and commit your edits, then run {CLI} publish --ready.")


def pull_request_state(pr: dict | None) -> str | None:
    if not pr:
        return None
    if pr.get("isDraft"):
        return "draft"
    if has_approval(pr):
        return "approved"
    decision = pr.get("reviewDecision")
    if decision == "CHANGES_REQUESTED" or (
        not decision and any(review["state"] == "CHANGES_REQUESTED" for review in pr.get("latestReviews") or [])
    ):
        return "changes_requested"
    return "in_review"


def sync(me: dict, repo: str, action: str, title: str | None, ready: bool = False,
         check: bool = True, anyway: bool = False) -> None:
    base = linked_base(me, repo)
    branch = command("git", "branch", "--show-current")
    if not branch:
        raise ValueError("Switch to a named branch before checking or publishing.")
    if action == "publish" and branch == base:
        raise ValueError(f"{base} is the shared branch. Run {CLI} start my-task first. "
                         f"It moves any work you already did on {base} to the new task branch.")
    ref = fetch_base(base)
    behind, ahead = map(int, command(
        "git", "rev-list", "--left-right", "--count", f"{ref}...HEAD",
    ).split())
    if action == "publish":
        findings = contract_findings(me, branch, ref) if check and ahead and me.get("check_code", True) else []
        if findings and not anyway:
            raise ValueError(
                "Not shared. This code breaks a team decision:\n" + findings_text(findings) + "\n"
                "  Change the code to follow the decision, or settle the decision with your team "
                "on the dashboard.\n"
                f"  To share it as it is, run {CLI} publish --anyway."
            )
        if findings:
            notice("Shared although this code breaks a team decision. The dashboard shows it:")
            print(findings_text(findings))
        pr = publish(repo, branch, base, ahead, title, ready)
    else:
        pr = open_pr(repo, branch, base)
    state = pull_request_state(pr)
    print(f"  Your branch: {branch}. Commits ahead of {base}: {ahead}. Behind: {behind}.")
    if pr:
        print(f"  Pull request #{pr['number']}: {pr['url']}")
        if state == "draft":
            print(f"  Next: run {CLI} publish --ready when you're ready for a teammate to review.")
        elif state == "in_review":
            print(f"  Next: ask a teammate to review the changes at {pr['url']}/files")
        elif state == "approved":
            print("  Next: merge on GitHub when the repository's requirements are met.")
        else:
            print("  Next: open the pull request to see the reviewer's feedback.")
    try:
        api(me, "POST", "/git/activity", {
            "author": me["name"], "repository": repo, "base_branch": base,
            "branch": branch, "ahead": ahead, "behind": behind,
            "pr_number": pr["number"] if pr else None, "pr_state": state,
        })
    except ValueError as error:
        raise ValueError(f"The dashboard could not be updated. Run {CLI} status to retry. {error}") from error
    notice("Branch status shared with your room.")


def pull_requests(repo: str, base: str, state: str, fields: str) -> list[dict]:
    return json.loads(command(
        "gh", "pr", "list", "--repo", f"github.com/{repo}", "--base", base,
        "--state", state, "--limit", "50", "--json", fields,
    ))


def has_approval(pr: dict) -> bool:
    decision = pr.get("reviewDecision")
    if decision:
        return decision == "APPROVED"
    # GitHub can omit the aggregate decision on branches without required reviews.
    reviews = {review["state"] for review in (pr.get("latestReviews") or [])}
    return "APPROVED" in reviews and "CHANGES_REQUESTED" not in reviews


def approved_requests(repo: str, base: str) -> dict[int, str]:
    approved = pull_requests(
        repo, base, "open", "number,headRefOid,isDraft,reviewDecision,latestReviews",
    )
    return {
        pr["number"]: pr["headRefOid"] for pr in approved
        if not pr["isDraft"] and has_approval(pr)
    }


def report_merged_branches(me: dict, repo: str, base: str) -> None:
    """Tell the room which task branches were merged, so "until merged" claims on them end. Best effort."""
    try:
        merged = pull_requests(repo, base, "merged", "headRefName,mergedAt")
        newest: dict[str, str] = {}  # a branch name can be merged more than once
        for pr in merged:
            if pr.get("headRefName") and pr.get("mergedAt"):
                newest[pr["headRefName"]] = max(pr["mergedAt"], newest.get(pr["headRefName"], ""))
        if newest:
            api(me, "POST", "/claims/merged", {"author": me["name"], "branches": [
                {"branch": branch, "merged_at": merged_at} for branch, merged_at in list(newest.items())[:50]
            ]})
    except (OSError, ValueError, KeyError, TypeError):
        pass  # claims also run out by themselves


def merged_update(repo: str, base: str, ref: str, any_merge: bool = True) -> tuple[str, list[int]] | None:
    # Match GitHub's merge commits to the fetched branch history. An open PR
    # or an unrelated remote tip is not a pull target. Every merged PR counts
    # by default; any_merge=False explicitly requires an approval review.
    commits = command("git", "rev-list", "--first-parent", f"HEAD..{ref}").splitlines()
    if not commits:
        return None
    merged = pull_requests(repo, base, "merged", "number,mergeCommit,reviewDecision,latestReviews")
    by_commit = {
        pr["mergeCommit"]["oid"]: pr["number"] for pr in merged
        if pr.get("mergeCommit") and (any_merge or has_approval(pr))
    }
    matches = [(commit, by_commit[commit]) for commit in commits if commit in by_commit]
    if not matches:
        return None
    return matches[0][0], [number for _, number in reversed(matches)]


def update_checkout(base: str, target: str) -> str:
    if command("git", "branch", "--show-current") != base:
        return f"Switch to {base} when ready to sync."
    for operation in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer", "BISECT_START"):
        if (PROJECT / command("git", "rev-parse", "--git-path", operation)).exists():
            return "Finish your current Git operation before syncing."
    # New files that Git does not track yet are left alone: Git itself refuses
    # the update if one of them would be overwritten.
    edits = command("git", "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=none").splitlines()
    if edits:
        return (f"Sync paused: you have unsaved edits on {base} ({edited_names(edits)}). "
                f"Run {CLI} start my-task to move them to a task branch.")
    ahead, behind = map(int, command(
        "git", "rev-list", "--left-right", "--count", f"HEAD...{target}",
    ).split())
    if ahead:
        return (f"Sync paused: your local {base} has {ahead} commit(s) that GitHub does not have. "
                f"Run {CLI} start my-task to move them to a task branch.")
    if not behind:
        return f"Local {base} already has this update."
    try:
        command("git", "merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", target)
    except ValueError as error:
        if "untracked working tree files would be overwritten" in str(error):
            return ("Sync paused: the update adds a file with the same name as one of your new files. "
                    "Rename or move your file, then sync again.")
        return f"Sync paused: Git could not update {base}. Nothing was changed. {str(error).splitlines()[0]}"
    return f"Updated local {base} to {target[:12]}."


def left_behind(base: str, ref: str, any_merge: bool) -> str:
    """Say so when the shared branch has newer commits that syncing leaves alone."""
    if command("git", "branch", "--show-current") != base:
        return ""
    count = int(command("git", "rev-list", "--count", f"HEAD..{ref}"))
    if not count:
        return ""
    if any_merge:
        return (f"{base} on GitHub has {count} newer commit(s) that did not come from a merged "
                "pull request, so they were not applied. To get them, run git pull.")
    return (f"{base} on GitHub has {count} newer commit(s) that did not come from an approved "
            f"pull request, so they were not applied. To get them, run git pull, or run "
            f"{CLI} watch --any-merge to also apply merged pull requests that nobody approved.")


def report_base_status(me: dict, repo: str, base: str, ref: str) -> None:
    """After a sync, tell the dashboard that this checkout is up to date. Best effort."""
    try:
        behind, ahead = map(int, command(
            "git", "rev-list", "--left-right", "--count", f"{ref}...HEAD",
        ).split())
        api(me, "POST", "/git/activity", {
            "author": me["name"], "repository": repo, "base_branch": base,
            "branch": base, "ahead": ahead, "behind": behind, "pr_number": None, "pr_state": None,
        })
    except (OSError, ValueError, KeyError):
        pass  # the files are updated; the dashboard catches up on the next status


def watch(me: dict, interval: int, notify_only: bool, once: bool = False, any_merge: bool = True) -> None:
    kind = "merged" if any_merge else "approved"
    if once:
        notice(f"Checking GitHub for {kind} changes…")
    else:
        mode = "show notices only" if notify_only else f"update your clean shared branch after {'a' if any_merge else 'an approved'} merge"
        notice(f"Checking GitHub about every {interval}s to {mode}. Press Ctrl+C to stop.")
    approvals: set[tuple[str, str, int, str]] = set()
    last_status = ""
    connected = False
    while True:
        try:
            repo = origin_repository()
            base = linked_base(me, repo)
            current = {(repo, base, number, head) for number, head in approved_requests(repo, base).items()}
            for _, _, number, _ in sorted(current - approvals):
                notice(f"PR #{number} approved. Waiting for someone to merge it on GitHub. "
                       f"https://github.com/{repo}/pull/{number}")
            approvals = current
            ref = fetch_base(base)
            report_merged_branches(me, repo, base)
            update = merged_update(repo, base, ref, any_merge)
            status = ""
            applied = False
            if update:
                target, numbers = update
                prs = ", ".join(f"#{number}" for number in numbers)
                result = (f"To apply the update, run {CLI} watch --once."
                          if notify_only else update_checkout(base, target))
                applied = result.startswith("Updated")
                status = f"{kind.capitalize()} PRs merged into {base}: {prs}. {result}"
                if applied:
                    report_base_status(me, repo, base, ref)
            if applied or not update:
                # Anything still newer on GitHub is work this command does not apply.
                status = " ".join(part for part in (status, left_behind(base, ref, any_merge)) if part)
            if not connected and not status and not current:
                notice(f"No {kind} updates to apply for {base} in {repo}.")
            connected = True
        except (OSError, ValueError, KeyError) as error:
            if once:
                raise
            status = f"Could not check GitHub. Will retry in about {interval}s. {error}"
            connected = False
        if status and status != last_status:
            notice(status)
        last_status = status
        if once:
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("link", help="link this origin to the room (room owner)")
    create_parser = subparsers.add_parser("create", help="create a private GitHub repo from committed local work and link it")
    create_parser.add_argument("repository", metavar="OWNER/REPO")
    start_parser = subparsers.add_parser("start", help="start a new task from the latest shared branch")
    start_parser.add_argument("branch", help="a unique branch name, for example ali/todo-api")
    subparsers.add_parser("status", help="fetch the base branch and report branch status")
    publish_parser = subparsers.add_parser("publish", help="push committed work and open or reuse its PR")
    publish_parser.add_argument("--title", help="title for a new pull request")
    publish_parser.add_argument("--ready", action="store_true", help="make the pull request ready for review")
    publish_parser.add_argument("--anyway", action="store_true",
                                help="share even if the code breaks a team decision")
    publish_parser.add_argument("--no-check", action="store_true",
                                help="do not send the change to the contract check")
    watch_parser = subparsers.add_parser("watch", help="notify about approved PRs and sync after merge")
    watch_parser.add_argument("--interval", type=int, default=30, help="seconds between checks (minimum 15)")
    watch_parser.add_argument("--notify-only", action="store_true", help="show updates without applying them")
    watch_parser.add_argument("--once", action="store_true", help="check once, apply eligible updates, and exit")
    merge_policy = watch_parser.add_mutually_exclusive_group()
    merge_policy.add_argument("--any-merge", dest="any_merge", action="store_true",
                              help="apply every merged PR (default)")
    merge_policy.add_argument("--approved-only", dest="any_merge", action="store_false",
                              help="only apply merged PRs with an approval review")
    watch_parser.set_defaults(any_merge=True)
    args = parser.parse_args()
    if args.action == "watch" and args.interval < 15:
        parser.error("--interval must be at least 15 seconds")
    try:
        config = PROJECT / "coord/me.json"
        if not config.is_file():
            raise ValueError("Install Team Contract for this project first. See Connect your project in the room dashboard.")
        me = json.loads(config.read_text())
        if command("git", "rev-parse", "--show-toplevel") != str(PROJECT):
            raise ValueError("Install Team Contract at the root of the Git repository.")
        if args.action == "create":
            create_repository(me, args.repository)
        elif args.action == "link":
            link(me, origin_repository(), approval_token())
        elif args.action == "start":
            repo = origin_repository()
            start_task(repo, linked_base(me, repo), args.branch)
        elif args.action == "watch":
            linked_base(me, origin_repository())
            watch(me, args.interval, args.notify_only, args.once, args.any_merge)
        else:
            sync(me, origin_repository(), args.action, getattr(args, "title", None), getattr(args, "ready", False),
                 check=not getattr(args, "no_check", False), anyway=getattr(args, "anyway", False))
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"[Team Contract] {error}")
    except KeyboardInterrupt:
        print()
        notice("Stopped.")


if __name__ == "__main__":
    main()
