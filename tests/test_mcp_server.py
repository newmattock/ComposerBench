import json
import subprocess
import sys
from pathlib import Path

import pytest

from composerbench.mcp.server import MCPServer, ServerConfig, build_client


@pytest.fixture()
def server(tmp_path):
    cfg = ServerConfig(adapter="fake", project_path=str(tmp_path))
    return MCPServer(build_client(cfg))


async def call(server, name, args=None):
    resp = await server.adispatch({
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": args or {}},
    })
    result = resp["result"]
    payload = json.loads(result["content"][0]["text"])
    return result, payload


def test_initialize_handshake(server):
    resp = server.dispatch({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                            "params": {"protocolVersion": "2025-11-25"}})
    assert resp["result"]["protocolVersion"] == "2025-11-25"
    assert resp["result"]["serverInfo"]["name"] == "composerbench"
    assert server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_has_semantic_surface(server):
    resp = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert {"music_project_inspect", "music_notes_add", "music_render",
            "music_intent_read", "music_snapshot_create"} <= names
    assert all("inputSchema" in t for t in resp["result"]["tools"])


async def test_compose_flow_over_mcp(server):
    await call(server, "music_track_create", {"name": "piano"})
    _, payload = await call(server, "music_clip_create", {
        "track": "piano", "name": "verse", "start_beats": 0, "length_beats": 8})
    assert "clip_id" in payload
    _, added = await call(server, "music_notes_add", {
        "track": "piano", "clip": "verse",
        "notes": [{"start": i, "pitch": "C4", "duration": 1} for i in range(4)]})
    assert added["added"] == 4
    _, inspect = await call(server, "music_project_inspect")
    assert inspect["tracks"][0]["name"] == "piano"
    assert inspect["tracks"][0]["clips"] == 1
    assert inspect["tracks"][0]["stable_id"].startswith("mb:track:")
    _, stats = await call(server, "music_track_inspect", {"track": "piano"})
    assert stats["symbolic_stats"]["note_count"] == 4


async def test_unknown_tool_is_protocol_error(server):
    resp = await server.adispatch({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "music_nonexistent", "arguments": {}}})
    # tool-not-found is an exceptional condition -> protocol error per MCP spec
    assert ("error" in resp and resp["error"]["code"] == -32601) or "isError" in str(resp)


async def test_tool_error_inside_result(server):
    result, payload = await call(server, "music_track_inspect", {"track": "ghost"})
    assert result["isError"] is True
    assert payload["error"] == "ENTITY_NOT_FOUND"


async def test_intent_and_plan_via_mcp(server):
    await call(server, "music_intent_update", {
        "intent": {"title": "mcp demo", "goal": "become loud", "principles": ["keep motif"]}})
    _, intent = await call(server, "music_intent_read")
    assert intent["goal"] == "become loud"
    await call(server, "music_plan_update", {
        "plan": {"sections": [{"name": "intro", "bars": 8, "start_bar": 1}]}})
    _, plan = await call(server, "music_plan_read")
    assert plan["sections"][0]["name"] == "intro"


async def test_snapshot_cycle_via_mcp(server):
    await call(server, "music_track_create", {"name": "drums"})
    _, snap = await call(server, "music_snapshot_create", {"label": "pre"})
    _, out = await call(server, "music_snapshot_restore",
                        {"snapshot_id": snap["snapshot_id"]})
    assert out["restored"].startswith("snap:pre")


def test_stdio_end_to_end_subprocess():
    """The real protocol loop: spawn server, speak newline JSON-RPC over stdio."""
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "composerbench.mcp.server"],
        input="\n".join(lines) + "\n",
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    responses = [json.loads(l) for l in proc.stdout.strip().splitlines()]
    assert len(responses) == 2  # notification produces no response
    assert responses[0]["result"]["serverInfo"]["name"] == "composerbench"
    tools = responses[1]["result"]["tools"]
    assert any(t["name"] == "music_render" for t in tools)
