from __future__ import annotations

from pathlib import Path

import pytest

from solana_agent.adapters import FilesystemAdapter
from solana_agent.execution import ExecutionRequest


def request(root: Path, operation: str, **arguments: object) -> ExecutionRequest:
    return ExecutionRequest("command", "filesystem", operation, dict(arguments), str(root), 5)


def test_filesystem_adapter_creates_reads_and_atomically_overwrites(tmp_path: Path) -> None:
    adapter = FilesystemAdapter(tmp_path)
    created = adapter.execute(request(tmp_path, "create_workspace", destination="project"))
    target = tmp_path / "project" / "README.md"
    written = adapter.execute(request(tmp_path, "overwrite", path=str(target), content="hello\n"))
    read = adapter.execute(request(tmp_path, "read", path=str(target)))

    assert created.exit_code == 0
    assert written.metadata["sha256"] == read.metadata["sha256"]
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_filesystem_adapter_denies_path_escape(tmp_path: Path) -> None:
    adapter = FilesystemAdapter(tmp_path)

    with pytest.raises(ValueError, match="escapes workspace root"):
        adapter.execute(request(tmp_path, "read", path="../outside"))
