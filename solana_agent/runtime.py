from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug_to_snake(value: str) -> str:
    return value.replace("-", "_")


def slug_to_camel(value: str) -> str:
    parts = value.replace("_", "-").split("-")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def ensure_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RuntimeErrorWithContext(RuntimeError):
    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.message,
            "context": self.context,
        }


@dataclass
class CommandResult:
    args: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_record(self) -> dict[str, Any]:
        return {
            "args": self.args,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class CommandExecutor:
    def __init__(self, shell_mode: str, wsl_distro: str | None = None, wsl_user: str | None = None) -> None:
        self.shell_mode = self._resolve_shell_mode(shell_mode)
        self.wsl_distro = wsl_distro or os.environ.get("SOLANA_AGENT_WSL_DISTRO", "Ubuntu")
        self.wsl_user = wsl_user or os.environ.get("SOLANA_AGENT_WSL_USER", "foundry")

    def _resolve_shell_mode(self, shell_mode: str) -> str:
        if shell_mode != "auto":
            return shell_mode
        if os.name == "nt":
            if not self.has_wsl_distribution():
                raise RuntimeErrorWithContext(
                    "WSL has no installed Linux distribution. Install Ubuntu in WSL or pass --shell-mode native if you have a separate bash runtime.",
                    suggested_commands=[
                        "wsl.exe --list --online",
                        "wsl.exe --install Ubuntu",
                    ],
                )
            return "wsl"
        return "native"

    def has_wsl_distribution(self) -> bool:
        if os.name != "nt":
            return False
        completed = subprocess.run(
            ["wsl.exe", "--list", "--quiet"],
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0 and bool(completed.stdout.strip())

    def to_wsl_path(self, path: Path) -> str:
        resolved = path.resolve()
        drive = resolved.drive.rstrip(":").lower()
        suffix = resolved.as_posix().split(":", 1)[1]
        return f"/mnt/{drive}{suffix}"

    def run(self, args: list[str], cwd: Path) -> CommandResult:
        if self.shell_mode == "wsl":
            return self._run_wsl(args, cwd)
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
        return CommandResult(
            args=args,
            cwd=str(cwd),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _run_wsl(self, args: list[str], cwd: Path) -> CommandResult:
        import shlex

        bootstrap = (
            'export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:'
            '$HOME/.avm/bin:$HOME/.local/bin:$HOME/.nvm/versions/node/v24.10.0/bin:$PATH"; '
            'export NVM_DIR="$HOME/.nvm"; '
            '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true; '
            '[ -f "$HOME/.profile" ] && . "$HOME/.profile" >/dev/null 2>&1 || true; '
        )
        script = f"{bootstrap}cd {shlex.quote(self.to_wsl_path(cwd))} && {shlex.join(args)}"
        completed = subprocess.run(
            ["wsl", "-d", self.wsl_distro, "-u", self.wsl_user, "--", "bash", "-lc", script],
            text=True,
            capture_output=True,
            check=False,
        )
        return CommandResult(
            args=args,
            cwd=str(cwd),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class MissionRunner:
    def __init__(
        self,
        repo_root: Path,
        shell_mode: str = "auto",
        wsl_distro: str | None = None,
        wsl_user: str | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.state_root = self.repo_root / ".solana-agent"
        self.executor = CommandExecutor(shell_mode=shell_mode, wsl_distro=wsl_distro, wsl_user=wsl_user)
        self._ensure_state_dirs()

    def _ensure_state_dirs(self) -> None:
        for name in ["sessions", "deployments", "wallets", "artifacts", "approvals", "runs"]:
            (self.state_root / name).mkdir(parents=True, exist_ok=True)

    def inspect_env(self) -> dict[str, Any]:
        result = self._run_script("scripts/solana/check_env.sh", [], self.repo_root)
        parsed = self._parse_json(result.stdout, "check_env")
        parsed["shell_mode"] = self.executor.shell_mode
        parsed["wsl_distro"] = self.executor.wsl_distro if self.executor.shell_mode == "wsl" else ""
        parsed["wsl_user"] = self.executor.wsl_user if self.executor.shell_mode == "wsl" else ""
        return parsed

    def run_create_counter(
        self,
        *,
        workspace: Path,
        project_name: str | None,
        cluster: str,
        airdrop_amount: str,
        approve_airdrop: bool,
        approve_deploy: bool,
        skip_airdrop: bool,
        skip_deploy: bool,
    ) -> dict[str, Any]:
        workspace = workspace.resolve()
        project_name = project_name or workspace.name
        program_slug = project_name
        program_snake = slug_to_snake(project_name)
        program_camel = slug_to_camel(project_name)

        mission_id = f"create-counter-{uuid.uuid4().hex[:8]}"
        session_id = f"session-{uuid.uuid4().hex[:8]}"
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        mission_record = {
            "id": mission_id,
            "type": "create-counter",
            "goal": "Scaffold, test, deploy, invoke, and capture evidence for an Anchor counter.",
            "status": "running",
            "workspace_path": str(workspace),
            "cluster": cluster,
            "inputs": {
                "project_name": project_name,
                "airdrop_amount": airdrop_amount,
                "approve_airdrop": approve_airdrop,
                "approve_deploy": approve_deploy,
                "skip_airdrop": skip_airdrop,
                "skip_deploy": skip_deploy,
            },
        }
        session_record = {
            "id": session_id,
            "created_at": utc_now(),
            "mission_id": mission_id,
            "status": "running",
            "workspace_path": str(workspace),
            "cluster": cluster,
        }
        run_record = {
            "id": run_id,
            "session_id": session_id,
            "started_at": utc_now(),
            "status": "running",
            "commands": [],
            "artifacts": [],
        }

        self._write_state("sessions", f"{session_id}.json", session_record)
        self._write_state("runs", f"{run_id}.json", run_record)

        approvals: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        deployment: dict[str, Any] | None = None

        try:
            env_report = self.inspect_env()
            self._record_command(run_record, self._last_script_result)
            wallet_report = self._wallet_info(cluster)

            if not env_report.get("ready"):
                raise RuntimeErrorWithContext(
                    "Environment is not ready for the create-counter mission.",
                    environment=env_report,
                )

            if wallet_report.get("cluster") and cluster not in wallet_report["cluster"]:
                raise RuntimeErrorWithContext(
                    "Active Solana cluster does not match the requested mission cluster.",
                    expected_cluster=cluster,
                    actual_cluster=wallet_report.get("cluster"),
                )

            self._record_command(run_record, self._last_script_result)

            if workspace.exists():
                raise RuntimeErrorWithContext(
                    "Target workspace already exists. Use a new path for the MVP scaffold.",
                    workspace=str(workspace),
                )

            if approve_airdrop and not skip_airdrop:
                approval = self._approval_record("airdrop-devnet", True, note=f"Requested {airdrop_amount} SOL")
                approvals.append(approval)
                self._write_state("approvals", f"{approval['id']}.json", approval)
                airdrop = self._run_script(
                    "scripts/solana/devnet_airdrop.sh",
                    [airdrop_amount],
                    self.repo_root,
                )
                self._record_command(run_record, airdrop)
                airdrop_payload = self._parse_json(airdrop.stdout, "airdrop")
                artifact = self._artifact_record("airdrop", airdrop_payload)
                artifacts.append(artifact)
                self._write_artifact_payload(artifact, airdrop_payload)

            self._scaffold_anchor_workspace(workspace, project_name)
            self._record_command(run_record, self._last_command_result)
            self._apply_counter_template(
                workspace,
                program_slug,
                program_snake,
                program_camel,
                cluster,
                wallet_report.get("keypair_path", "").strip(),
            )
            self._record_command(run_record, self._last_command_result)

            install_result = self.executor.run(["yarn", "install"], cwd=workspace)
            self._require_success(install_result, "yarn install failed")
            self._record_command(run_record, install_result)

            test_result = self.executor.run(["anchor", "test", "--skip-local-validator"], cwd=workspace)
            self._require_success(test_result, "anchor test failed")
            self._record_command(run_record, test_result)
            test_artifact = self._artifact_record(
                "test-log",
                {"workspace_path": str(workspace), "output": test_result.stdout, "errors": test_result.stderr},
            )
            artifacts.append(test_artifact)
            self._write_artifact_payload(
                test_artifact,
                {"stdout": test_result.stdout, "stderr": test_result.stderr},
            )

            if not skip_deploy:
                if not approve_deploy:
                    raise RuntimeErrorWithContext(
                        "Deploy approval is required for the create-counter mission.",
                        required_approval="deploy-devnet",
                    )
                approval = self._approval_record("deploy-devnet", True, note=f"Deploy {project_name} to {cluster}")
                approvals.append(approval)
                self._write_state("approvals", f"{approval['id']}.json", approval)

                deploy_script = self._run_script(
                    "scripts/solana/deploy_anchor.sh",
                    [str(workspace), project_name],
                    self.repo_root,
                )
                self._record_command(run_record, deploy_script)
                deploy_payload = self._parse_json(deploy_script.stdout, "deploy")

                deployment = {
                    "program_id": deploy_payload.get("program_id"),
                    "cluster": cluster,
                    "wallet_address": wallet_report.get("wallet_address"),
                    "deploy_signature": deploy_payload.get("deploy_signature", ""),
                    "explorer_link": self._tx_link(deploy_payload.get("deploy_signature", ""), cluster),
                    "idl_path": str(workspace / "target" / "idl" / f"{program_snake}.json"),
                    "workspace_path": str(workspace),
                    "created_at": utc_now(),
                }
                self._write_state("deployments", f"{mission_id}.json", deployment)

                invoke_result = self.executor.run(["yarn", "interact:devnet"], cwd=workspace)
                self._require_success(invoke_result, "invoke flow failed")
                self._record_command(run_record, invoke_result)
                invoke_payload = self._parse_interaction_output(invoke_result.stdout, cluster)
                invoke_artifact = self._artifact_record("invoke", invoke_payload)
                artifacts.append(invoke_artifact)
                self._write_artifact_payload(invoke_artifact, invoke_payload)

                evidence_pack = self._render_evidence_pack(
                    mission_id=mission_id,
                    project_name=project_name,
                    cluster=cluster,
                    wallet_address=wallet_report.get("wallet_address", ""),
                    deployment=deployment,
                    invoke_payload=invoke_payload,
                )
                evidence_artifact = self._artifact_record("evidence-pack", {"path": str(evidence_pack)})
                artifacts.append(evidence_artifact)

            mission_record["status"] = "completed"
            session_record["status"] = "completed"
            run_record["status"] = "completed"
            run_record["finished_at"] = utc_now()
            run_record["artifacts"] = [artifact["path"] for artifact in artifacts]

            self._write_state("sessions", f"{session_id}.json", session_record)
            self._write_state("runs", f"{run_id}.json", run_record)

            return {
                "ok": True,
                "mission": mission_record,
                "session": session_record,
                "run": run_record,
                "environment": env_report,
                "wallet": wallet_report,
                "approvals": approvals,
                "artifacts": artifacts,
                "deployment": deployment,
            }
        except RuntimeErrorWithContext:
            mission_record["status"] = "failed"
            session_record["status"] = "failed"
            run_record["status"] = "failed"
            run_record["finished_at"] = utc_now()
            self._write_state("sessions", f"{session_id}.json", session_record)
            self._write_state("runs", f"{run_id}.json", run_record)
            raise

    def _run_script(self, relative_path: str, args: list[str], cwd: Path) -> CommandResult:
        script_path = self.repo_root / relative_path
        script_arg = self._convert_for_shell(script_path)
        converted_args = [self._convert_for_shell(arg) for arg in args]
        result = self.executor.run(["bash", script_arg, *converted_args], cwd=cwd)
        self._last_script_result = result
        self._require_success(result, f"Script failed: {relative_path}")
        return result

    def _wallet_info(self, cluster: str) -> dict[str, Any]:
        result = self._run_script("scripts/solana/wallet_info.sh", [], self.repo_root)
        payload = self._parse_json(result.stdout, "wallet_info")
        if not payload.get("wallet_address"):
            raise RuntimeErrorWithContext("No active Solana wallet is configured.", cluster=cluster)
        return payload

    def _scaffold_anchor_workspace(self, workspace: Path, project_name: str) -> None:
        parent = workspace.parent
        parent.mkdir(parents=True, exist_ok=True)
        result = self.executor.run(["anchor", "init", project_name], cwd=parent)
        self._last_command_result = result
        self._require_success(result, "anchor init failed")

    def _apply_counter_template(
        self,
        workspace: Path,
        program_slug: str,
        program_snake: str,
        program_camel: str,
        cluster: str,
        keypair_path: str,
    ) -> None:
        lib_template = read_text(
            self.repo_root / "templates" / "anchor-counter" / "files" / "programs" / "__PROGRAM_SLUG__" / "src" / "lib.rs.tmpl"
        )
        test_template = read_text(
            self.repo_root / "templates" / "anchor-counter" / "files" / "tests" / "__PROGRAM_SLUG__.ts.tmpl"
        )
        interact_template = read_text(
            self.repo_root / "templates" / "anchor-counter" / "files" / "scripts" / "interact.ts.tmpl"
        )
        package_template = read_text(
            self.repo_root / "templates" / "anchor-counter" / "files" / "package.json.tmpl"
        )
        readme_template = read_text(
            self.repo_root / "templates" / "anchor-counter" / "files" / "README.md.tmpl"
        )

        program_id = self._anchor_program_id(workspace, program_slug, program_snake)
        replacements = {
            "__PROGRAM_SLUG__": program_slug,
            "__PROGRAM_SNAKE__": program_snake,
            "__PROGRAM_CAMEL__": program_camel,
            "__PROGRAM_ID__": program_id,
            "__CLUSTER__": cluster,
        }

        ensure_text(
            workspace / "programs" / program_slug / "src" / "lib.rs",
            self._render_template(lib_template, replacements),
        )
        ensure_text(
            workspace / "tests" / f"{program_slug}.ts",
            self._render_template(test_template, replacements),
        )
        ensure_text(
            workspace / "scripts" / "interact.ts",
            self._render_template(interact_template, replacements),
        )
        ensure_text(
            workspace / "package.json",
            self._render_template(package_template, replacements),
        )
        ensure_text(
            workspace / "README.md",
            self._render_template(readme_template, replacements),
        )
        generated_tests = workspace / "programs" / program_slug / "tests"
        if generated_tests.exists():
            shutil.rmtree(generated_tests)
        self._update_anchor_toml(workspace, program_slug, program_id, cluster, keypair_path)
        self._last_command_result = CommandResult(
            args=["apply-counter-template", program_slug],
            cwd=str(workspace),
            exit_code=0,
            stdout=f"Applied counter template for {program_slug}",
            stderr="",
        )

    def _anchor_program_id(self, workspace: Path, program_slug: str, program_snake: str) -> str:
        result = self.executor.run(["anchor", "keys", "list"], cwd=workspace)
        self._last_command_result = result
        self._require_success(result, "anchor keys list failed")
        for line in result.stdout.splitlines():
            if line.startswith(f"{program_slug}:") or line.startswith(f"{program_snake}:"):
                return line.split(":", 1)[1].strip()
        raise RuntimeErrorWithContext(
            "Could not resolve program id from anchor keys list.",
            workspace=str(workspace),
            program_slug=program_slug,
            output=result.stdout,
        )

    def _update_anchor_toml(
        self,
        workspace: Path,
        program_slug: str,
        program_id: str,
        cluster: str,
        keypair_path: str,
    ) -> None:
        anchor_toml = workspace / "Anchor.toml"
        content = read_text(anchor_toml)
        content = re.sub(r'cluster\s*=\s*".*?"', f'cluster = "{cluster}"', content)
        if keypair_path:
            content = re.sub(r'wallet\s*=\s*".*?"', f'wallet = "{keypair_path}"', content)
        content = re.sub(
            r"\[programs\.[^\]]+\][\s\S]*?(?=\n\[|\Z)",
            f'[programs.{cluster}]\n{slug_to_snake(program_slug)} = "{program_id}"\n',
            content,
            count=1,
        )
        if "[programs." not in content:
            content += f'\n[programs.{cluster}]\n{slug_to_snake(program_slug)} = "{program_id}"\n'
        if '[scripts]' not in content:
            content += '\n[scripts]\ntest = "yarn ts-mocha -p ./tsconfig.json -t 1000000 tests/**/*.ts"\n'
        ensure_text(anchor_toml, content)

    def _parse_json(self, raw: str, label: str) -> dict[str, Any]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeErrorWithContext(
                f"Failed to parse JSON output from {label}.",
                output=raw,
                error=str(exc),
            ) from exc

    def _parse_interaction_output(self, raw: str, cluster: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "cluster": cluster,
            "raw_output": raw,
        }
        for line in raw.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key.lower()] = value.strip()
        increment_signature = data.get("increment_signature", "")
        initialize_signature = data.get("initialize_signature", "")
        program_counter = data.get("counter_pubkey", "")
        data["initialize_explorer"] = self._tx_link(initialize_signature, cluster)
        data["increment_explorer"] = self._tx_link(increment_signature, cluster)
        data["counter_explorer"] = self._address_link(program_counter, cluster)
        return data

    def _render_evidence_pack(
        self,
        *,
        mission_id: str,
        project_name: str,
        cluster: str,
        wallet_address: str,
        deployment: dict[str, Any],
        invoke_payload: dict[str, Any],
    ) -> Path:
        path = self.state_root / "artifacts" / f"{mission_id}-evidence.md"
        content = "\n".join(
            [
                f"# Evidence Pack: {project_name}",
                "",
                f"- Cluster: `{cluster}`",
                f"- Wallet Address: `{wallet_address}`",
                f"- Program ID: `{deployment.get('program_id', '')}`",
                f"- Deploy Transaction: `{deployment.get('explorer_link', '')}`",
                f"- Initialize Transaction: `{invoke_payload.get('initialize_explorer', '')}`",
                f"- Increment Transaction: `{invoke_payload.get('increment_explorer', '')}`",
                f"- Counter Address: `{invoke_payload.get('counter_pubkey', '')}`",
                f"- Counter Explorer: `{invoke_payload.get('counter_explorer', '')}`",
                f"- Final Count: `{invoke_payload.get('count', '')}`",
            ]
        )
        ensure_text(path, content + "\n")
        return path

    def _approval_record(self, action: str, approved: bool, note: str) -> dict[str, Any]:
        return {
            "id": f"approval-{uuid.uuid4().hex[:8]}",
            "action": action,
            "approved": approved,
            "approved_at": utc_now(),
            "approved_by": "local-user",
            "note": note,
        }

    def _artifact_record(self, artifact_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4().hex[:8]}"
        payload_path = payload.get("path")
        path = Path(payload_path) if payload_path else self.state_root / "artifacts" / f"{artifact_id}.json"
        return {
            "id": artifact_id,
            "type": artifact_type,
            "path": str(path),
            "created_at": utc_now(),
            "description": payload.get("path", artifact_type),
        }

    def _write_artifact_payload(self, artifact: dict[str, Any], payload: dict[str, Any]) -> None:
        ensure_text(Path(artifact["path"]), json.dumps(payload, indent=2) + "\n")

    def _write_state(self, category: str, filename: str, payload: dict[str, Any]) -> None:
        ensure_text(self.state_root / category / filename, json.dumps(payload, indent=2) + "\n")

    def _record_command(self, run_record: dict[str, Any], result: CommandResult) -> None:
        run_record["commands"].append(result.to_record())

    def _tx_link(self, signature: str, cluster: str) -> str:
        if not signature:
            return ""
        return f"https://explorer.solana.com/tx/{signature}?cluster={cluster}"

    def _address_link(self, address: str, cluster: str) -> str:
        if not address:
            return ""
        return f"https://explorer.solana.com/address/{address}?cluster={cluster}"

    def _render_template(self, content: str, replacements: dict[str, str]) -> str:
        rendered = content
        for key, value in replacements.items():
            rendered = rendered.replace(key, value)
        return rendered

    def _require_success(self, result: CommandResult, message: str) -> None:
        if result.ok:
            return
        raise RuntimeErrorWithContext(
            message,
            args=result.args,
            cwd=result.cwd,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _convert_for_shell(self, value: str | Path) -> str:
        if isinstance(value, Path):
            return self.executor.to_wsl_path(value) if self.executor.shell_mode == "wsl" else str(value)
        if self.executor.shell_mode == "wsl" and re.match(r"^[A-Za-z]:\\", value):
            return self.executor.to_wsl_path(Path(value))
        return value
