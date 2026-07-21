from __future__ import annotations

import pytest

from solana_agent.contracts import (
    InvalidMissionStepTransition,
    MissionStepStatus,
    require_mission_step_transition,
)


def test_mission_step_can_pause_for_approval_then_succeed() -> None:
    require_mission_step_transition(MissionStepStatus.PENDING, MissionStepStatus.RUNNING)
    require_mission_step_transition(MissionStepStatus.RUNNING, MissionStepStatus.WAITING_APPROVAL)
    require_mission_step_transition(MissionStepStatus.WAITING_APPROVAL, MissionStepStatus.SUCCEEDED)


def test_succeeded_mission_step_is_terminal() -> None:
    with pytest.raises(InvalidMissionStepTransition):
        require_mission_step_transition(MissionStepStatus.SUCCEEDED, MissionStepStatus.RUNNING)
