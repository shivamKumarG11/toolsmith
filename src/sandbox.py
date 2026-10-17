"""Drive a CreateOS micro-VM sandbox through the CLI.

One sandbox is reused across a session (the free plan allows 1 at a time and 10
creations per 24h). Egress is locked to an allowlist; anything not listed is
dropped in-kernel on the host and cannot be changed from inside the VM.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from . import config


class SandboxError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    env = {"CREATEOS_API_KEY": config.require("CREATEOS_API_KEY", config.CREATEOS_API_KEY)}
    import os

    full_env = {**os.environ, **env, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [config.CREATEOS_BIN, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


@dataclass
class ExecResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class Sandbox:
    def __init__(self, sid: str, name: str):
        self.id = sid
        self.name = name

    # ---- lifecycle ----
    @classmethod
    def create(
        cls,
        name: str,
        shape: str = "s-1vcpu-1gb",
        rootfs: str = "devbox:1",
        egress: list[str] | None = None,
        env: dict[str, str] | None = None,
        auto_pause: str = "15m",
    ) -> "Sandbox":
        args = ["sandbox", "create", "--shape", shape, "--name", name,
                "--rootfs", rootfs, "--auto-pause", auto_pause]
        for host in (egress or []):
            args += ["--egress", host]
        for k, v in (env or {}).items():
            args += ["--env", f"{k}={v}"]
        proc = _run(args)
        if proc.returncode != 0:
            raise SandboxError(f"create failed: {proc.stderr or proc.stdout}")
        data = _parse_json(proc.stdout)
        return cls(data["id"], data.get("name", name))

    def rm(self) -> None:
        _run(["sandbox", "rm", self.id, "--force"])

    # ---- egress ----
    def set_egress(self, hosts: list[str]) -> None:
        """Replace the allowlist. Applies live to the running sandbox."""
        args = ["sandbox", "firewall", "set", self.id]
        for host in hosts:
            args += ["--egress", host]
        proc = _run(args)
        if proc.returncode != 0:
            raise SandboxError(f"firewall set failed: {proc.stderr or proc.stdout}")

    # ---- files + exec ----
    def push(self, local: str, remote: str) -> None:
        proc = _run(["sandbox", "push", self.id, local, remote])
        if proc.returncode != 0:
            raise SandboxError(f"push failed: {proc.stderr or proc.stdout}")

    def pull(self, remote: str, local: str) -> None:
        proc = _run(["sandbox", "pull", self.id, remote, local])
        if proc.returncode != 0:
            raise SandboxError(f"pull failed: {proc.stderr or proc.stdout}")

    def exec(self, command: str, timeout: int = 180, env: dict[str, str] | None = None) -> ExecResult:
        prefix = ""
        if env:
            prefix = "".join(f"export {k}={_shq(v)}; " for k, v in env.items())
        proc = _run(["sandbox", "exec", self.id, "--", "bash", "-lc", prefix + command], timeout=timeout)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)


def _shq(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _parse_json(text: str) -> dict:
    # The CLI prints a banner before JSON sometimes; find the first brace.
    start = text.find("{")
    if start < 0:
        raise SandboxError(f"no JSON in output: {text[:200]}")
    return json.loads(text[start:])
