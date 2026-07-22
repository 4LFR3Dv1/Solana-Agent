from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from solana_agent.execution import ExecutionRequest, ExecutionResult

PROGRAM_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
DECLARE_ID = re.compile(r'declare_id!\("([1-9A-HJ-NP-Za-km-z]{32,44})"\);')


class CounterTemplateAdapter:
    """Materialize the packaged counter template into an Anchor workspace."""

    def __init__(self, workspace_root: Path, template_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.template_root = template_root.resolve()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.operation != "apply":
            raise ValueError(f"unsupported counter template operation: {request.operation}")
        workspace = self._workspace(request.arguments.get("workspace"))
        project_name = str(request.arguments.get("project_name", ""))
        cluster = str(request.arguments.get("cluster", "devnet"))
        if not PROGRAM_NAME.fullmatch(project_name):
            raise ValueError("project_name must use lowercase letters, digits, and hyphens")
        if cluster not in {"devnet", "localnet", "localhost"}:
            raise ValueError("counter template cluster must be devnet, localnet, or localhost")
        if not workspace.is_dir() or not (workspace / "Anchor.toml").is_file():
            raise ValueError(f"not an Anchor workspace: {workspace}")

        program_slug = project_name
        program_snake = project_name.replace("-", "_")
        original_program = workspace / "programs" / program_slug / "src" / "lib.rs"
        if not original_program.is_file():
            raise ValueError(f"Anchor program source was not found: {original_program}")
        match = DECLARE_ID.search(original_program.read_text(encoding="utf-8"))
        if match is None:
            raise ValueError("generated Anchor source does not contain a valid declare_id")
        program_id = match.group(1)
        replacements = {
            "__PROGRAM_SLUG__": program_slug,
            "__PROGRAM_SNAKE__": program_snake,
            "__PROGRAM_CAMEL__": "".join(part.capitalize() for part in program_snake.split("_")),
            "__PROGRAM_ID__": program_id,
            "__CLUSTER__": cluster,
        }

        written: list[dict[str, Any]] = []
        for source in sorted(self.template_root.rglob("*.tmpl")):
            relative = source.relative_to(self.template_root)
            rendered_relative = str(relative)[:-5]
            for token, value in replacements.items():
                rendered_relative = rendered_relative.replace(token, value)
            destination = (workspace / rendered_relative).resolve()
            if workspace != destination and workspace not in destination.parents:
                raise ValueError(f"template destination escapes workspace: {destination}")
            content = source.read_text(encoding="utf-8")
            for token, value in replacements.items():
                content = content.replace(token, value)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8", newline="\n")
            written.append(
                {
                    "path": str(destination.relative_to(workspace)).replace("\\", "/"),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            )
        stale_lock = workspace / "pnpm-lock.yaml"
        lock_removed = stale_lock.is_file()
        stale_lock.unlink(missing_ok=True)
        if not written:
            raise ValueError(f"counter template has no files: {self.template_root}")
        return ExecutionResult(
            exit_code=0,
            stdout=f"Applied counter template for {program_id}\n",
            metadata={
                "program_id": program_id,
                "files": written,
                "file_count": len(written),
                "stale_lock_removed": lock_removed,
            },
        )

    def _workspace(self, value: Any) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("counter template requires workspace")
        candidate = Path(value)
        workspace = candidate.resolve() if candidate.is_absolute() else (self.workspace_root / candidate).resolve()
        if workspace != self.workspace_root and self.workspace_root not in workspace.parents:
            raise ValueError(f"workspace escapes configured root: {workspace}")
        return workspace
