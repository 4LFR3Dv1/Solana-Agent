from __future__ import annotations

import json

from solana_agent.doctor import host_doctor
from solana_agent.execution import ExecutionRequest, ExecutionResult


class DoctorAdapter:
    """Expose the reproducibility diagnostic through the governed executor API."""

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.operation != "inspect":
            raise ValueError(f"unsupported doctor operation: {request.operation}")
        report = host_doctor()
        toolchain = report.get("toolchain", {})
        compatible = bool(toolchain.get("compatible"))
        return ExecutionResult(
            exit_code=0 if compatible else 1,
            stdout=json.dumps(report, ensure_ascii=False, sort_keys=True),
            stderr="" if compatible else "host toolchain does not match toolchain.lock.json",
            metadata={"adapter": "doctor", "operation": "inspect", "compatible": compatible},
        )
