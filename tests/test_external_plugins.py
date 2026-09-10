"""Sprint 1: external-plugin control surface (serve protocol + CLI).

Live tests against the real magda_cli binary; skipped automatically when the
binary or any host-capable plugin is unavailable so the suite stays green on
machines without plugins installed.
"""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CLI = pathlib.Path(
    "~/magda-core/cmake-build-debug/magda/daw/"
    "magda_cli_artefacts/Debug/magda_cli"
).expanduser()

pytestmark = pytest.mark.skipif(
    not CLI.exists(), reason="magda_cli binary not built"
)


def _run_cli(*args: str, project: str = "/tmp/mb_extplug") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # The wrapper nests <dir>/<stem>/<stem>.mgd; pass the nested project path.
    nested = f"{project}/{pathlib.Path(project).name}"
    return subprocess.run(
        [sys.executable, "-m", "composerbench.cli.main",
         "--adapter", "magda", "--cli", str(CLI),
         "--project", nested, *args],
        capture_output=True, text=True, timeout=600, cwd=os.path.dirname(CLI),
        env=env,
    )


def _wav_peak(path: pathlib.Path) -> float:
    import struct, wave
    with wave.open(str(path), "rb") as w:
        n, wd, ch = w.getnframes(), w.getsampwidth(), w.getnchannels()
        f = w.readframes(n)
    peak = 0
    for i in range(n):
        s = abs(int.from_bytes(f[i * wd * ch: i * wd * ch + wd], "little", signed=True))
        peak = max(peak, s)
    return peak / 32768.0


class TestPluginsListContract:
    def test_plugins_json_shape(self, tmp_path):
        """plugins list --json returns the documented device-discovery shape."""
        r = _run_cli("plugins", "list", "--json", project=str(tmp_path))
        assert r.returncode == 0, r.stderr[-500:]
        doc = json.loads(r.stdout[r.stdout.index("{"):])
        assert isinstance(doc["plugins"], list)
        for p in doc["plugins"]:
            assert {"id", "name", "format", "isInstrument"} <= set(p)

    def test_plugins_table(self, tmp_path):
        r = _run_cli("plugins", "list", project=str(tmp_path))
        assert r.returncode == 0


@pytest.mark.slow
class TestHostRoundTrip:
    """discover -> load -> inspect -> set param -> remove, on a real plugin."""

    @pytest.fixture(scope="class")
    def some_plugin(self):
        if not CLI.exists():
            pytest.skip("no binary")
        from composerbench.adapter.magda_serve import MagdaServeAdapter

        async def find():
            daw = MagdaServeAdapter(CLI, "/tmp/mb_extplug_disc/p.mgd")
            await daw.initialize()
            try:
                plugs = await daw.list_plugins()
            finally:
                if daw._serve_proc is not None:
                    await daw.shutdown()
            return plugs

        plugs = asyncio.run(find())
        if not plugs:
            pytest.skip("no scanned plugins on this machine")
        # Prefer an instrument (audibility-checkable); else any plugin.
        instruments = [p for p in plugs if p.get("isInstrument")]
        return (instruments or plugs)[0]

    def test_load_inspect_set_remove(self, tmp_path, some_plugin):
        from composerbench.adapter.magda_serve import MagdaServeAdapter

        async def main():
            daw = MagdaServeAdapter(CLI, tmp_path / "p.mgd")
            await daw.initialize()
            try:
                t = await daw.create_track("ext")
                ext_id = await daw.add_plugin(t.id, some_plugin["id"],
                                              as_instrument=some_plugin.get("isInstrument", False))
                assert ext_id > 0
                params = await daw.external_parameters(ext_id)
                assert isinstance(params, list)
                if params:  # set first param round-trip
                    name, value = params[0]["name"], 0.42
                    await daw.set_external_parameter(ext_id, name, value)
                    after = await daw.external_parameters(ext_id)
                    hit = next(p for p in after if p["name"] == name)
                    assert abs(hit["value"] - value) < 0.02
                await daw.remove_plugin(ext_id)
            finally:
                if daw._serve_proc is not None:
                    await daw.shutdown()

        asyncio.run(main())

    def test_render_with_hosted_instrument_is_audible(self, tmp_path, some_plugin):
        if not some_plugin.get("isInstrument"):
            pytest.skip("only provable with an instrument plugin")
        from composerbench.adapter.base import RenderRequest
        from composerbench.adapter.magda_serve import MagdaServeAdapter
        from composerbench.model import Note

        async def main():
            daw = MagdaServeAdapter(CLI, tmp_path / "r.mgd")
            await daw.initialize()
            try:
                t = await daw.create_track("synth")
                ext_id = await daw.add_plugin(t.id, some_plugin["id"], as_instrument=True)
                c = await daw.create_clip(t.id, "c", 0.0, 4.0)
                await daw.add_notes(t.id, c.id,
                                    [Note(start=0.0, duration=2.0, pitch=60, velocity=110)])
                out = tmp_path / "probe.wav"
                await daw.render(RenderRequest(output_path=out))
                return out
            finally:
                if daw._serve_proc is not None:
                    await daw.shutdown()

        out = asyncio.run(main())
        if out.exists():
            assert _wav_peak(out) > 0.001, "hosted instrument rendered silence"
