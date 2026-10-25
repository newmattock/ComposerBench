"""One-step registration of the ComposerBench MCP server with agent clients.

    composerbench mcp install --client all        # Claude Desktop, Claude Code, Cursor
    composerbench mcp install --client cursor --dry-run
    composerbench mcp config                      # print the server entry only
    composerbench mcp doctor                      # start the server and call it

Existing client configuration is merged, never replaced: other servers are
kept, the previous file is backed up next to itself, and running the command
twice is a no-op.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SERVER_NAME = "composerbench"
CLIENTS = ("claude-desktop", "claude-code", "cursor")


def client_config_path(client: str, home: Path | None = None) -> Path | None:
    home = home or Path.home()
    if client == "claude-desktop":
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if sys.platform.startswith("win"):
            return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    return None  # claude-code is configured through its own CLI


def _search_path() -> str:
    """PATH for GUI-launched servers, which otherwise miss Homebrew (ffmpeg)."""
    dirs: list[str] = []
    ffmpeg = shutil.which("ffmpeg")
    for d in ([str(Path(ffmpeg).parent)] if ffmpeg else []) + [
            "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]:
        if d not in dirs and Path(d).is_dir():
            dirs.append(d)
    return os.pathsep.join(dirs)


def server_entry(python: str | None = None, cli: str | None = None,
                 workspace: str | None = None, adapter: str = "magda_serve") -> dict[str, Any]:
    from composerbench.engine import discover_cli
    from composerbench.mcp.server import DEFAULT_WORKSPACE
    found = discover_cli(cli)
    package_root = Path(__file__).resolve().parents[2]   # .../composerbench (repo root)
    env = {"COMPOSERBENCH_ADAPTER": adapter if found else "auto",
           "COMPOSERBENCH_WORKSPACE": str(Path(workspace or DEFAULT_WORKSPACE).expanduser()),
           "PYTHONUNBUFFERED": "1",
           "PYTHONPATH": str(package_root),
           "PATH": _search_path()}
    if found:
        env["COMPOSERBENCH_CLI"] = str(found)
    return {"command": python or sys.executable, "args": ["-m", "composerbench.mcp.server"], "env": env}


def merge_config(path: Path, entry: dict[str, Any], name: str = SERVER_NAME,
                 dry_run: bool = False) -> dict[str, Any]:
    """Insert/refresh mcpServers[name] in a JSON config file, keeping everything else."""
    existing: dict[str, Any] = {}
    if path.exists():
        text = path.read_text()
        existing = json.loads(text) if text.strip() else {}
        if not isinstance(existing, dict):
            raise ValueError(f"{path} is not a JSON object; refusing to rewrite it")
    servers = existing.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise ValueError(f"{path}: mcpServers is not an object; refusing to rewrite it")
    before = (servers or {}).get(name)
    if before == entry:
        return {"client_config": str(path), "status": "unchanged"}
    updated = dict(existing)
    updated["mcpServers"] = {**(servers or {}), name: entry}
    result = {"client_config": str(path), "status": "would-update" if dry_run else "updated",
              "previous": before}
    if dry_run:
        result["new_entry"] = entry
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + time.strftime(".bak-%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
        result["backup"] = str(backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(updated, indent=2) + "\n")
    os.replace(tmp, path)
    return result


def install_claude_code(entry: dict[str, Any], name: str = SERVER_NAME,
                        dry_run: bool = False) -> dict[str, Any]:
    claude = shutil.which("claude")
    cmd = ["claude", "mcp", "add-json", "--scope", "user", name, json.dumps(entry)]
    if claude is None:
        return {"client": "claude-code", "status": "skipped",
                "reason": "the `claude` CLI is not on PATH", "run_this": cmd}
    if dry_run:
        return {"client": "claude-code", "status": "would-run", "command": cmd}
    subprocess.run([claude, "mcp", "remove", "--scope", "user", name],
                   capture_output=True, text=True, timeout=60, check=False)
    proc = subprocess.run([claude, *cmd[1:]], capture_output=True, text=True, timeout=60, check=False)
    return {"client": "claude-code", "status": "updated" if proc.returncode == 0 else "failed",
            "output": (proc.stdout + proc.stderr).strip()[-800:]}


def install(clients: list[str], entry: dict[str, Any], name: str = SERVER_NAME,
            dry_run: bool = False, home: Path | None = None) -> list[dict[str, Any]]:
    results = []
    for client in clients:
        if client == "claude-code":
            results.append(install_claude_code(entry, name, dry_run))
            continue
        path = client_config_path(client, home)
        results.append({"client": client, **merge_config(path, entry, name, dry_run)})
    return results


def doctor(entry: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    """Launch the server exactly as a client would and exercise it."""
    env = {**os.environ, **entry.get("env", {})}
    proc = subprocess.Popen([entry["command"], *entry["args"]], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    report: dict[str, Any] = {"command": [entry["command"], *entry["args"]]}
    try:
        def call(req_id, method, params=None):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                                         "params": params or {}}) + "\n")
            proc.stdin.flush()
            deadline = time.time() + timeout
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                msg = json.loads(line)
                if msg.get("id") == req_id:
                    return msg
            raise TimeoutError(f"no answer to {method}")
        init = call(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                      "clientInfo": {"name": "composerbench-doctor", "version": "1"}})
        report["server"] = init.get("result", {}).get("serverInfo")
        tools = call(2, "tools/list").get("result", {}).get("tools", [])
        report["tools"] = len(tools)
        status = call(3, "tools/call", {"name": "music_status", "arguments": {}})
        report["status"] = status.get("result", {}).get("structuredContent")
        report["ok"] = bool(tools) and not status.get("result", {}).get("isError")
    except Exception as e:  # noqa: BLE001
        report["ok"] = False
        report["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=20)
        except Exception:  # noqa: BLE001
            proc.kill()
        report["stderr_tail"] = (proc.stderr.read() or "")[-600:]
    return report
