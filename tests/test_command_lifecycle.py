from __future__ import annotations

import pytest

from solana_agent.contracts.command import (
    CommandStatus,
    InvalidCommandTransition,
    is_terminal_command_status,
    require_command_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CommandStatus.PLANNED, CommandStatus.VALIDATING),
        (CommandStatus.VALIDATING, CommandStatus.AUTHORIZED),
        (CommandStatus.AUTHORIZED, CommandStatus.RUNNING),
        (CommandStatus.RUNNING, CommandStatus.SUCCEEDED),
        (CommandStatus.RUNNING, CommandStatus.FAILED),
    ],
)
def test_expected_command_transitions_are_allowed(current: CommandStatus, target: CommandStatus) -> None:
    require_command_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CommandStatus.PLANNED, CommandStatus.RUNNING),
        (CommandStatus.AUTHORIZED, CommandStatus.SUCCEEDED),
        (CommandStatus.SUCCEEDED, CommandStatus.RUNNING),
        (CommandStatus.REJECTED, CommandStatus.AUTHORIZED),
    ],
)
def test_invalid_command_transitions_fail_closed(current: CommandStatus, target: CommandStatus) -> None:
    with pytest.raises(InvalidCommandTransition):
        require_command_transition(current, target)


def test_terminal_statuses_are_explicit() -> None:
    assert is_terminal_command_status(CommandStatus.SUCCEEDED) is True
    assert is_terminal_command_status(CommandStatus.FAILED) is True
    assert is_terminal_command_status(CommandStatus.REJECTED) is True
    assert is_terminal_command_status(CommandStatus.RUNNING) is False
