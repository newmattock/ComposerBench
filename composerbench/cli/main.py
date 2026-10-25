"""composerbench / cbench CLI (spec §20-21).

Thin agent interface over the client facade. Every command supports --json;
errors are always structured JSON (never pretty-prose parsing).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from composerbench.client import ComposerBench
from composerbench.errors import ComposerBenchError


def _client(args: argparse.Namespace) -> ComposerBench:
    if args.adapter == "magda":
        return ComposerBench.connect_magda(args.cli, args.project)
    return ComposerBench.connect_fake(args.project)


def _emit(data: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif isinstance(data, dict):
        for k, v in data.items():
            print(f"{k}: {v}")
    else:
        print(data)


async def cmd_status(ns: argparse.Namespace) -> int:
    mb = _client(ns)
    info = await mb.project()
    tracks = await mb.tracks()
    _emit({
        "title": info.title,
        "tempo_bpm": info.tempo_bpm,
        "length_beats": round(info.length_beats, 2),
        "revision": info.revision,
        "tracks": [t.name for t in tracks],
    }, ns.json)
    return 0


async def cmd_inspect(ns: argparse.Namespace) -> int:
    mb = _client(ns)
    if ns.what == "project":
        payload = {
            "tracks": [{"name": t.name, "volume": t.volume} for t in await mb.tracks()],
            "clips": [
                {"track": c.track_id, "name": c.name, "start": c.start,
                 "length": c.length, "notes": len(c.notes)}
                for c in await mb.clips()
            ],
        }
    elif ns.what == "track":
        t = await mb.track(ns.target)
        notes = await mb.notes(t.name)
        from composerbench.analysis.symbolic import analyze_notes
        payload = {
            "name": t.name,
            "volume": t.volume,
            "muted": t.muted,
            "devices": [d.name for d in t.devices],
            "clips": [{"id": c.id, "name": c.name, "start": c.start,
                       "length": c.length} for c in await mb.clips(t.name)],
            "note_count": len(notes),
            "symbolic": analyze_notes(notes).to_dict(),
        }
    else:  # notes
        notes = await mb.notes(ns.target)
        if ns.bars:
            lo, hi = (float(x) for x in ns.bars.split(":"))
            notes = [n for n in notes if lo <= n.start < hi]
        payload = {"notes": [n.model_dump() for n in notes], "count": len(notes)}
    _emit(payload, ns.json)
    return 0


async def cmd_render(ns: argparse.Namespace) -> int:
    from composerbench.adapter.base import RenderRequest
    mb = _client(ns)
    path = await mb.render(RenderRequest(
        output_path=Path(ns.out or "render.wav"),
        start_beats=ns.start_beats,
        length_beats=ns.length_beats,
    ))
    from composerbench.analysis.audio import analyze_wav
    _emit({"file": str(path), "audio": analyze_wav(path).to_dict()}, ns.json)
    return 0


async def cmd_snapshot(ns: argparse.Namespace) -> int:
    mb = _client(ns)
    _emit({"snapshot_id": await mb.daw.snapshot(ns.label)}, ns.json)
    return 0


async def cmd_restore(ns: argparse.Namespace) -> int:
    mb = _client(ns)
    await mb.daw.restore_snapshot(ns.snapshot_id)
    _emit({"restored": ns.snapshot_id}, ns.json)
    return 0


async def cmd_intent(ns: argparse.Namespace) -> int:
    mb = _client(ns)
    _emit((await mb.read_intent()).to_dict(), ns.json)
    return 0


async def cmd_doctor(ns: argparse.Namespace) -> int:
    """Probe a MAGDA binary: which instrument ids actually produce sound?

    Renders one note through each candidate id in an isolated throwaway
    project and measures the WAV peak. Writes a manifest consumed by
    load_device() guards and RevisionLoop silence gates.
    """
    import os
    import shutil
    import tempfile
    import wave

    from composerbench.adapter.base import RenderRequest
    from composerbench.adapter.instruments import (
        ALLOW_ANY_ENV,
        KNOWN_AUDIBLE_INSTRUMENTS,
        write_manifest,
    )
    from composerbench.adapter.magda import MagdaCliAdapter
    from composerbench.model import Note

    cli_path = Path(ns.cli).expanduser()
    if not cli_path.exists():
        from composerbench.errors import MagdaCliNotFound
        raise MagdaCliNotFound(cli=str(cli_path))

    silent_evidence = ["square", "pulse", "saw", "noise", "triangle", "sine"]
    candidates = (
        [c.strip() for c in ns.instruments.split(",") if c.strip()]
        if ns.instruments
        else list(KNOWN_AUDIBLE_INSTRUMENTS) + silent_evidence
    )

    # Probing unknown ids is the whole point; bypass the load guard here.
    previous = os.environ.get(ALLOW_ANY_ENV)
    os.environ[ALLOW_ANY_ENV] = "1"
    try:
        work = Path(tempfile.mkdtemp(prefix="mb_doctor_"))
        results = []
        try:
            for instrument_id in candidates:
                proj_dir = work / instrument_id.replace("/", "_")
                proj_dir.mkdir(parents=True, exist_ok=True)
                entry = {"id": instrument_id, "audible": False, "peak": 0.0}
                try:
                    daw = MagdaCliAdapter(cli_path, proj_dir / "probe.mgd")
                    await daw.initialize()
                    track = await daw.create_track("probe")
                    await daw.load_device(track.id, instrument_id)
                    clip = await daw.create_clip(track.id, "probe", 0.0, 2.0)
                    await daw.add_notes(
                        track.id, clip.id,
                        [Note(start=0.0, duration=0.9, pitch=69, velocity=110)],
                    )
                    out = proj_dir / "probe.wav"
                    await daw.render(RenderRequest(output_path=out))
                    with wave.open(str(out), "rb") as w:
                        frames = w.readframes(w.getnframes())
                        width = w.getsampwidth() or 2
                    n = len(frames) // width
                    peak_raw = 0
                    step = max(1, n // 4000)
                    for i in range(0, max(n - width, 0), step):
                        sample = abs(int.from_bytes(
                            frames[i * width:(i + 1) * width], "little", signed=True))
                        peak_raw = max(peak_raw, sample)
                    full_scale = float(2 ** (width * 8 - 1))
                    peak = peak_raw / full_scale
                    entry["peak"] = round(peak, 5)
                    entry["audible"] = peak > 0.001
                except Exception as e:  # noqa: BLE001 - per-instrument isolation
                    entry["error"] = str(e)[:120]
                results.append(entry)

            manifest = {
                "binary": str(cli_path),
                "generated_by": "composerbench doctor",
                "instruments": results,
            }
            # Default destination: the global manifest, so every future
            # session's load guard + silence gate use fresh facts.
            if ns.out:
                out_path = Path(ns.out).expanduser()
                if out_path.is_dir() or not out_path.suffix:
                    manifest_path = write_manifest(out_path, manifest)
                else:
                    import json
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(json.dumps(manifest, indent=2))
                    manifest_path = out_path
            else:
                from composerbench.adapter.instruments import GLOBAL_MANIFEST
                GLOBAL_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
                import json
                GLOBAL_MANIFEST.write_text(json.dumps(manifest, indent=2))
                manifest_path = GLOBAL_MANIFEST
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        if previous is None:
            os.environ.pop(ALLOW_ANY_ENV, None)
        else:
            os.environ[ALLOW_ANY_ENV] = previous

    payload = {
        "binary": str(cli_path),
        "manifest_written_to": str(manifest_path) if manifest_path else None,
        "results": results,
    }
    _emit(payload, ns.json)

    if not ns.json:
        print("---")
        for r in results:
            status = "AUDIBLE" if r["audible"] else "SILENT"
            print(f"  {r['id']:18s} {status:8s} peak={r['peak']:.4f}")
    return 0


async def cmd_plan(ns: argparse.Namespace) -> int:
    mb = _client(ns)
    _emit((await mb.read_plan()).to_dict(), ns.json)
    return 0


# ------------------------------------------------------------ external plugins
def _serve_daw(args: argparse.Namespace):
    """Serve-mode adapter for plugin work (external plugins need the daemon)."""
    from composerbench.adapter.magda_serve import MagdaServeAdapter
    project = Path(args.project)
    return MagdaServeAdapter(args.cli, project)


async def cmd_serve(ns: argparse.Namespace) -> int:
    """Run the persistent engine in the foreground until Ctrl-C."""
    daw = await _serve_daw(ns).__aenter__() if False else None
    from composerbench.adapter.magda_serve import MagdaServeAdapter
    daw = MagdaServeAdapter(ns.cli, Path(ns.project))
    await daw.initialize()
    print(f"composerbench engine serving on {daw._sock_path} (Ctrl-C to stop)")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await daw.shutdown()
    return 0


async def cmd_plugins(ns: argparse.Namespace) -> int:
    daw = _serve_daw(ns)
    try:
        # Discovery needs a live engine but not an existing project file:
        # boot against a scratch project when the given path is absent.
        if not Path(ns.project).exists():
            await daw.initialize()
        if ns.action == "scan":
            await daw.scan_plugins()
        plugs = await daw.list_plugins(format_filter=ns.format)
    finally:
        if daw._serve_proc is not None:
            await daw.shutdown()
    as_json = ns.json or getattr(ns, "json_flag", False)
    if as_json:
        print(json.dumps({"plugins": plugs}, indent=2))
    else:
        for p in plugs:
            kind = "instrument" if p.get("isInstrument") else "effect"
            print(f"{p['name']:32s} {p['format']:12s} {kind:10s}")
    return 0


async def cmd_add_plugin(ns: argparse.Namespace) -> int:
    daw = _serve_daw(ns)
    try:
        await daw.initialize()
        t = await daw.create_track(ns.track)
        ext_id = await daw.add_plugin(t.id, ns.plugin,
                                      as_instrument=ns.instrument)
        print(json.dumps({"track": t.name, "device": ext_id}) if ns.json
              else f"device {ext_id}")
    finally:
        if daw._serve_proc is not None:
            await daw.shutdown()
    return 0


async def cmd_probe_device(ns: argparse.Namespace) -> int:
    from composerbench.adapter.instruments import GLOBAL_MANIFEST
    daw = _serve_daw(ns)
    try:
        await daw.initialize()
        out = (Path(ns.out) if ns.out
               else GLOBAL_MANIFEST.parent / "devices"
               / f"{ns.plugin.lower().replace(' ', '_')}.json")
        manifest = await daw.probe_plugin(ns.plugin, save_to=out)
        if ns.json:
            print(json.dumps(manifest, indent=2))
        else:
            print(f"probed {ns.plugin}: {len(manifest['parameters'])} parameters "
                  f"-> {out}")
    finally:
        if daw._serve_proc is not None:
            await daw.shutdown()
    return 0


async def cmd_ext_params(ns: argparse.Namespace) -> int:
    daw = _serve_daw(ns)
    try:
        await daw.initialize()
        params = await daw.external_parameters(ns.device_id)
        if ns.set is not None:
            name, value = ns.set
            await daw.set_external_parameter(ns.device_id, name, float(value))
            params = await daw.external_parameters(ns.device_id)
            after = next((p for p in params
                          if p["name"].lower() == name.lower()), None)
            result = {"set": {"name": name, "value": value},
                      "verified": bool(after) and abs(after["value"] - value) < 1e-3}
        else:
            result = {"parameters": params}
        print(json.dumps(result, indent=2) if ns.json else json.dumps(result))
    finally:
        if daw._serve_proc is not None:
            await daw.shutdown()
    return 0


async def cmd_mcp(ns: argparse.Namespace) -> int:
    """Register/inspect the MCP server for agent clients (Claude, Cursor)."""
    from composerbench.mcp import install as mcp_install

    entry = mcp_install.server_entry(cli=ns.cli, workspace=ns.workspace, toolsets=ns.toolsets)
    if ns.mcp_action == "config":
        print(json.dumps({"mcpServers": {ns.name: entry}}, indent=2))
        return 0
    if ns.mcp_action == "doctor":
        report = mcp_install.doctor(entry)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("ok") else 1
    requested = ns.client or ["all"]
    clients = list(mcp_install.CLIENTS) if "all" in requested else requested
    results = mcp_install.install(clients, entry, name=ns.name, dry_run=ns.dry_run)
    print(json.dumps({"server": entry, "results": results}, indent=2, default=str))
    if not entry["env"].get("COMPOSERBENCH_CLI"):
        print("warning: magda_cli was not found; the server will fall back to the fake "
              "emulator until COMPOSERBENCH_CLI points at a built binary", file=sys.stderr)
    if not ns.dry_run:
        print("Restart Claude Desktop / Cursor (or start a new Claude Code session) "
              "to load the composerbench tools.", file=sys.stderr)
    return 0 if all(r.get("status") != "failed" for r in results) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="composerbench", description="agentic musical workspace")
    p.add_argument("--adapter", default="fake", choices=["fake", "magda"])
    p.add_argument("--project", default=".")
    p.add_argument(
        "--cli",
        default=None,
        help="magda_cli path (default: $COMPOSERBENCH_CLI, then ~/magda-core build folders)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)

    insp = sub.add_parser("inspect")
    insp.add_argument("what", choices=["project", "track", "notes"])
    insp.add_argument("target", nargs="?", help="track ref for track/notes")
    insp.add_argument("--bars", help="lo:hi beat-range filter for notes")
    insp.set_defaults(func=cmd_inspect)

    rnd = sub.add_parser("render")
    rnd.add_argument("--out")
    rnd.add_argument("--start-beats", type=float)
    rnd.add_argument("--length-beats", type=float)
    rnd.set_defaults(func=cmd_render)

    snap = sub.add_parser("snapshot")
    snap.add_argument("label")
    snap.set_defaults(func=cmd_snapshot)

    rst = sub.add_parser("restore")
    rst.add_argument("snapshot_id")
    rst.set_defaults(func=cmd_restore)

    sub.add_parser("intent").set_defaults(func=cmd_intent)
    sub.add_parser("plan").set_defaults(func=cmd_plan)

    doc = sub.add_parser(
        "doctor",
        help="probe a MAGDA binary: which instrument ids render audible sound?",
    )
    doc.add_argument("--out", help="manifest destination (default: ~/.musicbench/instrument_manifest.json)")
    doc.add_argument("--instruments", help="comma-separated instrument ids to probe (default: known list + silent evidence)")
    doc.set_defaults(func=cmd_doctor)

    # ---- external plugin control (Sprint 1) --------------------------------
    srv = sub.add_parser("serve", help="run the persistent engine in the foreground")
    srv.set_defaults(func=cmd_serve)

    plg = sub.add_parser("plugins", help="list scanned AU/VST3/LV2 plugins")
    plg.add_argument("action", nargs="?", default="list", choices=["list", "scan"],
                     help="list, or scan to rescan installed plugins first")
    plg.add_argument("--format", help="filter by format (vst3, au, lv2)")
    plg.add_argument("--json", action="store_true", dest="json_flag",
                     help="machine-readable output")
    plg.set_defaults(func=cmd_plugins)

    addp = sub.add_parser("add-plugin", help="host an external plugin on a new track")
    addp.add_argument("--track", required=True)
    addp.add_argument("--plugin", required=True)
    addp.add_argument("--instrument", action="store_true",
                      help="insert at instrument slot (first) instead of fx slot")
    addp.set_defaults(func=cmd_add_plugin)

    probe = sub.add_parser("probe-plugin",
                           help="instantiate a plugin, dump its parameters to a manifest")
    probe.add_argument("plugin")
    probe.add_argument("--out", help="manifest path (default ~/.musicbench/devices/<name>.json)")
    probe.set_defaults(func=cmd_probe_device)

    extp = sub.add_parser("ext-params",
                          help="list (and optionally set) a hosted plugin's parameters")
    extp.add_argument("device_id", type=int, metavar="DEVICE_ID")
    extp.add_argument("--set", nargs=2, metavar=("NAME", "VALUE"),
                      help="set a parameter by name (normalised 0..1)")
    extp.set_defaults(func=cmd_ext_params)

    mcp = sub.add_parser("mcp", help="register the ComposerBench MCP server with agent clients")
    mcp.add_argument("mcp_action", choices=["install", "config", "doctor"])
    mcp.add_argument("--client", action="append", default=None,
                     choices=["claude-desktop", "claude-code", "cursor", "all"],
                     help="repeatable; default: all")
    mcp.add_argument("--name", default="composerbench", help="server name in client configs")
    mcp.add_argument("--workspace", default=None,
                     help="where new projects go (default ~/Music/ComposerBench/projects)")
    mcp.add_argument("--dry-run", action="store_true", help="show changes without writing")
    mcp.add_argument("--toolsets", default=None,
                     help="limit the tools agents see: core,song,engine,audio,revision (default all)")
    mcp.set_defaults(func=cmd_mcp)

    from composerbench.cli.production import register
    register(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "cli", "unset") is None:
        from composerbench.engine import candidates, discover_cli
        found = discover_cli()
        args.cli = str(found or candidates()[0])   # a missing path reports "not found" downstream
    try:
        return asyncio.run(args.func(args))
    except ComposerBenchError as e:
        print(json.dumps(e.to_dict(), indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
