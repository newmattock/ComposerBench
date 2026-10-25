"""MCP server end-to-end against the REAL headless MAGDA CLI.

Speaks newline JSON-RPC over a real subprocess exactly like an MCP client
would, then verifies the music actually happened: project mutated on disk,
render produced non-silent audio.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from composerbench.engine import discover_cli

MAGDA_CLI = discover_cli() or Path("/nonexistent/magda_cli")

pytestmark = pytest.mark.skipif(
    not MAGDA_CLI.exists(), reason="magda-cli binary not built yet"
)


def rpc(msg: dict) -> str:
    return json.dumps(msg) + "\n"


@pytest.mark.parametrize("adapter", ["magda", "magda_serve"])
def test_mcp_over_real_magda(tmp_path: Path, adapter):
    project = tmp_path / "proj.mgd"
    env = dict(
        os.environ,
        COMPOSERBENCH_ADAPTER=adapter,
        COMPOSERBENCH_CLI=str(MAGDA_CLI),
        COMPOSERBENCH_PROJECT=str(project),
    )
    lines = [
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-11-25"}}),
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "music_project_init", "arguments": {}}}),
        rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "music_track_create",
                        "arguments": {"name": "bass"}}}),
        rpc({"jsonrpc": "2.0", "id": "3b", "method": "tools/call",
             "params": {"name": "music_device_load",
                        "arguments": {"track": "bass", "device": "4osc"}}}),
        rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "music_clip_create",
                        "arguments": {"track": "bass", "name": "groove",
                                      "start_beats": 0, "length_beats": 8}}}),
        rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "music_notes_add",
                        "arguments": {"track": "bass", "clip": "groove",
                                      "notes": [
                                          {"start": i * 0.5, "pitch": 36 + (i % 3),
                                           "duration": 0.45, "velocity": 110}
                                          for i in range(16)]}}}),
        rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "music_project_inspect", "arguments": {}}}),
        rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "music_render",
                        "arguments": {"output_path": str(tmp_path / "out.wav"),
                                      "start_beats": 0, "length_beats": 8}}}),
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "composerbench.mcp.server"],
        input="".join(lines), capture_output=True, text=True,
        timeout=600, cwd=str(Path(__file__).resolve().parents[1]), env=env,
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    responses = {}
    for line in proc.stdout.splitlines():
        r = json.loads(line)
        if "id" in r:
            responses[r["id"]] = r

    def payload(rid: int) -> dict:
        result = responses[rid]["result"]
        return json.loads(result["content"][0]["text"])

    assert responses[1]["result"]["serverInfo"]["name"] == "composerbench"
    assert payload(2)["created"] is True
    assert payload(3)["created"] == "bass"
    assert payload("3b")["loaded"] == "4osc"
    assert "clip_id" in payload(4)
    assert payload(5)["added"] == 16
    inspect = payload(6)
    assert inspect["tracks"] and inspect["tracks"][0]["clips"] == 1
    render = payload(7)
    assert render["audio"]["rms_dbfs"] > -60, "render must be audible, not silence"
    assert (tmp_path / "out.wav").exists()
