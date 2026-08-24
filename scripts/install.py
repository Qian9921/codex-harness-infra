"""Install or remove only Codex Harness Infra V23-owned local assets.

The installer deliberately has a small ownership model: it writes V23-specific
names, records exact digests, refuses collisions, and only removes untouched
assets. It never restores a whole Codex configuration backup.
"""

from __future__ import annotations

try:
    from scripts.runtime import ensure_supported_python
except ModuleNotFoundError:  # Support the documented `python scripts/install.py` entrypoint.
    from runtime import ensure_supported_python

ensure_supported_python(__file__)

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

VERSION = "23.1.0"
MARKER = "CODEX-HARNESS-INFRA V23"
PORTABLE_KIND = "PORTABLE"
LOCAL_KIND = "LOCAL"
CONFIG_KIND = "CONFIG"
MANIFEST_NAME = "install.json"


class InstallError(RuntimeError):
    """A safe installation or removal could not be completed."""


@dataclass(frozen=True)
class Asset:
    """One file or directory owned by this installer."""

    path: Path
    source: Path | None
    kind: str
    content: bytes | None = None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_path(path: Path) -> str:
    """Return a stable digest for one regular file or a complete directory tree."""
    if path.is_symlink():
        raise InstallError(f"refusing symlink asset: {path}")
    if path.is_file():
        return sha256_bytes(path.read_bytes())
    if not path.is_dir():
        raise InstallError(f"asset does not exist: {path}")
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            raise InstallError(f"refusing symlink inside asset: {item}")
        if item.is_dir():
            digest.update(f"D:{relative}\n".encode())
        elif item.is_file():
            digest.update(f"F:{relative}\n".encode())
            digest.update(item.read_bytes())
        else:
            raise InstallError(f"unsupported asset entry: {item}")
    return digest.hexdigest()


def _marker(kind: str, edge: str) -> str:
    if kind == CONFIG_KIND:
        return f"# {edge} {MARKER} {kind}"
    return f"<!-- {edge} {MARKER} {kind} -->"


def block_body(text: str, kind: str) -> str | None:
    """Extract one complete managed block, rejecting duplicated or partial markers."""
    begin, end = _marker(kind, "BEGIN"), _marker(kind, "END")
    begins, ends = text.count(begin), text.count(end)
    if begins == ends == 0:
        return None
    if begins != 1 or ends != 1:
        raise InstallError(f"partial or duplicate {kind.lower()} marker in managed file")
    start = text.index(begin) + len(begin)
    finish = text.index(end)
    if finish < start:
        raise InstallError(f"misordered {kind.lower()} markers in managed file")
    return text[start:finish].strip("\n")


def replace_managed_block(text: str, kind: str, body: str) -> str:
    """Idempotently replace a complete V23 block without changing other content."""
    current = block_body(text, kind)
    if current is not None:
        begin, end = _marker(kind, "BEGIN"), _marker(kind, "END")
        left = text[: text.index(begin)]
        right = text[text.index(end) + len(end) :]
        text = (left.rstrip("\n") + "\n" + right.lstrip("\n")).rstrip("\n")
    rendered = f"{_marker(kind, 'BEGIN')}\n{body.strip()}\n{_marker(kind, 'END')}"
    return (text.rstrip("\n") + "\n\n" + rendered + "\n") if text.strip() else rendered + "\n"


def remove_managed_block(text: str, kind: str, expected_digest: str) -> tuple[str, bool]:
    """Remove only an unedited block; return unchanged text when it was edited."""
    body = block_body(text, kind)
    if body is None or sha256_bytes(body.encode()) != expected_digest:
        return text, False
    begin, end = _marker(kind, "BEGIN"), _marker(kind, "END")
    left = text[: text.index(begin)].rstrip("\n")
    right = text[text.index(end) + len(end) :].lstrip("\n")
    joined = (left + "\n" + right).strip("\n")
    return (joined + "\n") if joined else "", True


def atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise InstallError(f"refusing symlink target: {path}")
    data = content.encode() if isinstance(content, str) else content
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def read_toml(path: Path) -> dict:
    import tomllib

    try:
        return tomllib.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise InstallError(f"invalid local configuration {path}: {error}") from error


def _python_version(executable: Path) -> tuple[int, int] | None:
    """Return a Python executable's major/minor version without importing it."""
    try:
        completed = subprocess.run(
            [
                str(executable),
                "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)", completed.stdout.strip())
    if completed.returncode or not match:
        return None
    return int(match.group(1)), int(match.group(2))


def resolve_python_runtime(config: dict) -> Path:
    """Choose a stable Python 3.11+ runtime for the installed prompt hook."""
    runtime = config.get("runtime", {})
    if runtime and not isinstance(runtime, dict):
        raise InstallError("[runtime] must be a TOML table")
    configured = runtime.get("python", "") if isinstance(runtime, dict) else ""
    candidates: list[str]
    if configured:
        if not isinstance(configured, str):
            raise InstallError("[runtime].python must be a command or absolute path")
        candidates = [configured]
    else:
        candidates = [sys.executable, "python3.14", "python3.13", "python3.12", "python3.11"]
    seen: set[Path] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_file():
            found = shutil.which(candidate)
            if not found:
                continue
            path = Path(found)
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        version = _python_version(resolved)
        if version and version >= (3, 11):
            return resolved
    if configured:
        raise InstallError("[runtime].python must resolve to Python 3.11 or newer")
    raise InstallError("V23 requires a discoverable Python 3.11+ runtime")


def active_global_agents(codex_home: Path) -> Path:
    """Respect Codex's global override precedence instead of creating a shadow file."""
    override = codex_home / "AGENTS.override.md"
    return override if override.exists() else codex_home / "AGENTS.md"


def ensure_within(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise InstallError(f"target escapes Codex home: {path}")
    return resolved_path


def render_template(path: Path, **values: str) -> str:
    if path.is_symlink():
        raise InstallError(f"refusing symlink template: {path}")
    result = path.read_text()
    for key, value in values.items():
        if (
            not isinstance(value, str)
            or not value
            or any(char in value for char in ("\n", "\r", '"'))
        ):
            raise InstallError(f"invalid value for {key}")
        result = result.replace("{{" + key + "}}", value)
    if "{{" in result:
        raise InstallError(f"unresolved template value in {path}")
    return result


def _load_manifest(state_dir: Path) -> dict | None:
    manifest_path = state_dir / MANIFEST_NAME
    if manifest_path.is_symlink():
        raise InstallError(f"refusing symlink V23 manifest: {manifest_path}")
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (OSError, ValueError) as error:
        raise InstallError(f"invalid V23 manifest {manifest_path}: {error}") from error


def _old_assets(manifest: dict | None) -> dict[str, str]:
    return {entry["path"]: entry["digest"] for entry in (manifest or {}).get("assets", [])}


def _check_asset_target(asset: Asset, old_assets: dict[str, str]) -> None:
    key = str(asset.path)
    if not asset.path.exists() and not asset.path.is_symlink():
        return
    if asset.path.is_symlink():
        raise InstallError(f"refusing symlink target: {asset.path}")
    previous = old_assets.get(key)
    if previous is None:
        raise InstallError(f"refusing to overwrite unowned asset: {asset.path}")
    if digest_path(asset.path) != previous:
        raise InstallError(f"refusing to overwrite locally modified V23 asset: {asset.path}")


def _check_asset_parents(codex_home: Path, asset: Asset) -> None:
    """Reject a parent that would make an otherwise safe install partial."""
    parent = asset.path.parent
    while parent != codex_home:
        if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
            raise InstallError(f"unsafe V23 asset parent: {parent}")
        parent = parent.parent


def _copy_asset(asset: Asset) -> None:
    if asset.content is not None:
        atomic_write(asset.path, asset.content)
        return
    if asset.source is None:
        raise InstallError(f"asset has no source content: {asset.path}")
    if asset.source.is_symlink():
        raise InstallError(f"refusing symlink source asset: {asset.source}")
    if asset.source.is_file():
        atomic_write(asset.path, asset.source.read_bytes())
        return
    staging = Path(tempfile.mkdtemp(prefix=f".{asset.path.name}.", dir=asset.path.parent))
    try:
        destination = staging / asset.path.name
        shutil.copytree(asset.source, destination, symlinks=True)
        if any(item.is_symlink() for item in destination.rglob("*")):
            raise InstallError(f"refusing symlink in source asset: {asset.source}")
        if asset.path.exists():
            shutil.rmtree(asset.path)
        os.replace(destination, asset.path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _assets(repo_root: Path, codex_home: Path, config: dict) -> list[Asset]:
    models = config.get("models", {})
    if not isinstance(models, dict):
        raise InstallError("[models] must be a TOML table")
    primary_template = repo_root / "package/v23-primary.config.toml.in"
    executor_template = repo_root / "package/agents/v23-executor.toml.in"
    reviewer_template = repo_root / "package/agents/v23-reviewer.toml.in"
    skill_source = repo_root / ".agents/skills/engineering-delivery"
    grok_skill_source = repo_root / ".agents/skills/grok-execution"
    bootstrap_source = repo_root / "scripts/task_bootstrap.py"
    grok_bridge_source = repo_root / "scripts/grok_execution.py"
    for source in (
        primary_template,
        executor_template,
        reviewer_template,
        skill_source,
        grok_skill_source,
        bootstrap_source,
        grok_bridge_source,
    ):
        if not source.exists() or source.is_symlink():
            raise InstallError(f"invalid V23 source asset: {source}")
    primary = render_template(
        primary_template,
        primary_model=models.get("primary", ""),
        primary_effort=models.get("primary_effort", "medium"),
        reviewer_model=models.get("reviewer", ""),
    )
    executor = render_template(
        executor_template,
        executor_model=models.get("executor", ""),
        executor_effort=models.get("executor_effort", "medium"),
    )
    reviewer = render_template(reviewer_template, reviewer_model=models.get("reviewer", ""))
    try:
        primary_profile = tomllib.loads(primary)
    except tomllib.TOMLDecodeError as error:
        raise InstallError(f"invalid rendered V23 primary profile: {error}") from error
    if (
        primary_profile.get("model") != models["primary"]
        or primary_profile.get("model_reasoning_effort") != models.get("primary_effort", "medium")
        or primary_profile.get("review_model") != models["reviewer"]
    ):
        raise InstallError("rendered V23 primary profile does not match local configuration")
    _validate_agent_content(
        executor, "v23_executor", models["executor"], models.get("executor_effort", "medium")
    )
    _validate_agent_content(reviewer, "v23_reviewer", models["reviewer"], "high")
    return [
        Asset(
            ensure_within(codex_home, codex_home / "v23-primary.config.toml"),
            None,
            "file",
            primary.encode(),
        ),
        Asset(
            ensure_within(codex_home, codex_home / "agents/v23-executor.toml"),
            None,
            "file",
            executor.encode(),
        ),
        Asset(
            ensure_within(codex_home, codex_home / "agents/v23-reviewer.toml"),
            None,
            "file",
            reviewer.encode(),
        ),
        Asset(
            ensure_within(codex_home, codex_home / "skills/engineering-delivery"),
            skill_source,
            "directory",
        ),
        Asset(
            ensure_within(codex_home, codex_home / "skills/grok-execution"),
            grok_skill_source,
            "directory",
        ),
        Asset(
            ensure_within(codex_home, codex_home / "harness/v23/task_bootstrap.py"),
            bootstrap_source,
            "file",
        ),
        Asset(
            ensure_within(codex_home, codex_home / "bin/grok-execution.py"),
            grok_bridge_source,
            "file",
        ),
    ]


def _config_block(runtime_python: Path, bootstrap_path: Path, local_config: Path) -> str:
    """Render the one native V23 prompt hook and agent registrations."""
    command = " ".join(
        shlex.quote(str(part))
        for part in (runtime_python, bootstrap_path, "--local-config", local_config)
    )
    return f"""[agents.\"v23_executor\"]
description = \"V23 quota-exhaustion-only native execution fallback.\"
config_file = \"agents/v23-executor.toml\"

[agents.\"v23_reviewer\"]
description = \"V23 independent current-head reviewer.\"
config_file = \"agents/v23-reviewer.toml\"

[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = \"command\"
command = {json.dumps(command)}
timeout = 90
statusMessage = \"Running required V23 tool bootstrap\"
additionalContextLimit = 1000"""


def _prepare_state_dir(state_dir: Path) -> None:
    """Prove the manifest location is writable before mutating Codex files."""
    if state_dir.is_symlink():
        raise InstallError(f"refusing symlink V23 state directory: {state_dir}")
    if state_dir.exists() and not state_dir.is_dir():
        raise InstallError(f"V23 state path is not a directory: {state_dir}")
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        descriptor, probe = tempfile.mkstemp(prefix=".write-probe.", dir=state_dir)
        os.close(descriptor)
        Path(probe).unlink()
    except OSError as error:
        raise InstallError(f"V23 state directory is not writable: {state_dir}") from error


def _safe_state_dir(state_dir: Path) -> Path:
    """Reject a state path or nearest existing parent that redirects through a symlink."""
    candidate = Path(os.path.abspath(state_dir.expanduser()))
    cursor = candidate
    while not cursor.exists() and not cursor.is_symlink() and cursor != cursor.parent:
        cursor = cursor.parent
    if cursor.is_symlink():
        raise InstallError(f"refusing symlink in V23 state path: {cursor}")
    return candidate.resolve(strict=False)


def _validate_agent_content(content: str, name: str, model: str, effort: str) -> None:
    """Reject a locally configured template result Codex could not load."""
    try:
        agent = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise InstallError(f"invalid rendered {name} agent TOML: {error}") from error
    for field in ("name", "description", "developer_instructions"):
        if not isinstance(agent.get(field), str) or not agent[field].strip():
            raise InstallError(f"rendered {name} agent is missing {field}")
    expected = {"name": name, "model": model, "model_reasoning_effort": effort}
    if any(agent.get(field) != value for field, value in expected.items()):
        raise InstallError(f"rendered {name} agent does not match local configuration")


def install(repo_root: Path, codex_home: Path, local_config: Path, state_dir: Path) -> None:
    """Install V23 into an empty or previously V23-managed local area."""
    repo_root, codex_home = repo_root.resolve(), codex_home.resolve()
    state_dir = _safe_state_dir(state_dir)
    _prepare_state_dir(state_dir)
    config = read_toml(local_config)
    runtime_python = resolve_python_runtime(config)
    manifest = _load_manifest(state_dir)
    agents_path, codex_config = active_global_agents(codex_home), codex_home / "config.toml"
    if manifest is not None and manifest.get("agents_path") != str(agents_path):
        raise InstallError(
            "active global instruction path changed; uninstall the intact V23 installation before reinstalling"
        )
    portable = (repo_root / "package/global-portable.md").read_text().strip()
    opening = config.get("opening", {})
    if not isinstance(opening, dict):
        raise InstallError("[opening] must be a TOML table")
    local = opening.get("instruction")
    if not isinstance(local, str) or not local.strip():
        raise InstallError("[opening].instruction must contain the local-only opening rule")
    local = local.strip()
    for path in (agents_path, codex_config):
        if path.is_symlink():
            raise InstallError(f"refusing symlink managed file: {path}")
    agent_text = agents_path.read_text() if agents_path.exists() else ""
    config_text = codex_config.read_text() if codex_config.exists() else ""
    block_body(agent_text, PORTABLE_KIND)
    block_body(agent_text, LOCAL_KIND)
    block_body(config_text, CONFIG_KIND)
    assets = _assets(repo_root, codex_home, config)
    bootstrap_path = codex_home / "harness/v23/task_bootstrap.py"
    config_block = _config_block(runtime_python, bootstrap_path, local_config.resolve())
    old_assets = _old_assets(manifest)
    for asset in assets:
        _check_asset_parents(codex_home, asset)
        _check_asset_target(asset, old_assets)
    # Complete all safe checks before making a single local mutation.
    rendered_agents = replace_managed_block(
        replace_managed_block(agent_text, PORTABLE_KIND, portable), LOCAL_KIND, local
    )
    rendered_config = replace_managed_block(config_text, CONFIG_KIND, config_block)
    try:
        tomllib.loads(rendered_config)
    except tomllib.TOMLDecodeError as error:
        raise InstallError(f"refusing invalid rendered Codex config: {error}") from error
    atomic_write(agents_path, rendered_agents)
    atomic_write(codex_config, rendered_config)
    for asset in assets:
        asset.path.parent.mkdir(parents=True, exist_ok=True)
        _copy_asset(asset)
    record = {
        "version": VERSION,
        "agents_path": str(agents_path),
        "portable_digest": sha256_bytes(portable.encode()),
        "local_digest": sha256_bytes(local.encode()),
        "config_digest": sha256_bytes(config_block.encode()),
        "assets": [
            {"path": str(asset.path), "digest": digest_path(asset.path), "kind": asset.kind}
            for asset in assets
        ],
    }
    atomic_write(state_dir / MANIFEST_NAME, json.dumps(record, indent=2, sort_keys=True) + "\n")


def uninstall(codex_home: Path, state_dir: Path) -> list[str]:
    """Remove only unedited V23 artifacts and return a human-readable report."""
    codex_home = codex_home.resolve()
    state_dir = _safe_state_dir(state_dir)
    manifest = _load_manifest(state_dir)
    if manifest is None:
        raise InstallError("no V23 manifest found; refusing unscoped removal")
    agents_path = Path(manifest["agents_path"])
    config_path = codex_home / "config.toml"
    blockers: list[str] = []
    if not ensure_within(codex_home, agents_path).is_file() or agents_path.is_symlink():
        blockers.append(f"managed instruction file is absent or unsafe: {agents_path}")
        agent_text = ""
    else:
        agent_text = agents_path.read_text()
        for kind, digest in (
            (PORTABLE_KIND, manifest["portable_digest"]),
            (LOCAL_KIND, manifest["local_digest"]),
        ):
            if (
                block_body(agent_text, kind) is None
                or sha256_bytes(block_body(agent_text, kind).encode()) != digest
            ):
                blockers.append(f"managed {kind.lower()} block was edited or is absent")
    if not config_path.is_file() or config_path.is_symlink():
        blockers.append(f"managed Codex config is absent or unsafe: {config_path}")
        config_text = ""
    else:
        config_text = config_path.read_text()
        config_body = block_body(config_text, CONFIG_KIND)
        if config_body is None or sha256_bytes(config_body.encode()) != manifest["config_digest"]:
            blockers.append("managed config block was edited or is absent")
    assets = [ensure_within(codex_home, Path(entry["path"])) for entry in manifest["assets"]]
    for entry, path in zip(manifest["assets"], assets):
        if not path.exists() or path.is_symlink() or digest_path(path) != entry["digest"]:
            blockers.append(f"managed asset was edited or is absent: {path}")
    if blockers:
        return ["preserved entire V23 installation: " + "; ".join(blockers)]
    # All dependent assets and registrations are intact. Remove them as one
    # logical unit so no registration can point at a deleted V23 agent.
    agent_text, _ = remove_managed_block(agent_text, PORTABLE_KIND, manifest["portable_digest"])
    agent_text, _ = remove_managed_block(agent_text, LOCAL_KIND, manifest["local_digest"])
    config_text, _ = remove_managed_block(config_text, CONFIG_KIND, manifest["config_digest"])
    atomic_write(agents_path, agent_text)
    atomic_write(config_path, config_text)
    for path in assets:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    (state_dir / MANIFEST_NAME).unlink()
    try:
        state_dir.rmdir()
    except OSError:
        pass
    return [f"removed V23 installation from {codex_home}"]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("install", "uninstall"))
    result.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    result.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    result.add_argument(
        "--local-config", type=Path, default=Path.home() / ".config/codex-harness/local.toml"
    )
    result.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".local/state/codex-harness"
    )
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "install":
            install(args.repo_root, args.codex_home, args.local_config, args.state_dir)
            print("Codex Harness Infra V23 installed.")
        else:
            for line in uninstall(args.codex_home, args.state_dir):
                print(line)
    except InstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
