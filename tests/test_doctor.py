from __future__ import annotations

from solana_agent.doctor import _next_steps


def test_windows_without_wsl_has_actionable_install_step() -> None:
    steps = _next_steps("nt", {"installed": False}, [{"name": "node", "available": True}])

    assert steps == ["Install Ubuntu in WSL: wsl.exe --install Ubuntu"]


def test_windows_with_wsl_points_to_runtime_toolchain() -> None:
    steps = _next_steps("nt", {"installed": True}, [{"name": "node", "available": True}])

    assert any("Solana CLI" in step for step in steps)


def test_missing_node_is_reported_independently() -> None:
    steps = _next_steps("posix", {"installed": False}, [{"name": "node", "available": False}])

    assert steps == ["Install Node.js on the host if you want local helper tooling outside WSL"]
