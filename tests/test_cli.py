import json
import subprocess
import sys
from pathlib import Path


def run_cli(*argv, cwd=None):
    proc = subprocess.run(
        [sys.executable, "-m", "composerbench.cli.main", *argv],
        capture_output=True, text=True, timeout=60,
        cwd=cwd or str(Path(__file__).resolve().parents[1]),
    )
    return proc


def test_status_json_output(tmp_path):
    proc = run_cli("--json", "--project", str(tmp_path), "status")
    assert proc.returncode == 0, proc.stderr[-400:]
    payload = json.loads(proc.stdout)
    assert payload["title"] == "Untitled"
    assert isinstance(payload["tracks"], list)


def test_inspect_track_json_and_symbolic_stats(tmp_path):
    from composerbench.client import ComposerBench
    from composerbench.model import Note

    async def seed():
        mb = ComposerBench.connect_fake(tmp_path)
        await mb.create_track("piano")
        await mb.create_clip("piano", "v1", 0.0, 8.0)
        await mb.add_notes("piano", clip="v1",
                           notes=[Note(start=i * 2, duration=1.0, pitch=60 + i, velocity=90)
                                  for i in range(4)])

    import asyncio
    asyncio.run(seed())
    proc = run_cli("--json", "--project", str(tmp_path), "inspect", "track", "piano")
    assert proc.returncode == 0, proc.stderr[-400:]
    payload = json.loads(proc.stdout)
    assert payload["name"] == "piano"
    assert payload["note_count"] == 4
    assert payload["symbolic"]["median_pitch"] == 62  # upper-median of 60..63


def test_structured_error_on_missing_track(tmp_path):
    proc = run_cli("--json", "--project", str(tmp_path), "inspect", "track", "ghost")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["error"] == "ENTITY_NOT_FOUND"


def test_render_command_empty_project_is_structured_error(tmp_path):
    proc = run_cli("--json", "--project", str(tmp_path), "render",
                   "--out", str(tmp_path / "r.wav"))
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["error"] in ("UNSUPPORTED_OPERATION", "RENDER_FAILED")


def test_render_command_after_seeding_produces_wav(tmp_path):
    import asyncio

    from composerbench.analysis.audio import analyze_wav
    from composerbench.client import ComposerBench
    from composerbench.model import Note

    async def seed():
        mb = ComposerBench.connect_fake(tmp_path)
        await mb.create_track("piano")
        await mb.create_clip("piano", "v1", 0.0, 8.0)
        await mb.add_notes("piano", clip="v1",
                           notes=[Note(start=i, duration=0.5, pitch=60 + i) for i in range(8)])

    asyncio.run(seed())
    out = tmp_path / "r.wav"
    proc = run_cli("--json", "--project", str(tmp_path), "render", "--out", str(out))
    assert proc.returncode == 0, (proc.stdout[-400:], proc.stderr[-400:])
    payload = json.loads(proc.stdout)
    assert payload["audio"]["duration_seconds"] > 1.0
    assert analyze_wav(out).duration_seconds > 1.0
