from __future__ import annotations

from solana_agent.contracts.mission import MissionDefinition, MissionStepDefinition


class MissionGraphError(ValueError):
    pass


def validate_mission_graph(mission: MissionDefinition) -> None:
    if not mission.id.strip() or not mission.version.strip() or not mission.goal.strip():
        raise MissionGraphError("mission id, version, and goal must not be empty")
    step_ids = [step.id for step in mission.steps]
    if not step_ids:
        raise MissionGraphError("mission must define at least one step")
    if len(step_ids) != len(set(step_ids)):
        raise MissionGraphError("mission step ids must be unique")
    known = set(step_ids)
    for step in mission.steps:
        if not step.id.strip() or not step.adapter.strip() or not step.operation.strip():
            raise MissionGraphError("step id, adapter, and operation must not be empty")
        if step.timeout_seconds <= 0:
            raise MissionGraphError(f"step {step.id} timeout must be greater than zero")
        missing = set(step.depends_on) - known
        if missing:
            raise MissionGraphError(f"step {step.id} has unknown dependencies: {sorted(missing)}")
        if step.id in step.depends_on:
            raise MissionGraphError(f"step {step.id} cannot depend on itself")
    topological_steps(mission)


def topological_steps(mission: MissionDefinition) -> tuple[MissionStepDefinition, ...]:
    by_id = {step.id: step for step in mission.steps}
    indegree = {step.id: len(step.depends_on) for step in mission.steps}
    dependents: dict[str, list[str]] = {step.id: [] for step in mission.steps}
    order_index = {step.id: index for index, step in enumerate(mission.steps)}
    for step in mission.steps:
        for dependency in step.depends_on:
            if dependency in dependents:
                dependents[dependency].append(step.id)
    ready = sorted(
        (step_id for step_id, degree in indegree.items() if degree == 0),
        key=lambda step_id: order_index[step_id],
    )
    result: list[MissionStepDefinition] = []
    while ready:
        step_id = ready.pop(0)
        result.append(by_id[step_id])
        for dependent in sorted(dependents[step_id], key=lambda item: order_index[item]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=lambda item: order_index[item])
    if len(result) != len(mission.steps):
        cyclic = sorted(step_id for step_id, degree in indegree.items() if degree > 0)
        raise MissionGraphError(f"mission contains a dependency cycle: {cyclic}")
    return tuple(result)
