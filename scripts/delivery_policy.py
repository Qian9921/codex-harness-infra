"""Parse explicit local delivery preference and repository authorization.

Installing or configuring credentials is not publication permission.
Omitted ``[delivery]`` is ``local_only`` with an empty repository set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODE_LOCAL_ONLY = "local_only"
MODE_PULL_REQUEST = "pull_request"
MODE_MERGE_IF_READY = "merge_if_ready"
DELIVERY_MODES = frozenset({MODE_LOCAL_ONLY, MODE_PULL_REQUEST, MODE_MERGE_IF_READY})
GITHUB_WRITE_MODES = frozenset({MODE_PULL_REQUEST, MODE_MERGE_IF_READY})


class DeliveryError(RuntimeError):
    """Local delivery configuration could not be used."""


@dataclass(frozen=True)
class DeliveryPolicy:
    mode: str
    repositories: frozenset[str]

    @property
    def github_write(self) -> bool:
        return self.mode in GITHUB_WRITE_MODES

    @property
    def merge_authorized(self) -> bool:
        return self.mode == MODE_MERGE_IF_READY

    def authorizes(self, repo: str) -> bool:
        normalized = _normalize_repo(repo)
        return bool(normalized) and normalized in self.repositories


def _normalize_repo(value: str) -> str:
    text = value.strip().removesuffix(".git")
    if text.startswith("https://github.com/"):
        text = text.removeprefix("https://github.com/")
    return text.casefold()


def parse_delivery(config: dict[str, Any] | None) -> DeliveryPolicy:
    """Return the explicit delivery policy; missing table is local-only."""
    if not config:
        return DeliveryPolicy(MODE_LOCAL_ONLY, frozenset())
    raw = config.get("delivery")
    if raw is None:
        return DeliveryPolicy(MODE_LOCAL_ONLY, frozenset())
    if not isinstance(raw, dict):
        raise DeliveryError("[delivery] must be a TOML table")
    mode = raw.get("mode", MODE_LOCAL_ONLY)
    if not isinstance(mode, str) or mode.strip() not in DELIVERY_MODES:
        raise DeliveryError("[delivery].mode must be local_only, pull_request, or merge_if_ready")
    mode = mode.strip()
    repos_raw = raw.get("repositories", [])
    if repos_raw is None:
        repos_raw = []
    if not isinstance(repos_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in repos_raw
    ):
        raise DeliveryError("[delivery].repositories must be a list of owner/name strings")
    repositories = frozenset(_normalize_repo(item) for item in repos_raw)
    if mode in GITHUB_WRITE_MODES and not repositories:
        raise DeliveryError("GitHub delivery modes require nonempty [delivery].repositories")
    if mode == MODE_LOCAL_ONLY and repositories:
        raise DeliveryError("local_only delivery must not list publication repositories")
    return DeliveryPolicy(mode, repositories)


def assert_publication_allowed(policy: DeliveryPolicy, repo: str, action: str) -> None:
    """Reject GitHub publication unless mode and repository are explicit."""
    if not policy.github_write:
        raise DeliveryError("delivery.mode is local_only; GitHub publication is not authorized")
    if not policy.authorizes(repo):
        raise DeliveryError(f"repository is not in [delivery].repositories: {repo}")
    if action == "merge" and not policy.merge_authorized:
        raise DeliveryError("merge requires delivery.mode=merge_if_ready")
    if action not in {"push", "ensure-pr", "publish-review", "merge", "preflight"}:
        raise DeliveryError(f"unsupported delivery action: {action}")
