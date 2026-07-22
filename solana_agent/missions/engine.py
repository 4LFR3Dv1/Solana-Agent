from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solana_agent.authority import ApprovalService, PolicyEngine, PolicyProfile, redact_mapping
from solana_agent.contracts import (
    ApprovalStatus,
    CommandRecord,
    CommandStatus,
    MissionDefinition,
    MissionPack,
    MissionStepDefinition,
    MissionStepRecord,
    MissionStepStatus,
    PolicyContext,
    RunStatus,
    RuntimeContract,
    hash_payload,
)
from solana_agent.execution import CommandJournal, CommandSpec, Executor
from solana_agent.storage import JournalRepository

from .graph import topological_steps, validate_mission_graph
from .templates import TemplateResolutionError, resolve_value


class MissionExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MissionOutcome:
    run_id: str
    status: str
    steps: tuple[MissionStepRecord, ...]
    executed_steps: tuple[str, ...] = ()
    skipped_steps: tuple[str, ...] = ()
    waiting_step: str | None = None


class MissionEngine:
    def __init__(
        self,
        repository: JournalRepository,
        approval_service: ApprovalService,
        executors: dict[str, Executor],
    ) -> None:
        self.repository = repository
        self.approval_service = approval_service
        self.executors = dict(executors)

    def start(
        self,
        pack: MissionPack,
        mission_id: str,
        *,
        inputs: dict[str, Any],
        runtime_contract: RuntimeContract,
        run_id: str | None = None,
    ) -> MissionOutcome:
        try:
            mission = pack.missions[mission_id]
        except KeyError as exc:
            raise MissionExecutionError(f"mission not found in pack: {mission_id}") from exc
        validate_mission_graph(mission)
        resolved_inputs = self._resolve_inputs(mission, inputs)
        journal = self._journal(runtime_contract)
        run = journal.create_run(
            mission_id=mission.id,
            run_id=run_id,
            metadata=self._run_metadata(pack, mission, resolved_inputs, runtime_contract),
        )
        self.repository.initialize_mission_steps(
            run.id,
            [(step.id, hash_payload(step.to_dict())) for step in mission.steps],
        )
        self.repository.set_run_status(run.id, RunStatus.RUNNING)
        return self._drive(mission, resolved_inputs, runtime_contract, run.id, journal)

    def resume(
        self,
        pack: MissionPack,
        run_id: str,
        *,
        inputs: dict[str, Any],
        runtime_contract: RuntimeContract,
    ) -> MissionOutcome:
        run = self.repository.require_run(run_id)
        try:
            mission = pack.missions[run.mission_id]
        except KeyError as exc:
            raise MissionExecutionError(f"mission no longer exists in pack: {run.mission_id}") from exc
        resolved_inputs = self._resolve_inputs(mission, inputs)
        self._verify_resume_compatibility(
            run.metadata,
            pack,
            mission,
            resolved_inputs,
            runtime_contract,
        )
        journal = self._journal(runtime_contract)
        journal.recover_orphaned_commands()
        self.repository.set_run_status(run.id, RunStatus.RUNNING)
        return self._drive(mission, resolved_inputs, runtime_contract, run.id, journal)

    def _drive(
        self,
        mission: MissionDefinition,
        inputs: dict[str, Any],
        runtime_contract: RuntimeContract,
        run_id: str,
        journal: CommandJournal,
    ) -> MissionOutcome:
        executed: list[str] = []
        skipped: list[str] = []
        for step in topological_steps(mission):
            record = self.repository.require_mission_step(run_id, step.id)
            if record.status == MissionStepStatus.SUCCEEDED:
                skipped.append(step.id)
                continue
            dependency_records = [
                self.repository.require_mission_step(run_id, dependency) for dependency in step.depends_on
            ]
            if any(
                dependency.status in {MissionStepStatus.FAILED, MissionStepStatus.BLOCKED}
                for dependency in dependency_records
            ):
                self._block_step(run_id, step.id, dependency_records)
                continue
            if any(dependency.status != MissionStepStatus.SUCCEEDED for dependency in dependency_records):
                return self._outcome(run_id, "waiting_dependency", executed, skipped, step.id)
            if record.status == MissionStepStatus.WAITING_APPROVAL:
                resumed = self._resume_approval(record, journal)
                if resumed is None:
                    return self._outcome(run_id, "waiting_approval", executed, skipped, step.id)
                self._finish_step(step, resumed, runtime_contract, inputs)
                executed.append(step.id)
                continue
            context = self._template_context(inputs, runtime_contract, run_id)
            try:
                self._check_preconditions(step, context)
                spec = self._command_spec(step, record, context)
            except (MissionExecutionError, TemplateResolutionError) as exc:
                self.repository.set_mission_step_status(
                    run_id,
                    step.id,
                    MissionStepStatus.FAILED,
                    error={"code": "precondition_failed", "message": str(exc)},
                )
                continue
            executor = self.executors.get(step.adapter)
            if executor is None:
                self.repository.set_mission_step_status(
                    run_id,
                    step.id,
                    MissionStepStatus.FAILED,
                    error={"code": "adapter_unavailable", "message": f"adapter is not registered: {step.adapter}"},
                )
                continue
            running = self.repository.set_mission_step_status(
                run_id,
                step.id,
                MissionStepStatus.RUNNING,
                increment_attempt=True,
            )
            outcome = journal.execute(
                spec,
                executor,
                policy_context=self._policy_context(runtime_contract),
            )
            if outcome.command.status == CommandStatus.APPROVAL_REQUIRED:
                self.repository.set_mission_step_status(
                    run_id,
                    step.id,
                    MissionStepStatus.WAITING_APPROVAL,
                    command_id=outcome.command.id,
                    result={"attempt": running.attempt},
                )
                return self._outcome(run_id, "waiting_approval", executed, skipped, step.id)
            self._finish_step(step, outcome.command, runtime_contract, inputs)
            executed.append(step.id)

        steps = tuple(self.repository.list_mission_steps(run_id))
        failed = any(step.status in {MissionStepStatus.FAILED, MissionStepStatus.BLOCKED} for step in steps)
        final_status = RunStatus.FAILED if failed else RunStatus.COMPLETED
        self.repository.set_run_status(run_id, final_status)
        return MissionOutcome(
            run_id=run_id,
            status=final_status.value,
            steps=steps,
            executed_steps=tuple(executed),
            skipped_steps=tuple(skipped),
        )

    def _resume_approval(self, record: MissionStepRecord, journal: CommandJournal) -> CommandRecord | None:
        if record.command_id is None:
            raise MissionExecutionError(f"waiting step has no command: {record.step_id}")
        command = self.repository.require_command(record.command_id)
        if command.approval_id is None:
            raise MissionExecutionError(f"waiting command has no approval: {record.command_id}")
        approval = self.repository.require_approval(command.approval_id)
        if approval.status == ApprovalStatus.PENDING:
            return None
        executor = self.executors.get(command.adapter)
        if executor is None:
            raise MissionExecutionError(f"adapter is not registered: {command.adapter}")
        return journal.execute_approved(command.id, executor).command

    def _finish_step(
        self,
        step: MissionStepDefinition,
        command: CommandRecord,
        runtime_contract: RuntimeContract,
        inputs: dict[str, Any],
    ) -> MissionStepRecord:
        if command.status != CommandStatus.SUCCEEDED:
            return self.repository.set_mission_step_status(
                command.run_id,
                step.id,
                MissionStepStatus.FAILED,
                command_id=command.id,
                error=command.error or {"code": "command_failed", "message": command.status.value},
            )
        try:
            self._check_acceptance(step, command, self._template_context(inputs, runtime_contract, command.run_id))
        except (MissionExecutionError, TemplateResolutionError) as exc:
            return self.repository.set_mission_step_status(
                command.run_id,
                step.id,
                MissionStepStatus.FAILED,
                command_id=command.id,
                error={"code": "acceptance_failed", "message": str(exc)},
            )
        return self.repository.set_mission_step_status(
            command.run_id,
            step.id,
            MissionStepStatus.SUCCEEDED,
            command_id=command.id,
            result={"command_status": command.status.value, "result": command.result},
        )

    def _block_step(
        self,
        run_id: str,
        step_id: str,
        dependencies: list[MissionStepRecord],
    ) -> None:
        self.repository.set_mission_step_status(
            run_id,
            step_id,
            MissionStepStatus.BLOCKED,
            error={
                "code": "dependency_failed",
                "message": "one or more dependencies did not succeed",
                "dependencies": {dependency.step_id: dependency.status.value for dependency in dependencies},
            },
        )

    def _check_preconditions(self, step: MissionStepDefinition, context: dict[str, Any]) -> None:
        for check in step.preconditions:
            parameters = resolve_value(check.parameters, context)
            if check.type == "input_present":
                name = str(parameters["name"])
                value = context["inputs"].get(name)
                if value is None or value == "":
                    raise MissionExecutionError(f"required input is missing: {name}")
            elif check.type in {"path_exists", "path_not_exists"}:
                path = self._resolve_path(parameters["path"], context)
                expected = check.type == "path_exists"
                if path.exists() != expected:
                    raise MissionExecutionError(f"precondition {check.type} failed: {path}")
            elif check.type == "equals":
                if parameters.get("actual") != parameters.get("expected"):
                    raise MissionExecutionError(f"precondition equals failed for step {step.id}")
            else:
                raise MissionExecutionError(f"unsupported precondition type: {check.type}")

    def _check_acceptance(
        self,
        step: MissionStepDefinition,
        command: CommandRecord,
        context: dict[str, Any],
    ) -> None:
        checks = step.acceptance or ()
        for check in checks:
            parameters = resolve_value(check.parameters, context)
            if check.type == "command_succeeded":
                if command.status != CommandStatus.SUCCEEDED:
                    raise MissionExecutionError(f"command did not succeed for step {step.id}")
            elif check.type == "exit_code_equals":
                expected = int(parameters.get("value", 0))
                if command.exit_code != expected:
                    raise MissionExecutionError(f"expected exit code {expected}, got {command.exit_code}")
            elif check.type == "result_equals":
                actual = self._nested(command.result or {}, str(parameters["path"]))
                if actual != parameters.get("expected"):
                    raise MissionExecutionError(f"result acceptance failed at {parameters['path']}: {actual!r}")
            elif check.type == "path_exists":
                path = self._resolve_path(parameters["path"], context)
                if not path.exists():
                    raise MissionExecutionError(f"accepted path does not exist: {path}")
            elif check.type == "artifact_kind_exists":
                kind = str(parameters["kind"])
                if kind not in {artifact.kind for artifact in self.repository.list_artifacts(command.id)}:
                    raise MissionExecutionError(f"required artifact kind is missing: {kind}")
            else:
                raise MissionExecutionError(f"unsupported acceptance type: {check.type}")

    def _command_spec(
        self,
        step: MissionStepDefinition,
        record: MissionStepRecord,
        context: dict[str, Any],
    ) -> CommandSpec:
        next_attempt = record.attempt + 1
        return CommandSpec(
            run_id=record.run_id,
            step_id=step.id,
            adapter=step.adapter,
            operation=step.operation,
            arguments=resolve_value(step.arguments, context),
            cwd=str(resolve_value(step.cwd, context)),
            timeout_seconds=step.timeout_seconds,
            expected_state=f"mission_step:{step.id}:succeeded",
            idempotency_key=hash_payload(
                {
                    "run_id": record.run_id,
                    "step_id": step.id,
                    "definition_hash": record.definition_hash,
                    "attempt": next_attempt,
                }
            ),
        )

    def _journal(self, runtime_contract: RuntimeContract) -> CommandJournal:
        return CommandJournal(
            self.repository,
            policy_engine=PolicyEngine(PolicyProfile(runtime_contract.policy_profile)),
            approval_service=self.approval_service,
        )

    @staticmethod
    def _policy_context(runtime_contract: RuntimeContract) -> PolicyContext:
        return PolicyContext(
            workspace_root=runtime_contract.workspace_root,
            cluster=runtime_contract.cluster,
            wallet=runtime_contract.wallet,
            max_lamports=runtime_contract.max_lamports,
            runtime_contract_hash=runtime_contract.contract_hash,
        )

    def _template_context(
        self,
        inputs: dict[str, Any],
        runtime_contract: RuntimeContract,
        run_id: str,
    ) -> dict[str, Any]:
        steps = {
            record.step_id: {
                "status": record.status.value,
                "attempt": record.attempt,
                "result": record.result or {},
            }
            for record in self.repository.list_mission_steps(run_id)
        }
        return {"inputs": inputs, "runtime": runtime_contract.to_dict(), "steps": steps}

    @staticmethod
    def _resolve_inputs(mission: MissionDefinition, supplied: dict[str, Any]) -> dict[str, Any]:
        known = {item.name for item in mission.inputs}
        unknown = set(supplied) - known
        if unknown:
            raise MissionExecutionError(f"unknown mission inputs: {sorted(unknown)}")
        resolved: dict[str, Any] = {}
        for definition in mission.inputs:
            value = supplied.get(definition.name, definition.default)
            if definition.required and value is None:
                raise MissionExecutionError(f"required mission input is missing: {definition.name}")
            resolved[definition.name] = value
        return redact_mapping(resolved).value

    @staticmethod
    def _run_metadata(
        pack: MissionPack,
        mission: MissionDefinition,
        inputs: dict[str, Any],
        runtime_contract: RuntimeContract,
    ) -> dict[str, Any]:
        return {
            "mission_definition": {
                "id": mission.id,
                "version": mission.version,
                "hash": mission.definition_hash,
            },
            "mission_pack": {"id": pack.id, "version": pack.version, "hash": pack.pack_hash},
            "runtime_contract": {
                "snapshot": runtime_contract.to_dict(),
                "hash": runtime_contract.contract_hash,
            },
            "inputs": inputs,
            "inputs_hash": hash_payload(inputs),
        }

    @staticmethod
    def _verify_resume_compatibility(
        metadata: dict[str, Any],
        pack: MissionPack,
        mission: MissionDefinition,
        inputs: dict[str, Any],
        runtime_contract: RuntimeContract,
    ) -> None:
        expected = {
            "mission": mission.definition_hash,
            "pack": pack.pack_hash,
            "runtime": runtime_contract.contract_hash,
            "inputs": hash_payload(inputs),
        }
        actual = {
            "mission": metadata.get("mission_definition", {}).get("hash"),
            "pack": metadata.get("mission_pack", {}).get("hash"),
            "runtime": metadata.get("runtime_contract", {}).get("hash"),
            "inputs": metadata.get("inputs_hash"),
        }
        if actual != expected:
            raise MissionExecutionError(f"resume contract mismatch: expected {expected}, persisted {actual}")

    def _outcome(
        self,
        run_id: str,
        status: str,
        executed: list[str],
        skipped: list[str],
        waiting_step: str,
    ) -> MissionOutcome:
        return MissionOutcome(
            run_id=run_id,
            status=status,
            steps=tuple(self.repository.list_mission_steps(run_id)),
            executed_steps=tuple(executed),
            skipped_steps=tuple(skipped),
            waiting_step=waiting_step,
        )

    @staticmethod
    def _nested(value: dict[str, Any], path: str) -> Any:
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise MissionExecutionError(f"result path not found: {path}")
            current = current[part]
        return current

    @staticmethod
    def _resolve_path(value: Any, context: dict[str, Any]) -> Path:
        path = Path(str(value))
        if path.is_absolute():
            return path.resolve()
        return (Path(str(context["runtime"]["workspace_root"])) / path).resolve()
