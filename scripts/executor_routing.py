"""Validate local executor routing and select a capability-matching candidate.

This helper is the runnable route surface. It does not spend quota, start a
daemon, or invent a live balance from a user cost declaration.
"""

from __future__ import annotations

try:
    from scripts.runtime import ensure_supported_python
except ModuleNotFoundError:  # Support the documented `python scripts/executor_routing.py` entry.
    from runtime import ensure_supported_python

ensure_supported_python(__file__)

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SELECTION_NATIVE_ONLY = "native_only"
SELECTION_PAID_PREFERRED = "paid_preferred"
SELECTION_PAID_STRICT = "paid_strict"
SELECTIONS = frozenset({SELECTION_NATIVE_ONLY, SELECTION_PAID_PREFERRED, SELECTION_PAID_STRICT})
BACKEND_GROK = "grok"
BACKEND_CODEX = "codex"
SUPPORTED_BACKENDS = frozenset({BACKEND_GROK, BACKEND_CODEX})
COST_PAID_INCLUDED = "paid_included"
COST_METERED = "metered"
COST_UNKNOWN = "unknown"
COST_PREFERENCES = frozenset({COST_PAID_INCLUDED, COST_METERED, COST_UNKNOWN})
AVAIL_CONFIGURED = "configured"
AVAIL_UNAVAILABLE = "unavailable"
AVAIL_UNKNOWN = "unknown"
AVAILABILITIES = frozenset({AVAIL_CONFIGURED, AVAIL_UNAVAILABLE, AVAIL_UNKNOWN})
PERMIT_QUOTA = "quota_exhausted"
PERMITTED_FALLBACK_CAUSES = frozenset({PERMIT_QUOTA})
GENERIC_FAILURE_CAUSES = frozenset(
    {"network", "auth", "timeout", "bridge", "http_429", "malformed_receipt"}
)
GROK_REQUESTED_MODEL = "grok-4.6"
GROK_ACTUAL_MODEL = "grok-4.6-build"
LEGACY_GROK_ID = "grok_build"
LEGACY_NATIVE_ID = "native"
SCHEMA = "codex-executor-routing.v1"
RECEIPT_SCHEMA = "codex-external-execution.v1"
EXECUTOR_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")


class RoutingError(RuntimeError):
    """Local routing configuration or selection could not be used."""


@dataclass(frozen=True)
class ExecutorSpec:
    id: str
    backend: str
    requested_model: str
    actual_model: str
    capabilities: frozenset[str]
    tools: frozenset[str]
    cost_preference: str
    availability: str


@dataclass(frozen=True)
class RoutingPolicy:
    selection: str
    executors: tuple[ExecutorSpec, ...]
    fallback_permit: tuple[str, ...]
    fallback_target: str | None
    grok_required: bool
    legacy: bool
    native_is_fallback: bool


@dataclass(frozen=True)
class SelectionResult:
    status: str
    selected_id: str | None
    backend: str | None
    requested_model: str | None
    actual_model: str | None
    reason: str
    fallback_authorized: bool
    blocked: str | None
    grok_required: bool
    native_is_fallback: bool
    selection: str
    invocation: dict[str, Any] | None = None
    eligible: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "selected_id": self.selected_id,
            "backend": self.backend,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "reason": self.reason,
            "fallback_authorized": self.fallback_authorized,
            "blocked": self.blocked,
            "grok_required": self.grok_required,
            "native_is_fallback": self.native_is_fallback,
            "selection": self.selection,
            "invocation": self.invocation,
            "eligible": list(self.eligible),
        }


def load_local_config(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RoutingError(f"local configuration is missing: {path}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RoutingError(f"local configuration is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise RoutingError("local configuration is not a table")
    return data


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise RoutingError(f"{field} must be a list of nonempty strings")
    return tuple(item.strip() for item in value)


def _models(config: dict[str, Any]) -> dict[str, Any]:
    models = config.get("models", {})
    if not isinstance(models, dict):
        raise RoutingError("[models] must be a TOML table")
    return models


def _native_model(config: dict[str, Any]) -> str:
    value = _models(config).get("executor", "")
    return value.strip() if isinstance(value, str) else ""


def _legacy_policy(config: dict[str, Any]) -> RoutingPolicy:
    native_model = _native_model(config)
    grok = ExecutorSpec(
        id=LEGACY_GROK_ID,
        backend=BACKEND_GROK,
        requested_model=GROK_REQUESTED_MODEL,
        actual_model=GROK_ACTUAL_MODEL,
        capabilities=frozenset({"implementation", "tests", "git", "local_write"}),
        tools=frozenset({"workspace-write"}),
        cost_preference=COST_PAID_INCLUDED,
        availability=AVAIL_CONFIGURED,
    )
    native = ExecutorSpec(
        id=LEGACY_NATIVE_ID,
        backend=BACKEND_CODEX,
        requested_model=native_model,
        actual_model=native_model,
        capabilities=frozenset({"implementation", "tests", "git", "local_write"}),
        tools=frozenset({"workspace-write"}),
        cost_preference=COST_UNKNOWN,
        availability=AVAIL_CONFIGURED if native_model else AVAIL_UNKNOWN,
    )
    return RoutingPolicy(
        selection=SELECTION_PAID_STRICT,
        executors=(grok, native),
        fallback_permit=(PERMIT_QUOTA,),
        fallback_target=LEGACY_NATIVE_ID,
        grok_required=True,
        legacy=True,
        native_is_fallback=True,
    )


def _parse_executor(raw: Any, config: dict[str, Any]) -> ExecutorSpec:
    if not isinstance(raw, dict):
        raise RoutingError("[[routing.executors]] entries must be tables")
    ident = raw.get("id")
    backend = raw.get("backend")
    if not isinstance(ident, str) or not ident.strip():
        raise RoutingError("executor id is required")
    ident = ident.strip()
    if not EXECUTOR_ID_RE.fullmatch(ident):
        raise RoutingError(
            f"executor id {ident!r} must be a portable token "
            "[A-Za-z][A-Za-z0-9_-]{0,62} without path separators"
        )
    if not isinstance(backend, str) or backend not in SUPPORTED_BACKENDS:
        raise RoutingError(
            f"executor {ident!r} backend must be one of: {', '.join(sorted(SUPPORTED_BACKENDS))}"
        )
    cost = raw.get("cost_preference", COST_UNKNOWN)
    availability = raw.get("availability", AVAIL_CONFIGURED)
    if cost not in COST_PREFERENCES:
        raise RoutingError(f"executor {ident!r} has unknown cost_preference {cost!r}")
    if availability not in AVAILABILITIES:
        raise RoutingError(f"executor {ident!r} has unknown availability {availability!r}")
    requested = raw.get("requested_model", "")
    actual = raw.get("actual_model", raw.get("model", ""))
    if backend == BACKEND_CODEX:
        native = _native_model(config)
        if not isinstance(requested, str) or not requested.strip():
            requested = native
        if not isinstance(actual, str) or not actual.strip():
            actual = native or requested
    if not isinstance(requested, str):
        requested = ""
    if not isinstance(actual, str):
        actual = ""
    if backend == BACKEND_GROK:
        requested = requested.strip() or GROK_REQUESTED_MODEL
        actual = actual.strip() or GROK_ACTUAL_MODEL
        if requested != GROK_REQUESTED_MODEL or actual != GROK_ACTUAL_MODEL:
            raise RoutingError(
                f"executor {ident.strip()!r} uses unsupported Grok identity "
                f"{requested}/{actual}; the bridge is fixed "
                f"{GROK_REQUESTED_MODEL}/{GROK_ACTUAL_MODEL}"
            )
    return ExecutorSpec(
        id=ident.strip(),
        backend=backend,
        requested_model=requested.strip(),
        actual_model=actual.strip(),
        capabilities=frozenset(
            _string_list(raw.get("capabilities"), f"executor {ident} capabilities")
        ),
        tools=frozenset(_string_list(raw.get("tools"), f"executor {ident} tools")),
        cost_preference=cost,
        availability=availability,
    )


def parse_policy(config: dict[str, Any]) -> RoutingPolicy:
    routing = config.get("routing")
    if routing is None:
        return _legacy_policy(config)
    if not isinstance(routing, dict):
        raise RoutingError("[routing] must be a TOML table")
    selection = routing.get("selection")
    if selection not in SELECTIONS:
        raise RoutingError(
            "[routing].selection must be native_only, paid_preferred, or paid_strict"
        )
    raw_executors = routing.get("executors", [])
    if not isinstance(raw_executors, list) or not raw_executors:
        raise RoutingError("[routing.executors] must list at least one executor")
    executors = tuple(_parse_executor(item, config) for item in raw_executors)
    ids = [item.id for item in executors]
    if len(ids) != len(set(ids)):
        raise RoutingError("executor ids must be unique")
    fallback = routing.get("fallback", {})
    if fallback is None:
        fallback = {}
    if not isinstance(fallback, dict):
        raise RoutingError("[routing.fallback] must be a table")
    permit = _string_list(fallback.get("permit"), "routing.fallback.permit")
    unknown_permit = [item for item in permit if item not in PERMITTED_FALLBACK_CAUSES]
    if unknown_permit:
        raise RoutingError(
            "unknown fallback permit "
            f"{unknown_permit[0]!r}; supported: {', '.join(sorted(PERMITTED_FALLBACK_CAUSES))}"
        )
    target = fallback.get("target")
    if target is not None and (not isinstance(target, str) or not target.strip()):
        raise RoutingError("[routing.fallback].target must be an executor id")
    target_id = target.strip() if isinstance(target, str) else None
    if target_id and target_id not in ids:
        raise RoutingError(f"fallback target {target_id!r} is not a configured executor")
    grok_sources = [item for item in executors if item.backend == BACKEND_GROK]
    if PERMIT_QUOTA in permit:
        if not grok_sources:
            raise RoutingError("quota fallback requires a configured Grok source")
        if not target_id:
            raise RoutingError("quota fallback requires a Codex target")
        target_spec = next(item for item in executors if item.id == target_id)
        if target_spec.backend != BACKEND_CODEX:
            raise RoutingError("quota fallback target must be a Codex executor")
    elif target_id:
        raise RoutingError('fallback target requires permit = ["quota_exhausted"]')
    if selection == SELECTION_NATIVE_ONLY and not any(
        item.backend == BACKEND_CODEX for item in executors
    ):
        raise RoutingError("native_only requires a Codex backend executor")
    native_is_fallback = bool(
        selection != SELECTION_NATIVE_ONLY and PERMIT_QUOTA in permit and grok_sources and target_id
    )
    tentative = RoutingPolicy(
        selection=selection,
        executors=executors,
        fallback_permit=permit,
        fallback_target=target_id,
        grok_required=False,
        legacy=False,
        native_is_fallback=native_is_fallback,
    )
    grok_required = any(
        item.backend == BACKEND_GROK
        and item.availability == AVAIL_CONFIGURED
        and mode_allows_initial(tentative, item)
        for item in executors
    )
    return RoutingPolicy(
        selection=selection,
        executors=executors,
        fallback_permit=permit,
        fallback_target=target_id,
        grok_required=grok_required,
        legacy=False,
        native_is_fallback=native_is_fallback,
    )


def _by_id(policy: RoutingPolicy, ident: str) -> ExecutorSpec:
    for item in policy.executors:
        if item.id == ident:
            return item
    raise RoutingError(f"unknown executor id: {ident}")


def _capability_match(
    spec: ExecutorSpec, capabilities: Sequence[str], tools: Sequence[str]
) -> str | None:
    missing_caps = [item for item in capabilities if item not in spec.capabilities]
    missing_tools = [item for item in tools if item not in spec.tools]
    if missing_caps:
        return f"capability mismatch: missing {', '.join(missing_caps)}"
    if missing_tools:
        return f"tool mismatch: missing {', '.join(missing_tools)}"
    return None


def _availability_block(spec: ExecutorSpec) -> str | None:
    if spec.availability == AVAIL_UNAVAILABLE:
        return f"executor {spec.id!r} is marked unavailable"
    if spec.availability == AVAIL_UNKNOWN:
        return f"executor {spec.id!r} availability is unknown"
    if spec.backend not in SUPPORTED_BACKENDS:
        return f"unsupported backend {spec.backend!r}"
    if spec.backend == BACKEND_CODEX and not spec.actual_model:
        return f"executor {spec.id!r} has no native model mapping"
    return None


def toml_quoted(value: str, field: str) -> str:
    if not isinstance(value, str) or any(char in value for char in ("\n", "\r", "\0")):
        raise RoutingError(f"{field} is not a TOML-safe string")
    return json.dumps(value)


def default_native_spec(policy: RoutingPolicy) -> ExecutorSpec | None:
    if policy.fallback_target:
        for item in policy.executors:
            if item.id == policy.fallback_target and item.backend == BACKEND_CODEX:
                return item
    for item in policy.executors:
        if item.backend == BACKEND_CODEX:
            return item
    return None


def executor_agent_name(spec: ExecutorSpec, policy: RoutingPolicy) -> str:
    default = default_native_spec(policy)
    if default is not None and spec.id == default.id:
        return "v23_executor"
    return f"v23_executor_{spec.id}"


def executor_agent_file(spec: ExecutorSpec, policy: RoutingPolicy) -> str:
    name = executor_agent_name(spec, policy)
    if name == "v23_executor":
        return "agents/v23-executor.toml"
    return f"agents/v23-executor-{spec.id}.toml"


def is_quota_fallback_target(policy: RoutingPolicy, spec: ExecutorSpec) -> bool:
    if policy.selection == SELECTION_NATIVE_ONLY:
        return False
    return (
        PERMIT_QUOTA in policy.fallback_permit
        and policy.fallback_target == spec.id
        and spec.backend == BACKEND_CODEX
        and any(item.backend == BACKEND_GROK for item in policy.executors)
    )


def is_fallback_only_role(policy: RoutingPolicy, spec: ExecutorSpec) -> bool:
    if policy.selection == SELECTION_NATIVE_ONLY:
        return False
    if spec.backend != BACKEND_CODEX:
        return False
    if policy.legacy:
        return True
    if not is_quota_fallback_target(policy, spec):
        return False
    if policy.selection == SELECTION_PAID_PREFERRED:
        return False
    return not (
        policy.selection == SELECTION_PAID_STRICT and spec.cost_preference == COST_PAID_INCLUDED
    )


def mode_allows_initial(policy: RoutingPolicy, spec: ExecutorSpec) -> bool:
    """Whether this candidate may be an initial (non-fallback) selection."""
    if policy.selection == SELECTION_NATIVE_ONLY:
        return spec.backend == BACKEND_CODEX
    if is_fallback_only_role(policy, spec):
        return False
    if policy.legacy:
        return spec.backend == BACKEND_GROK
    if policy.selection == SELECTION_PAID_STRICT:
        return spec.cost_preference == COST_PAID_INCLUDED
    return True


def dispatch_plan(spec: ExecutorSpec, policy: RoutingPolicy) -> dict[str, Any]:
    if spec.backend == BACKEND_GROK:
        return {
            "kind": "grok_bridge",
            "agent": None,
            "config_file": "bin/grok-execution.py",
            "model": spec.actual_model,
            "how": (
                "Invoke the installed Grok bridge with Python: "
                'python "${CODEX_HOME}/bin/grok-execution.py" run --cwd <dir> '
                "--task-id <id> --owned-path <path> --prompt-file <file>. "
                "The bridge identity is fixed grok-4.6 / grok-4.6-build."
            ),
        }
    agent = executor_agent_name(spec, policy)
    return {
        "kind": "codex_subagent",
        "agent": agent,
        "config_file": executor_agent_file(spec, policy),
        "model": spec.actual_model,
        "how": (
            f"Spawn the registered Codex custom agent {agent!r} (not a CLI JSON executable). "
            "Its installed TOML model must equal this actual_model; re-run install after "
            "changing local model mappings."
        ),
    }


def _selected(
    spec: ExecutorSpec,
    policy: RoutingPolicy,
    reason: str,
    eligible: Sequence[str] = (),
) -> SelectionResult:
    return SelectionResult(
        status="selected",
        selected_id=spec.id,
        backend=spec.backend,
        requested_model=spec.requested_model,
        actual_model=spec.actual_model,
        reason=reason,
        fallback_authorized=False,
        blocked=None,
        grok_required=policy.grok_required,
        native_is_fallback=is_fallback_only_role(policy, spec),
        selection=policy.selection,
        invocation=dispatch_plan(spec, policy),
        eligible=tuple(eligible),
    )


def _blocked(policy: RoutingPolicy, detail: str, eligible: Sequence[str] = ()) -> SelectionResult:
    return SelectionResult(
        status="blocked",
        selected_id=None,
        backend=None,
        requested_model=None,
        actual_model=None,
        reason=detail,
        fallback_authorized=False,
        blocked=detail,
        grok_required=policy.grok_required,
        native_is_fallback=policy.native_is_fallback,
        selection=policy.selection,
        invocation=None,
        eligible=tuple(eligible),
    )


def _fallback_result(
    spec: ExecutorSpec,
    policy: RoutingPolicy,
    reason: str,
    eligible: Sequence[str] = (),
) -> SelectionResult:
    return SelectionResult(
        status="fallback",
        selected_id=spec.id,
        backend=spec.backend,
        requested_model=spec.requested_model,
        actual_model=spec.actual_model,
        reason=reason,
        fallback_authorized=True,
        blocked=None,
        grok_required=policy.grok_required,
        native_is_fallback=True,
        selection=policy.selection,
        invocation=dispatch_plan(spec, policy),
        eligible=tuple(eligible),
    )


def classify_failure_cause(cause: str | None) -> str | None:
    if cause is None or not cause.strip():
        return None
    normalized = cause.strip()
    if normalized in PERMITTED_FALLBACK_CAUSES:
        return normalized
    if normalized in GENERIC_FAILURE_CAUSES:
        return normalized
    return normalized


def select_executor(
    policy: RoutingPolicy,
    *,
    capabilities: Sequence[str] = (),
    tools: Sequence[str] = (),
    failure_cause: str | None = None,
    executor_id: str | None = None,
    choice_reason: str | None = None,
) -> SelectionResult:
    """Choose one configured executor for the stated task needs."""

    if not capabilities and policy.selection:
        # Read-only or unspecified work does not force an implementation executor.
        return SelectionResult(
            status="not_required",
            selected_id=None,
            backend=None,
            requested_model=None,
            actual_model=None,
            reason="read-only or unspecified work does not require an implementation executor",
            fallback_authorized=False,
            blocked=None,
            grok_required=policy.grok_required,
            native_is_fallback=policy.native_is_fallback,
            selection=policy.selection,
        )

    cause = classify_failure_cause(failure_cause)
    if cause is not None:
        if cause == PERMIT_QUOTA:
            return _blocked(
                policy,
                "quota fallback requires validate-receipt with a bound receipt; "
                "a failure-cause string is not authorization",
            )
        if cause in GENERIC_FAILURE_CAUSES:
            return _blocked(
                policy,
                f"{cause} is not a quota receipt and does not authorize fallback",
            )
        return _blocked(
            policy,
            f"unknown failure {cause!r} does not authorize fallback or restart selection",
        )

    candidates: list[tuple[str | None, ExecutorSpec]] = []
    for spec in policy.executors:
        mismatch = _capability_match(spec, capabilities, tools)
        avail = None if mismatch else _availability_block(spec)
        candidates.append((mismatch or avail, spec))

    eligible = [spec for problem, spec in candidates if problem is None]
    if policy.selection == SELECTION_NATIVE_ONLY:
        eligible = [spec for spec in eligible if spec.backend == BACKEND_CODEX]
    names = tuple(spec.id for spec in eligible)
    paid = [spec for spec in eligible if spec.cost_preference == COST_PAID_INCLUDED]

    if executor_id:
        chosen = next((spec for spec in eligible if spec.id == executor_id), None)
        if chosen is None:
            return _blocked(
                policy,
                f"executor {executor_id!r} is not an eligible candidate for this task",
                names,
            )
        if is_fallback_only_role(policy, chosen):
            return _blocked(
                policy,
                f"executor {chosen.id!r} is quota-fallback-only; use validate-receipt",
                names,
            )
        if policy.selection == SELECTION_PAID_STRICT and chosen not in paid:
            return _blocked(
                policy,
                "paid_strict cannot select a non-paid/included executor without a bound quota receipt",
                names,
            )
        if not isinstance(choice_reason, str) or not choice_reason.strip():
            return _blocked(
                policy,
                "explicit --executor requires a nonempty --reason",
                names,
            )
        return _selected(
            chosen,
            policy,
            f"explicit choice of {chosen.id}: {choice_reason.strip()}",
            names,
        )

    if policy.selection == SELECTION_NATIVE_ONLY:
        if not eligible:
            detail = (
                "; ".join(
                    f"{spec.id}: {problem}"
                    for problem, spec in candidates
                    if spec.backend == BACKEND_CODEX
                )
                or "no Codex executor is configured"
            )
            return _blocked(policy, f"native_only has no suitable Codex executor ({detail})")
        pool = eligible
    else:
        if policy.selection == SELECTION_PAID_STRICT and not paid:
            problems = "; ".join(
                f"{spec.id}: {problem or spec.cost_preference}"
                for problem, spec in candidates
                if spec.cost_preference == COST_PAID_INCLUDED or spec.backend == BACKEND_GROK
            )
            return _blocked(
                policy,
                "paid_strict requires a suitable paid/included executor; "
                + (problems or "none available"),
                names,
            )
        pool = paid or eligible
        if not pool:
            problems = "; ".join(f"{spec.id}: {problem}" for problem, spec in candidates if problem)
            return _blocked(policy, "no suitable executor: " + (problems or "none configured"))

    if len(pool) != 1:
        ids = ", ".join(spec.id for spec in pool)
        return _blocked(
            policy,
            "multiple suitable executors; choose with --executor <id> --reason <why>: " + ids,
            tuple(spec.id for spec in pool),
        )
    chosen = pool[0]
    preference = (
        "paid/included" if chosen.cost_preference == COST_PAID_INCLUDED else chosen.cost_preference
    )
    return _selected(
        chosen,
        policy,
        f"{policy.selection} selected {chosen.id} ({preference}, {chosen.backend} {chosen.actual_model})",
        names,
    )


def validate_fallback_receipt(
    policy: RoutingPolicy,
    receipt: dict[str, Any],
    *,
    task_id: str,
    working_directory: str,
    owned_paths: Sequence[str],
    capabilities: Sequence[str] = (),
    tools: Sequence[str] = (),
    source_id: str | None = None,
) -> SelectionResult:
    """Authorize native fallback only from a bound quota receipt."""

    grok_sources = [item for item in policy.executors if item.backend == BACKEND_GROK]
    if (
        policy.selection == SELECTION_NATIVE_ONLY
        or not grok_sources
        or PERMIT_QUOTA not in policy.fallback_permit
        or not policy.fallback_target
    ):
        return _blocked(policy, "this policy does not authorize Grok quota fallback")
    if not isinstance(receipt, dict):
        return _blocked(policy, "fallback receipt is not a JSON object")
    if not isinstance(task_id, str) or not task_id.strip():
        return _blocked(policy, "task-id is required and must be nonempty")
    cwd = Path(working_directory)
    if not cwd.is_absolute():
        return _blocked(policy, "working_directory must be an absolute path")
    if not owned_paths:
        return _blocked(policy, "at least one owned-path is required")
    normalized_owned = [str(Path(item)) for item in owned_paths]
    if any(not Path(item).is_absolute() for item in normalized_owned):
        return _blocked(policy, "owned paths must be absolute")
    if len(normalized_owned) != len(set(normalized_owned)):
        return _blocked(policy, "owned paths must be unique")
    required = {
        "schema": RECEIPT_SCHEMA,
        "status": "QUOTA_EXHAUSTED",
        "fallback_reason": "grok_quota_exhausted",
        "task_id": task_id,
        "working_directory": str(cwd),
        "owned_paths": normalized_owned,
        "requested_model": GROK_REQUESTED_MODEL,
    }
    mismatched = [key for key, value in required.items() if receipt.get(key) != value]
    actual = receipt.get("actual_model")
    if actual is not None and actual != GROK_ACTUAL_MODEL:
        mismatched.append("actual_model")
    if mismatched:
        return _blocked(
            policy,
            "fallback receipt binding mismatch: " + ", ".join(mismatched),
        )
    usable_sources = [
        item
        for item in grok_sources
        if mode_allows_initial(policy, item)
        and _capability_match(item, capabilities, tools) is None
        and _availability_block(item) is None
    ]
    if source_id:
        source = next((item for item in grok_sources if item.id == source_id), None)
        if source is None or source not in usable_sources:
            return _blocked(policy, f"source {source_id!r} is not an eligible Grok executor")
    elif len(usable_sources) == 1:
        source = usable_sources[0]
    elif len(usable_sources) > 1:
        return _blocked(
            policy,
            "multiple Grok sources; pass --source <id>: "
            + ", ".join(item.id for item in usable_sources),
        )
    else:
        return _blocked(policy, "no eligible Grok source for this task")
    target = _by_id(policy, policy.fallback_target)
    mismatch = _capability_match(target, capabilities, tools) or _availability_block(target)
    if mismatch:
        return _blocked(policy, f"fallback target {target.id} is not usable: {mismatch}")
    return _fallback_result(
        target,
        policy,
        f"bound quota receipt from {source.id} authorizes fallback to {target.id}",
    )


def executor_agent_description(policy: RoutingPolicy, spec: ExecutorSpec | None = None) -> str:
    if spec is not None and is_fallback_only_role(policy, spec):
        return "V23 quota-exhaustion-only native execution fallback."
    if spec is None and policy.legacy:
        return "V23 quota-exhaustion-only native execution fallback."
    return "V23 native implementation executor."


def executor_agent_instructions(policy: RoutingPolicy, spec: ExecutorSpec | None = None) -> str:
    if spec is not None and is_fallback_only_role(policy, spec):
        return (
            "You are the native fallback executor for one scoped change. Act only when the\n"
            "parent supplies a verified Grok bridge receipt with status QUOTA_EXHAUSTED and\n"
            "fallback_reason grok_quota_exhausted, and the receipt task_id, absolute\n"
            "working_directory, and owned_paths match this task exactly. Generic HTTP 429,\n"
            "authentication, network, or timeout errors are not quota exhaustion. Otherwise\n"
            "stop with GROK_FALLBACK_NOT_AUTHORIZED. When authorized, make the smallest\n"
            "complete change, keep one writer per worktree, and leave concise test evidence.\n"
            "Do not add new ceremonies, hashes, gates, or abstraction layers without a\n"
            "concrete failure mode that ordinary version control, types, tests, or platform\n"
            "controls cannot handle. Escalate only real ambiguity or consequential external\n"
            "action. Primary remains decision-only; do not assign implementation back to\n"
            "primary."
        )
    if spec is None and policy.legacy:
        return executor_agent_instructions(policy, default_native_spec(policy))
    if policy.selection == SELECTION_PAID_PREFERRED:
        return (
            "You are a native Codex executor that may be selected initially when you are the\n"
            "capability-fit candidate, including when a paid Grok candidate is unavailable.\n"
            "That initial selection is not a quota fallback. After a Grok attempt, switch to\n"
            "this agent only with a bound QUOTA_EXHAUSTED receipt; network, auth, timeout,\n"
            "and bridge errors are not quota. Make the smallest complete change, keep one\n"
            "writer per worktree, and leave concise test evidence. Primary remains\n"
            "decision-only; do not assign implementation back to primary."
        )
    if policy.selection == SELECTION_NATIVE_ONLY:
        return (
            "You are the native implementation executor for one scoped change. This\n"
            "native_only route is complete: Grok is not required and no quota receipt is\n"
            "needed. Make the smallest complete change, keep one writer per worktree, and\n"
            "leave concise test evidence. Do not add new ceremonies, hashes, gates, or\n"
            "abstraction layers without a concrete failure mode that ordinary version\n"
            "control, types, tests, or platform controls cannot handle. Escalate only real\n"
            "ambiguity or consequential external action. Primary remains decision-only; do\n"
            "not assign implementation back to primary."
        )
    return (
        "You are a native Codex implementation executor selected under the configured\n"
        "routing mode. This candidate is a normal selectable executor, not a Grok quota\n"
        "fallback, and no quota receipt is required. Make the smallest complete change,\n"
        "keep one writer per worktree, and leave concise test evidence. Do not add new\n"
        "ceremonies, hashes, gates, or abstraction layers without a concrete failure\n"
        "mode that ordinary version control, types, tests, or platform controls cannot\n"
        "handle. Escalate only real ambiguity or consequential external action. Primary\n"
        "remains decision-only; do not assign implementation back to primary."
    )


def render_executor_agent(
    policy: RoutingPolicy,
    model: str,
    effort: str,
    *,
    name: str = "v23_executor",
    spec: ExecutorSpec | None = None,
) -> str:
    description = executor_agent_description(policy, spec)
    instructions = executor_agent_instructions(policy, spec)
    return (
        f"name = {toml_quoted(name, 'agent name')}\n"
        f"description = {toml_quoted(description, 'agent description')}\n"
        f"model = {toml_quoted(model, 'agent model')}\n"
        f"model_reasoning_effort = {toml_quoted(effort, 'agent effort')}\n"
        'sandbox_mode = "workspace-write"\n'
        "\n"
        'developer_instructions = """\n'
        f"{instructions}\n"
        '"""\n'
    )


def grok_checks_required(policy: RoutingPolicy) -> bool:
    return policy.grok_required


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "validate-receipt", "show-policy"))
    parser.add_argument("--local-config", type=Path, required=True)
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--cause")
    parser.add_argument("--executor")
    parser.add_argument("--reason")
    parser.add_argument("--source")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--cwd")
    parser.add_argument("--owned-path", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        policy = parse_policy(load_local_config(args.local_config))
        if args.command == "show-policy":
            payload = {
                "schema": SCHEMA,
                "selection": policy.selection,
                "legacy": policy.legacy,
                "grok_required": policy.grok_required,
                "native_is_fallback": policy.native_is_fallback,
                "fallback_permit": list(policy.fallback_permit),
                "fallback_target": policy.fallback_target,
                "executors": [
                    {
                        "id": item.id,
                        "backend": item.backend,
                        "requested_model": item.requested_model,
                        "actual_model": item.actual_model,
                        "capabilities": sorted(item.capabilities),
                        "tools": sorted(item.tools),
                        "cost_preference": item.cost_preference,
                        "availability": item.availability,
                    }
                    for item in policy.executors
                ],
            }
        elif args.command == "select":
            payload = select_executor(
                policy,
                capabilities=args.capability,
                tools=args.tool,
                failure_cause=args.cause,
                executor_id=args.executor,
                choice_reason=args.reason,
            ).as_dict()
        else:
            if args.receipt is None or not args.task_id or not args.cwd:
                raise RoutingError("validate-receipt requires --receipt, --task-id, and --cwd")
            try:
                receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise RoutingError(f"receipt file is missing: {args.receipt}") from exc
            except OSError as exc:
                raise RoutingError(f"receipt file is unreadable: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise RoutingError(f"malformed receipt JSON: {exc.msg}") from exc
            if not isinstance(receipt, dict):
                raise RoutingError("receipt is not a JSON object")
            payload = validate_fallback_receipt(
                policy,
                receipt,
                task_id=args.task_id,
                working_directory=args.cwd,
                owned_paths=args.owned_path,
                capabilities=args.capability,
                tools=args.tool,
                source_id=args.source,
            ).as_dict()
    except RoutingError as error:
        print(
            json.dumps({"schema": SCHEMA, "status": "error", "blocked": str(error)}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
