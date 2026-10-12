"""MCP server: project lifecycle, fast paths and protocol hygiene."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from composerbench.client import ComposerBench
from composerbench.mcp.server import INSTRUCTIONS, MCPServer, ServerConfig, normalize_project_path

EMULATOR = Path(__file__).with_name("_serve_emulator.py")


def emulator_cli(tmp_path):
    cli = tmp_path / "magda_cli"
    cli.write_text(f'#!/bin/sh\nEMU_PROTOCOL=2 exec "{sys.executable}" "{EMULATOR}" "$@"\n')
    cli.chmod(0o755)
    return cli


async def call(server, tool, **args):
    out = await server.call_tool(tool, args)
    return out["structuredContent"], out["isError"]


def test_initialize_advertises_instructions_and_answers_ping():
    server = MCPServer(ComposerBench.connect_fake("."))
    init = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["instructions"] == INSTRUCTIONS
    assert server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}
    names = {t["name"] for t in server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]}
    assert {"music_status", "music_project_new", "music_project_open", "music_notes_set",
            "music_parameter_describe", "music_plugins_list", "music_plugin_add"} <= names


async def test_tools_need_a_project_until_one_is_opened(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGDA_SOCK", raising=False)
    cfg = ServerConfig(adapter="magda_serve", cli_path=str(emulator_cli(tmp_path)),
                       workspace=str(tmp_path / "ws"))
    server = MCPServer(None, cfg)
    out, err = await call(server, "music_track_create", name="x")
    assert err and out["error"] == "NO_PROJECT"
    status, err = await call(server, "music_status")
    assert not err and status["adapter"] == "magda_serve" and status["next"]
    caps, err = await call(server, "music_capabilities")
    assert not err and caps["project"] is None and caps["next"]


async def test_hosted_plugin_labels_through_mcp(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGDA_SOCK", raising=False)
    cfg = ServerConfig(adapter="magda_serve", cli_path=str(emulator_cli(tmp_path)),
                       workspace=str(tmp_path / "ws"))
    server = MCPServer(None, cfg)
    await call(server, "music_project_new", name="labels")
    await call(server, "music_track_create", name="saws")
    listed, _ = await call(server, "music_plugins_list", query="surge")
    assert [p["name"] for p in listed["plugins"]] == ["Surge XT"]
    added, err = await call(server, "music_plugin_add", track="saws", plugin="Surge XT")
    assert not err and added["persistent"] and added["instrument"]
    dev = str(added["device_id"])
    # Surge does not declare its filter type discrete: the label comes from param_info
    out, err = await call(server, "music_parameter_set", track="saws", device=dev,
                          parameter="A Filter 1 Type", value="LP 12 dB")
    assert not err, out
    desc, _ = await call(server, "music_parameter_describe", track="saws", device=dev,
                         parameter="A Filter 1 Type")
    assert desc["text"] == "LP 12 dB" and "LP 24 dB" in desc["choices"]
    host, _ = await call(server, "music_parameter_describe", track="saws", device=dev, parameter="host:Wet Level")
    assert host["owner"] == "host" and "wrapper" in host["note"]
    await server.client.close()


async def test_project_new_notes_set_labels_and_reopen(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGDA_SOCK", raising=False)
    cfg = ServerConfig(adapter="magda_serve", cli_path=str(emulator_cli(tmp_path)),
                       workspace=str(tmp_path / "ws"))
    server = MCPServer(None, cfg)
    created, err = await call(server, "music_project_new", name="my song", tempo_bpm=160)
    assert not err and created["tempo_bpm"] == 160 and created["project"].endswith("my_song.mgd")
    await call(server, "music_track_create", name="lead")
    await call(server, "music_device_load", track="lead", device="magda_polysynth")
    await call(server, "music_clip_create", track="lead", name="part", start_beats=0, length_beats=32)
    notes = [{"start": i * 0.5, "duration": 0.4, "pitch": "C5" if i % 2 else 67, "velocity": 90}
             for i in range(64)]
    out, err = await call(server, "music_notes_set", track="lead", notes=notes)
    assert not err and out["notes"] == 64
    devices, _ = await call(server, "music_devices_inspect", track="lead")
    synth = devices["devices"][0]["id"]
    out, err = await call(server, "music_parameter_set", track="lead", device=synth,
                          parameter="Voice Mode", value="Legato")
    assert not err, out
    desc, err = await call(server, "music_parameter_describe", track="lead", device=synth, parameter="Voice Mode")
    assert not err and desc["text"] == "Legato" and desc["choices"] == ["Poly", "Mono", "Legato"]
    bad, err = await call(server, "music_parameter_set", track="lead", device=synth,
                          parameter="Voice Mode", value="Choir")
    assert err and bad["error"] == "UNKNOWN_PARAMETER_LABEL" and "Legato" in bad["choices"]
    await call(server, "music_project_save")
    listed, _ = await call(server, "music_project_list")
    assert created["project"] in listed["projects"]
    status, _ = await call(server, "music_status")
    assert status["features"]["engine"]["protocol"] == 2
    opened, err = await call(server, "music_project_open", path=str(Path(created["project"]).parent))
    assert not err and opened["tracks"] == ["lead"]
    inspect, _ = await call(server, "music_track_inspect", track="lead")
    assert inspect["symbolic_stats"]["note_count"] == 64
    await server.client.close()


def test_normalize_project_path_accepts_folder_and_wrapper(tmp_path):
    wrapped = tmp_path / "song" / "song.mgd"
    wrapped.parent.mkdir()
    wrapped.write_bytes(b"x")
    assert normalize_project_path(tmp_path / "song") == wrapped
    assert normalize_project_path(tmp_path / "song.mgd") == wrapped


def test_stdio_frames_stay_clean(tmp_path):
    """Every stdout line is a JSON-RPC frame even when the server errors."""
    env = {"COMPOSERBENCH_ADAPTER": "fake", "COMPOSERBENCH_WORKSPACE": str(tmp_path), "PATH": "/usr/bin:/bin"}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "music_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ]
    proc = subprocess.run([sys.executable, "-m", "composerbench.mcp.server"],
                          input="\n".join(json.dumps(r) for r in requests) + "\n",
                          capture_output=True, text=True, timeout=60, env=env,
                          cwd=str(Path(__file__).resolve().parents[1]))
    frames = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [f["id"] for f in frames] == [1, 2, 3, 4]
    assert frames[1]["result"]["structuredContent"]["adapter"] == "fake"
    assert frames[2]["result"]["isError"]
