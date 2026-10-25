"""MCP production tools: catalog, presets, checks, background song builds, mixdown."""
import asyncio
import sys
from pathlib import Path

import pytest

from composerbench.mcp.server import TOOLS, MCPServer, ServerConfig
from composerbench.song.examples import example_song

EMULATOR = Path(__file__).with_name("_serve_emulator.py")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGDA_SOCK", raising=False)
    monkeypatch.setenv("EMU_RENDER_AUDIO", "1")
    monkeypatch.setenv("EMU_CATALOG", "1")
    cli = tmp_path / "magda_cli_emu"
    cli.write_text(f'#!/bin/sh\nEMU_PROTOCOL=2 exec "{sys.executable}" "{EMULATOR}" "$@"\n')
    cli.chmod(0o755)
    return MCPServer(None, ServerConfig(adapter="fake", cli_path=str(cli), workspace=str(tmp_path)))


def payload(result):
    assert "structuredContent" in result
    return result["structuredContent"]


def test_tools_are_registered_with_strict_schemas():
    names = {t["name"]: t for t in TOOLS}
    for n in ("music_device_catalog", "music_presets", "music_song_check", "music_song_build",
              "music_job_status", "music_mixdown", "music_score_check", "music_section_report"):
        assert n in names and names[n]["inputSchema"]["additionalProperties"] is False


async def test_catalog_presets_and_score_check(server):
    cat = payload(await server.call_tool("music_device_catalog", {"device": "magda_delay"}))
    assert any(p["name"] == "Division" and "1/8." in p["choices"] for p in cat["params"])
    listing = payload(await server.call_tool("music_device_catalog", {"kind": "effect"}))
    assert all(d["kind"] == "effect" for d in listing["devices"])
    missing = await server.call_tool("music_device_catalog", {"device": "nope"})
    assert missing["isError"]
    pre = payload(await server.call_tool("music_presets", {"name": "bass_808"}))
    assert pre["instrument"]["device"] == "magda_polysynth"
    _, score = example_song()
    check = payload(await server.call_tool("music_score_check", {"score": score, "ranges": {"bass": [24, 52]},
                                                                 "mono": ["lead"]}))
    assert check["harmony"]["clashes"] == [] and check["validation"] == []
    bad = await server.call_tool("music_score_check", {"ranges": {"bass": [1, 2, 3]}})
    assert bad["isError"]                       # schema: exactly two numbers per range


async def test_song_build_job_then_mixdown(server, tmp_path):
    spec, score = example_song()
    spec["score"] = score
    check = payload(await server.call_tool("music_song_check", {"spec": spec, "out_dir": str(tmp_path / "s")}))
    assert check["ok"]
    started = payload(await server.call_tool("music_song_build", {"spec": spec, "out_dir": str(tmp_path / "s")}))
    job_id = started["job_id"]
    assert started["status"] in ("queued", "running")
    for _ in range(300):
        status = payload(await server.call_tool("music_job_status", {"job_id": job_id}))
        if status["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.1)
    assert status["status"] == "done", status
    assert set(status["result"]["renders"]) >= {"mix", "stem:drums"} and status["log_tail"]
    listing = payload(await server.call_tool("music_job_status", {}))
    assert any(j["job_id"] == job_id for j in listing["jobs"])

    mix_spec = {"score": str(tmp_path / "s" / "score.json"), "stems_dir": str(tmp_path / "s" / "stems"),
                "sidechain": [{"trigger": {"part": "kick"}, "targets": ["bass", "synths"], "depth_db": 4}],
                "cuts": [{"start": "verse.end-1", "end": "verse.end", "keep": ["fx"]}],
                "master": {"target_lufs": -14, "ceiling_dbtp": -1},
                "export": {"basename": "master", "mp3": False, "spectrogram": False}}
    mixed = payload(await server.call_tool("music_mixdown", {"spec": mix_spec, "out_dir": str(tmp_path / "s")}))
    assert mixed["status"] == "done", mixed
    assert mixed["result"]["master"]["true_peak_dbtp"] <= -1.0
    assert Path(mixed["result"]["files"]["wav"]).exists()
    rep = payload(await server.call_tool("music_section_report", {
        "path": mixed["result"]["files"]["wav"], "score_path": str(tmp_path / "s" / "score.json"),
        "stems_dir": str(tmp_path / "s" / "stems")}))
    assert set(rep["sections"]["mix"]) == {"verse", "drop"} and "bass" in rep["sections"]["stems"]


async def test_song_build_errors_surface_in_the_job(server, tmp_path):
    spec, score = example_song()
    spec["score"] = score
    spec["tracks"][0]["params"] = {"nonsense": 1}
    started = payload(await server.call_tool("music_song_build", {"spec": spec, "out_dir": str(tmp_path / "x"),
                                                                  "wait": True}))
    assert started["status"] == "failed" and started["error"]["error"] == "SONG_SPEC_INVALID"
    nothing = await server.call_tool("music_song_build", {})
    assert nothing["isError"]
