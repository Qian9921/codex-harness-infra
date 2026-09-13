"""A small stateless adapter for the V23 GitHub delivery path.

This is intentionally not a scheduler or an approval security boundary. On one
machine, separate `GH_CONFIG_DIR` values provide audit identity separation;
GitHub branch/ruleset protection remains the server-side merge authority.
"""

from __future__ import annotations

try:
    from scripts.runtime import ensure_supported_python
except ModuleNotFoundError:  # Support the documented direct script entrypoint.
    from runtime import ensure_supported_python

ensure_supported_python(__file__)

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class FlowError(RuntimeError):
    """A GitHub delivery precondition did not hold."""


@dataclass(frozen=True)
class ReviewVerdict:
    """The exact SHA and concise independent-review conclusion to publish."""

    reviewed_sha: str
    event: str
    body: str

    def __post_init__(self) -> None:
        if self.event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
            raise FlowError(f"unsupported GitHub review event: {self.event}")
        if not self.reviewed_sha:
            raise FlowError("reviewed_sha is required")


Runner = Callable[[tuple[str, ...], dict[str, str]], subprocess.CompletedProcess[str]]
_AMBIENT_AUTH_VARIABLES = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
)
_GIT_CONFIG_INJECTION_VARIABLES = (
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
)


def _system_runner(
    command: tuple[str, ...], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, text=True, capture_output=True, check=False)


def _github_environment(config_dir: Path) -> dict[str, str]:
    """Select one GH identity without accepting ambient token or askpass overrides."""
    env = os.environ.copy()
    for variable in _AMBIENT_AUTH_VARIABLES:
        env.pop(variable, None)
    for variable in tuple(env):
        if variable in _GIT_CONFIG_INJECTION_VARIABLES or variable.startswith(
            ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
        ):
            env.pop(variable)
    env["GH_CONFIG_DIR"] = str(config_dir)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["NO_COLOR"] = "1"
    env["CLICOLOR"] = "0"
    env.pop("GH_FORCE_TTY", None)
    # The author push uses a prevalidated literal URL, so it does not need
    # repository, global, or system Git configuration. Keeping those sources
    # out prevents a later URL rewrite or scoped credential override.
    env["GIT_CONFIG"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _local_config_environment() -> dict[str, str]:
    """Read raw local remote configuration without inherited injection."""
    env = os.environ.copy()
    env.pop("GH_CONFIG_DIR", None)
    for variable in _AMBIENT_AUTH_VARIABLES:
        env.pop(variable, None)
    for variable in tuple(env):
        if variable in _GIT_CONFIG_INJECTION_VARIABLES or variable.startswith(
            ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
        ):
            env.pop(variable)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


class GHClient:
    """One `gh` identity, isolated by a caller-selected `GH_CONFIG_DIR`."""

    def __init__(self, config_dir: Path, runner: Runner | None = None) -> None:
        self.config_dir = Path(config_dir)
        self._runner = runner or _system_runner

    def _run(self, *parts: str, allow_empty: bool = False) -> str:
        completed = self._runner(tuple(parts), self.environment())
        if completed.returncode:
            detail = completed.stderr.strip() or "GitHub CLI command failed"
            raise FlowError(detail)
        output = completed.stdout.strip()
        if not output and not allow_empty:
            raise FlowError("GitHub CLI returned no data")
        return output

    def environment(self) -> dict[str, str]:
        """Return the isolated environment used for this GitHub identity."""
        return _github_environment(self.config_dir)

    @staticmethod
    def _json(output: str) -> object:
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise FlowError("GitHub CLI returned invalid JSON") from error

    def login(self) -> str:
        value = self._json(self._run("gh", "api", "user"))
        try:
            login = value["login"]  # type: ignore[index]
        except (KeyError, TypeError) as error:
            raise FlowError("GitHub user response has no login") from error
        if not isinstance(login, str) or not login:
            raise FlowError("GitHub user response has invalid login")
        return login

    def current_head(self, repo: str, number: int) -> str:
        value = self._json(self._run("gh", "api", f"repos/{repo}/pulls/{number}"))
        try:
            sha = value["head"]["sha"]  # type: ignore[index]
        except (KeyError, TypeError) as error:
            raise FlowError("pull request response has no head SHA") from error
        if not isinstance(sha, str) or not sha:
            raise FlowError("pull request response has invalid head SHA")
        return sha

    def reviews(self, repo: str, number: int) -> list[dict]:
        reviews: list[dict] = []
        page = 1
        while True:
            value = self._json(
                self._run(
                    "gh", "api", f"repos/{repo}/pulls/{number}/reviews?per_page=100&page={page}"
                )
            )
            if not isinstance(value, list):
                raise FlowError("pull request reviews response is not a list")
            reviews.extend(entry for entry in value if isinstance(entry, dict))
            if len(value) < 100:
                return reviews
            page += 1

    def latest_reviewer_review(self, repo: str, number: int, reviewer: str) -> dict | None:
        matches = [
            (index, review)
            for index, review in enumerate(self.reviews(repo, number))
            if str(review.get("user", {}).get("login", "")).casefold() == reviewer.casefold()
            and str(review.get("state", "")).upper()
            in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
        ]
        if not matches:
            return None
        # GitHub returns reviews in chronological order. Keep API position as a
        # stable tie-breaker, because mocks or older API payloads may omit time.
        return max(matches, key=lambda item: (str(item[1].get("submitted_at", "")), item[0]))[1]

    def has_current_approval(self, repo: str, number: int, reviewer: str, head_sha: str) -> bool:
        review = self.latest_reviewer_review(repo, number, reviewer)
        return bool(
            review and review.get("state") == "APPROVED" and review.get("commit_id") == head_sha
        )

    def unresolved_threads(self, repo: str, number: int) -> list[dict]:
        owner, separator, name = repo.partition("/")
        if not separator or not owner or not name:
            raise FlowError("repo must be OWNER/REPO")
        query = (
            "query($owner:String!, $name:String!, $number:Int!, $cursor:String) {"
            " repository(owner:$owner,name:$name) { pullRequest(number:$number) {"
            " reviewThreads(first:100,after:$cursor) { nodes { isResolved path line }"
            " pageInfo { hasNextPage endCursor } } } } }"
        )
        cursor: str | None = None
        unresolved: list[dict] = []
        while True:
            command = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"number={number}",
            ]
            if cursor:
                command.extend(("-f", f"cursor={cursor}"))
            value = self._json(self._run(*command))
            try:
                threads = value["data"]["repository"]["pullRequest"]["reviewThreads"]  # type: ignore[index]
            except (KeyError, TypeError) as error:
                raise FlowError("review thread response is incomplete") from error
            nodes = threads.get("nodes", []) if isinstance(threads, dict) else []
            unresolved.extend(
                node
                for node in nodes
                if isinstance(node, dict) and not node.get("isResolved", False)
            )
            page = threads.get("pageInfo", {}) if isinstance(threads, dict) else {}
            if not page.get("hasNextPage", False):
                return unresolved
            cursor = page.get("endCursor")
            if not cursor:
                raise FlowError("review thread pagination has no cursor")

    def required_checks_pass(self, repo: str, number: int) -> bool:
        output = self._run(
            "gh",
            "pr",
            "checks",
            str(number),
            "--repo",
            repo,
            "--required",
            "--json",
            "name,state",
            allow_empty=True,
        )
        if not output:
            return True
        try:
            checks = self._json(output)
        except FlowError:
            # Text mode is also accepted by gh; only explicit failing statuses block.
            return not any(
                word in output.casefold() for word in ("fail", "pending", "cancel", "error")
            )
        if not isinstance(checks, list):
            return False
        allowed = {"SUCCESS", "NEUTRAL", "SKIPPED"}
        return all(
            str(check.get("state", "")).upper() in allowed
            for check in checks
            if isinstance(check, dict)
        )

    def submit_review(self, repo: str, number: int, verdict: ReviewVerdict) -> None:
        head = self.current_head(repo, number)
        if head != verdict.reviewed_sha:
            raise FlowError(
                f"stale review: current head changed from {verdict.reviewed_sha} to {head}"
            )
        self._run(
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repo}/pulls/{number}/reviews",
            "-f",
            f"commit_id={verdict.reviewed_sha}",
            "-f",
            f"event={verdict.event}",
            "-f",
            f"body={verdict.body}",
            allow_empty=True,
        )

    def find_or_create_pr(self, repo: str, branch: str, base: str, title: str, body: str) -> int:
        existing = self._json(
            self._run(
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number",
            )
        )
        if isinstance(existing, list) and existing:
            number = existing[0].get("number") if isinstance(existing[0], dict) else None
            if isinstance(number, int):
                return number
        created_url = self._run(
            "gh",
            "pr",
            "create",
            "--repo",
            repo,
            "--head",
            branch,
            "--base",
            base,
            "--title",
            title,
            "--body",
            body,
            "--draft",
        )
        created = self._json(
            self._run("gh", "pr", "view", created_url, "--repo", repo, "--json", "number")
        )
        if not isinstance(created, dict) or not isinstance(created.get("number"), int):
            raise FlowError("created pull request response has no number")
        return created["number"]


class DeliveryFlow:
    """Explicit, idempotent checks around GitHub's own merge protection."""

    def __init__(
        self,
        author: GHClient,
        reviewer: GHClient,
        expected_author: str,
        expected_reviewer: str,
        git_runner: Runner | None = None,
    ) -> None:
        self.author = author
        self.reviewer = reviewer
        self.expected_author = expected_author
        self.expected_reviewer = expected_reviewer
        self._git_runner = git_runner or _system_runner

    def preflight(self) -> tuple[str, str]:
        author, reviewer = self.author.login(), self.reviewer.login()
        if author.casefold() == reviewer.casefold():
            raise FlowError("author and reviewer must use different audit identities")
        if author.casefold() != self.expected_author.casefold():
            raise FlowError("author GitHub identity does not match local configuration")
        if reviewer.casefold() != self.expected_reviewer.casefold():
            raise FlowError("reviewer GitHub identity does not match local configuration")
        return author, reviewer

    def publish_review(self, repo: str, number: int, verdict: ReviewVerdict) -> None:
        self.preflight()
        self.reviewer.submit_review(repo, number, verdict)

    def push_branch(self, workdir: Path, remote: str, refspec: str) -> None:
        """Push through the configured author identity, never an ambient credential."""
        self.preflight()
        if not workdir.is_dir():
            raise FlowError(f"Git worktree does not exist: {workdir}")
        if not remote or not refspec:
            raise FlowError("remote and refspec are required for an author push")
        env = self.author.environment()
        remote_url = self._author_push_url(workdir, remote)
        command = [
            "git",
            "-C",
            str(workdir.resolve()),
            "-c",
            "credential.helper=",
        ]
        for setting in self._author_push_clears(remote_url):
            command.extend(("-c", f"{setting}="))
        command.extend(
            (
                "-c",
                f"{self._author_push_helper(remote_url)}=!gh auth git-credential",
            )
        )
        command.extend(("push", remote_url, refspec))
        completed = self._git_runner(tuple(command), env)
        if completed.returncode:
            detail = completed.stderr.strip() or "Git push failed"
            raise FlowError(detail)

    def _author_push_url(self, workdir: Path, remote: str) -> str:
        """Return a credential-free HTTPS GitHub push URL for the author helper."""
        completed = self._git_runner(
            (
                "git",
                "-C",
                str(workdir.resolve()),
                "config",
                "--local",
                "--no-includes",
                "--get-all",
                f"remote.{remote}.pushurl",
            ),
            _local_config_environment(),
        )
        if completed.returncode == 1:
            completed = self._git_runner(
                (
                    "git",
                    "-C",
                    str(workdir.resolve()),
                    "config",
                    "--local",
                    "--no-includes",
                    "--get-all",
                    f"remote.{remote}.url",
                ),
                _local_config_environment(),
            )
        if completed.returncode:
            detail = completed.stderr.strip() or "cannot resolve Git push remote"
            raise FlowError(detail)
        urls = [value for value in completed.stdout.splitlines() if value]
        if len(urls) != 1:
            raise FlowError("author push requires exactly one raw local push URL")
        remote_url = urls[0]
        parsed = urlsplit(remote_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.casefold() != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise FlowError("author push requires a credential-free HTTPS github.com remote")
        return remote_url

    @staticmethod
    def _author_push_clears(remote_url: str) -> tuple[str, ...]:
        """Clear generic, host, owner, and exact-remote credential overrides."""
        parsed = urlsplit(remote_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise FlowError("author push URL must identify a GitHub repository")
        host = "https://github.com"
        owner = f"{host}/{parts[0]}"
        exact = f"{host}/{'/'.join(parts)}"
        return (
            "credential.helper",
            f"credential.{host}.helper",
            f"credential.{owner}.helper",
            f"credential.{exact}.helper",
            "http.extraHeader",
            f"http.{host}/.extraHeader",
            f"http.{owner}/.extraHeader",
            f"http.{exact}.extraHeader",
        )

    @staticmethod
    def _author_push_helper(remote_url: str) -> str:
        """Return the host-level helper key that works without credential paths."""
        parsed = urlsplit(remote_url)
        if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
            raise FlowError("author push requires a GitHub hostname")
        return "credential.https://github.com.helper"

    def merge_if_ready(self, repo: str, number: int, reviewed_sha: str) -> None:
        _author, reviewer = self.preflight()
        current = self.author.current_head(repo, number)
        if current != reviewed_sha:
            raise FlowError(f"pull request head changed: expected {reviewed_sha}, found {current}")
        if self.reviewer.unresolved_threads(repo, number):
            raise FlowError("unresolved review threads remain")
        if not self.author.required_checks_pass(repo, number):
            raise FlowError("required checks are not passing")
        if not self.author.has_current_approval(repo, number, reviewer, reviewed_sha):
            raise FlowError("latest reviewer state is not an approval for the current head")
        if self.author.current_head(repo, number) != reviewed_sha:
            raise FlowError("pull request head changed before merge")
        self.author._run(
            "gh",
            "pr",
            "merge",
            str(number),
            "--repo",
            repo,
            "--merge",
            "--match-head-commit",
            reviewed_sha,
            allow_empty=True,
        )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("preflight", "push", "ensure-pr", "publish-review", "merge")
    )
    parser.add_argument("--author-config", type=Path, required=True)
    parser.add_argument("--reviewer-config", type=Path, required=True)
    parser.add_argument("--author-login", required=True)
    parser.add_argument("--reviewer-login", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--sha")
    parser.add_argument("--branch")
    parser.add_argument("--base")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--refspec")
    parser.add_argument("--title")
    parser.add_argument(
        "--event", choices=("APPROVE", "REQUEST_CHANGES", "COMMENT"), default="APPROVE"
    )
    parser.add_argument("--body", default="Independent current-head review.")
    parser.add_argument("--body-file", type=Path)
    args = parser.parse_args(argv)
    if args.body_file is not None:
        args.body = args.body_file.read_text(encoding="utf-8")
    flow = DeliveryFlow(
        GHClient(args.author_config),
        GHClient(args.reviewer_config),
        args.author_login,
        args.reviewer_login,
    )
    try:
        if args.command == "preflight":
            print(json.dumps(dict(zip(("author", "reviewer"), flow.preflight()))))
        elif args.command == "push":
            if not (args.workdir and args.refspec):
                raise FlowError("--workdir and --refspec are required")
            flow.push_branch(args.workdir, args.remote, args.refspec)
        elif args.command == "ensure-pr":
            if not (args.repo and args.branch and args.base and args.title):
                raise FlowError("--repo, --branch, --base, and --title are required")
            flow.preflight()
            print(
                json.dumps(
                    {
                        "number": flow.author.find_or_create_pr(
                            args.repo, args.branch, args.base, args.title, args.body
                        )
                    }
                )
            )
        elif args.command == "publish-review":
            if not (args.repo and args.pr and args.sha):
                raise FlowError("--repo, --pr, and --sha are required")
            flow.publish_review(args.repo, args.pr, ReviewVerdict(args.sha, args.event, args.body))
        else:
            if not (args.repo and args.pr and args.sha):
                raise FlowError("--repo, --pr, and --sha are required")
            flow.merge_if_ready(args.repo, args.pr, args.sha)
    except FlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
