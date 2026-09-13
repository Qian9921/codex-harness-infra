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
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SELECTION_NATIVE_ONLY = "native_only"
SELECTION_PAID_PREFERRED = "paid_preferred"
SELECTION_PAID_STRICT = "paid_strict"
SELECTIONS = frozenset(
    {SELECTION_NATIVE_ONLY, SELECTION_PAID_PREFERRED, SELECTION_PAID_STRICT}
)
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
    return ExecutorSpec(
        id=ident.strip(),
        backend=backend,
        requested_model=requested.strip(),
        actual_model=actual.strip(),
        capabilities=frozenset(_string_list(raw.get("capabilities"), f"executor {ident} capabilities")),
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
    grok_required = any(item.backend == BACKEND_GROK for item in executors) and selection != (
        SELECTION_NATIVE_ONLY
    )
    if selection == SELECTION_NATIVE_ONLY:
        grok_required = False
        if not any(item.backend == BACKEND_CODEX for item in executors):
            raise RoutingError("native_only requires a Codex backend executor")
    native_is_fallback = selection != SELECTION_NATIVE_ONLY and bool(permit)
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


def _rank(spec: ExecutorSpec) -> tuple[int, str]:
    cost_rank = {
        COST_PAID_INCLUDED: 0,
        COST_UNKNOWN: 1,
        COST_METERED: 2,
    }[spec.cost_preference]
    return (cost_rank, spec.id)


def _selected(spec: ExecutorSpec, policy: RoutingPolicy, reason: str) -> SelectionResult:
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
        native_is_fallback=policy.native_is_fallback,
        selection=policy.selection,
    )


def _blocked(policy: RoutingPolicy, detail: str) -> SelectionResult:
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
    )


def _fallback_result(spec: ExecutorSpec, policy: RoutingPolicy, reason: str) -> SelectionResult:
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
        native_is_fallback=policy.native_is_fallback,
        selection=policy.selection,
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
    if cause in GENERIC_FAILURE_CAUSES:
        return _blocked(
            policy,
            f"{cause} is not a quota receipt and does not authorize fallback",
        )

    candidates: list[tuple[str | None, ExecutorSpec]] = []
    for spec in policy.executors:
        mismatch = _capability_match(spec, capabilities, tools)
        avail = None if mismatch else _availability_block(spec)
        candidates.append((mismatch or avail, spec))

    eligible = [spec for problem, spec in candidates if problem is None]
    if policy.selection == SELECTION_NATIVE_ONLY:
        native = [spec for spec in eligible if spec.backend == BACKEND_CODEX]
        if not native:
            detail = "; ".join(
                f"{spec.id}: {problem}" for problem, spec in candidates if spec.backend == BACKEND_CODEX
            ) or "no Codex executor is configured"
            return _blocked(policy, f"native_only has no suitable Codex executor ({detail})")
        chosen = min(native, key=_rank)
        return _selected(
            chosen,
            policy,
            f"native_only uses Codex executor {chosen.id} model {chosen.actual_model or 'unmapped'}",
        )

    paid = [spec for spec in eligible if spec.cost_preference == COST_PAID_INCLUDED]
    pool = paid or eligible
    if cause == PERMIT_QUOTA:
        if PERMIT_QUOTA not in policy.fallback_permit or not policy.fallback_target:
            return _blocked(policy, "quota fallback is not permitted by local routing")
        target = _by_id(policy, policy.fallback_target)
        mismatch = _capability_match(target, capabilities, tools) or _availability_block(target)
        if mismatch:
            return _blocked(policy, f"fallback target {target.id} is not usable: {mismatch}")
        return _fallback_result(
            target,
            policy,
            f"explicit quota receipt authorizes fallback to {target.id}",
        )
    if not pool:
        problems = "; ".join(f"{spec.id}: {problem}" for problem, spec in candidates if problem)
        if policy.selection == SELECTION_PAID_STRICT:
            return _blocked(
                policy,
                "paid_strict has no suitable paid or included executor: " + (problems or "none configured"),
            )
        if PERMIT_QUOTA in policy.fallback_permit and policy.fallback_target and cause == PERMIT_QUOTA:
            target = _by_id(policy, policy.fallback_target)
            return _fallback_result(target, policy, "configured fallback after paid executor unusable")
        return _blocked(policy, "no suitable executor: " + (problems or "none configured"))

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
        )

    chosen = min(pool, key=_rank)
    preference = (
        "paid/included" if chosen.cost_preference == COST_PAID_INCLUDED else chosen.cost_preference
    )
    return _selected(
        chosen,
        policy,
        f"{policy.selection} selected {chosen.id} ({preference}, {chosen.backend} {chosen.actual_model})",
    )


def validate_fallback_receipt(
    policy: RoutingPolicy,
    receipt: dict[str, Any],
    *,
    task_id: str,
    working_directory: str,
    owned_paths: Sequence[str],
) -> SelectionResult:
    """Authorize native fallback only from a bound quota receipt."""

    if PERMIT_QUOTA not in policy.fallback_permit or not policy.fallback_target:
        return _blocked(policy, "local routing does not permit quota fallback")
    required = {
        "schema": RECEIPT_SCHEMA,
        "status": "QUOTA_EXHAUSTED",
        "fallback_reason": "grok_quota_exhausted",
        "task_id": task_id,
        "working_directory": working_directory,
        "owned_paths": list(owned_paths),
    }
    mismatched = [key for key, value in required.items() if receipt.get(key) != value]
    if mismatched:
        return _blocked(
            policy,
            "fallback receipt binding mismatch: " + ", ".join(mismatched),
        )
    return select_executor(
        policy,
        capabilities=("implementation",),
        tools=(),
        failure_cause=PERMIT_QUOTA,
    )


def executor_agent_description(policy: RoutingPolicy) -> str:
    if policy.native_is_fallback:
        return "V23 quota-exhaustion-only native execution fallback."
    return "V23 native implementation executor."


def executor_agent_instructions(policy: RoutingPolicy) -> str:
    if policy.native_is_fallback:
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


def render_executor_agent(policy: RoutingPolicy, model: str, effort: str) -> str:
    description = executor_agent_description(policy)
    instructions = executor_agent_instructions(policy)
    return (
        'name = "v23_executor"\n'
        f'description = "{description}"\n'
        f'model = "{model}"\n'
        f'model_reasoning_effort = "{effort}"\n'
        'sandbox_mode = "workspace-write"\n'
        "\n"
        "developer_instructions = \"\"\"\n"
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
            ).as_dict()
        else:
            if args.receipt is None or not args.task_id or not args.cwd:
                raise RoutingError("validate-receipt requires --receipt, --task-id, and --cwd")
            receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise RoutingError("receipt is not a JSON object")
            payload = validate_fallback_receipt(
                policy,
                receipt,
                task_id=args.task_id,
                working_directory=args.cwd,
                owned_paths=args.owned_path,
            ).as_dict()
    except RoutingError as error:
        print(json.dumps({"schema": SCHEMA, "status": "error", "blocked": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
