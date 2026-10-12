"""Build a song spec in MAGDA and render its mix and stems.

    report = await build_song("song.json")             # CLI: composerbench song build song.json
    report = check_song("song.json")                   # preflight only, no engine

What a build does, in order:

1. preflight: presets resolved, every device and parameter checked against
   the device catalog, notes checked against the clip, free disk space checked
   against the renders it will write (a full disk fails here, not mid-render);
2. one engine session (MagdaServeAdapter): tempo, then per track the
   instrument, its parameters (one request per device, labels or numbers,
   out-of-range numbers clamped and reported), the FX chain, one clip spanning
   the song and every note in a single bulk request;
3. one save, a read-back of every parameter that was set (mismatches listed),
   a note-count check per track;
4. renders: the full mix, then one WAV per stem (other tracks' faders at 0 for
   that render), each measured (peak, RMS, LUFS);
5. ``session.json`` (everything above), ``score.json`` (the notes as played)
   and ``spec.resolved.json`` next to the renders.

Problems on one track are recorded and the build carries on; the report's
``problems`` list says what to fix.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from composerbench.devices.catalog import check_value, device as catalog_device, load_catalog
from composerbench.errors import ComposerBenchError
from composerbench.song.spec import (ResolvedTrack, SongSpec, load_score, load_spec, resolve, song_bpm,
                                  song_length)

MARKER = ".musicbench_song"
SAMPLE_RATE_GUESS = 44100


class SongSpecInvalid(ComposerBenchError):
    code = "SONG_SPEC_INVALID"


class InsufficientDiskSpace(ComposerBenchError):
    code = "INSUFFICIENT_DISK_SPACE"


Progress = Callable[[str, dict], None]


def _stderr_progress(stage: str, info: dict) -> None:
    detail = " ".join(f"{k}={v}" for k, v in info.items() if not isinstance(v, (dict, list)))
    print(f"[song] {stage} {detail}", file=sys.stderr, flush=True)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "song"


def stems_of(tracks: list[ResolvedTrack], spec: SongSpec) -> list[str]:
    present = list(dict.fromkeys(t.stem for t in tracks))
    if spec.render.stems is True:
        return present
    if spec.render.stems is False:
        return []
    return list(spec.render.stems)


def render_bytes(seconds: float, renders: int, bit_depth: int, channels: int = 2) -> int:
    return int(seconds * SAMPLE_RATE_GUESS * channels * (bit_depth // 8) * renders)


def free_bytes(path: Path) -> int:
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _existing_bytes(paths) -> int:
    """Bytes already used by files a build is about to overwrite."""
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    return total


def _default_out(spec: SongSpec) -> Path:
    return Path.home() / "Music" / "ComposerBench" / slug(spec.title)


def check_song(source, out_dir: str | Path | None = None, catalog: dict | None = None) -> dict:
    """Preflight a spec without an engine: resolution, devices, parameters,
    notes, stems and disk space. ``ok`` is False when anything would fail."""
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []
    try:
        spec, base = load_spec(source)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "errors": [f"spec: {e}"], "warnings": [], "info": []}
    cat = catalog or load_catalog()
    try:
        score = load_score(spec, base)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "errors": [f"score: {e}"], "warnings": [], "info": []}
    try:
        tracks = resolve(spec, score)
        bpm = song_bpm(spec, score)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "errors": [str(e)], "warnings": [], "info": []}
    length = song_length(spec, score, tracks)
    clip_len = length + spec.render.tail_beats
    seen_plugins = set(cat.get("hosted_plugins_seen", []))
    summary = []
    for t in tracks:
        inst = t.instrument
        entry = catalog_device(inst.label, cat)
        if inst.device:
            if entry is None:
                warnings.append(f"{t.name}: instrument {inst.device!r} is not in the device catalog")
            elif entry["kind"] != "instrument":
                errors.append(f"{t.name}: {inst.device!r} is an effect, not an instrument")
        elif inst.plugin and seen_plugins and inst.plugin not in seen_plugins:
            warnings.append(f"{t.name}: plugin {inst.plugin!r} was not in the last plugin scan")
        chain = [(inst, "instrument")] + [(f, f"fx[{i}]") for i, f in enumerate(t.fx)]
        for dev, where in chain:
            if dev.device and where != "instrument":
                fx_entry = catalog_device(dev.device, cat)
                if fx_entry is not None and fx_entry["kind"] != "effect":
                    errors.append(f"{t.name}/{where}: {dev.device!r} is an instrument, not an effect")
            for pname, value in dev.params.items():
                c = check_value(dev.label, pname, value, cat)
                msg = f"{t.name}/{where} {dev.label} '{pname}' = {value!r}: {c.message}"
                if not c.ok:
                    errors.append(msg)
                elif c.severity == "warning":
                    warnings.append(msg)
        late = [n for n in t.notes if n.start + n.duration > clip_len + 1e-6]
        if late:
            errors.append(f"{t.name}: {len(late)} note(s) end after the clip ({clip_len} beats), "
                          f"first at beat {late[0].start}")
        if not t.notes:
            warnings.append(f"{t.name}: no notes (silent track)")
        summary.append({"name": t.name, "preset": t.preset, "instrument": inst.label,
                        "fx": [f.label for f in t.fx], "volume": t.volume, "stem": t.stem,
                        "notes": len(t.notes)})
    stems = stems_of(tracks, spec)
    unknown = [s for s in stems if s not in {t.stem for t in tracks}]
    if unknown:
        errors.append(f"render.stems names stems no track uses: {unknown}")
    seconds = (length + spec.render.tail_beats) * 60.0 / bpm
    renders = int(spec.render.mix) + len(stems)
    out = Path(out_dir or spec.out_dir or _default_out(spec)).expanduser()
    reclaim = _existing_bytes([out / "mix.wav"] + [out / "stems" / f"{s}.wav" for s in stems])
    need = max(0, render_bytes(seconds, renders, spec.render.bit_depth) - reclaim) + 20 * 2**20
    free = free_bytes(out)
    disk = {"out_dir": str(out), "needed_mb": round(need / 2**20, 1), "free_mb": round(free / 2**20, 1),
            "keep_free_mb": spec.render.min_free_mb}
    if free - need < spec.render.min_free_mb * 2**20:
        errors.append(f"not enough disk space: renders need ~{disk['needed_mb']} MB and "
                      f"{spec.render.min_free_mb} MB must stay free; {disk['free_mb']} MB available at {out}")
    if score is not None and score.chords:
        from composerbench.analysis.harmony import clash_report
        h = clash_report(score)
        info.append(f"harmony: {h['summary']}")
    info.append(f"{len(tracks)} tracks, {sum(len(t.notes) for t in tracks)} notes, {length:g} beats at "
                f"{bpm:g} BPM ({seconds:.1f} s with tail), {renders} render(s): "
                f"{'mix + ' if spec.render.mix else ''}{', '.join(stems) or 'no stems'}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info,
            "title": spec.title, "bpm": bpm, "length_beats": length, "clip_beats": clip_len,
            "stems": stems, "tracks": summary, "disk": disk}


async def build_song(source, out_dir: str | Path | None = None, cli_path: str | Path | None = None,
                     adapter=None, progress: Progress | None = None, force: bool = False,
                     stems: bool | list[str] | None = None) -> dict:
    """Build and render a song spec. Returns the session report (also written
    to <out_dir>/session.json). ``adapter`` injects an already-constructed
    engine adapter (tests); otherwise a MagdaServeAdapter is started on the
    discovered ``magda_cli`` and shut down afterwards."""
    say = progress or _stderr_progress
    t_start = time.time()
    spec, base = load_spec(source)
    if stems is not None:
        spec = spec.model_copy(update={"render": spec.render.model_copy(update={"stems": stems})})
    out = Path(out_dir or spec.out_dir or _default_out(spec)).expanduser()
    spec_dict = json.loads(spec.model_dump_json(exclude_none=True))
    spec_dict["score"] = _abs_score(spec, base)
    check = check_song(spec_dict, out)
    if not check["ok"] and not force:
        raise SongSpecInvalid(errors=check["errors"], warnings=check["warnings"][:20])
    say("preflight", {"ok": check["ok"], "warnings": len(check["warnings"])})
    score = load_score(spec, base)
    tracks = resolve(spec, score)
    bpm = song_bpm(spec, score)
    length = song_length(spec, score, tracks)
    clip_len = length + spec.render.tail_beats
    stem_names = stems_of(tracks, spec)

    out.mkdir(parents=True, exist_ok=True)
    project_dir = out / "project"
    if project_dir.exists():
        if not (out / MARKER).exists():
            raise SongSpecInvalid(errors=[f"{project_dir} exists and was not made by the song builder; "
                                          "choose another out_dir"])
        shutil.rmtree(project_dir)
    (out / MARKER).write_text(json.dumps({"title": spec.title, "built": time.time()}))
    (out / "stems").mkdir(exist_ok=True)
    played = {t.name: [n.model_dump() for n in t.notes] for t in tracks}
    score_out = {"title": spec.title, "bpm": bpm, "beats_per_bar": spec.beats_per_bar,
                 "length_beats": length, "render_beats": clip_len,
                 "sections": [s.to_dict() for s in score.sections] if score else [],
                 "chords": [c.to_dict() for c in score.chords] if score else [],
                 "parts": played, "stems": {t.name: t.stem for t in tracks}}
    (out / "score.json").write_text(json.dumps(score_out))
    (out / "spec.resolved.json").write_text(json.dumps(
        {"title": spec.title, "bpm": bpm, "length_beats": length,
         "tracks": [t.model_dump(exclude={"notes"}) for t in tracks]}, indent=1))

    report: dict[str, Any] = {"title": spec.title, "bpm": bpm, "length_beats": length,
                              "clip_beats": clip_len, "out_dir": str(out), "preflight": check,
                              "problems": [], "param_results": {}, "readback_mismatch": [],
                              "tracks": {}, "renders": {}, "timings": {}}

    def write_report():
        report["timings"]["total"] = round(time.time() - t_start, 1)
        (out / "session.json").write_text(json.dumps(report, indent=1, default=str))

    own = adapter is None
    daw = adapter
    if own:
        from composerbench.adapter.magda_serve import MagdaServeAdapter
        from composerbench.mcp.server import discover_cli
        cli = discover_cli(str(cli_path) if cli_path else None)
        if cli is None:
            from composerbench.errors import MagdaCliNotFound
            raise MagdaCliNotFound(hint="build magda_cli or pass --cli / set COMPOSERBENCH_CLI")
        daw = MagdaServeAdapter(cli, project_dir / f"{slug(spec.title)}.mgd")
    try:
        t0 = time.time()
        await daw.initialize()
        if hasattr(daw, "_ensure_session"):
            await daw._ensure_session()
        report["engine"] = {"features": await daw.features() if hasattr(daw, "features") else {},
                            "project": str(getattr(daw, "project", ""))}
        caps = daw.capabilities()
        if getattr(caps, "explicit_save", False) and hasattr(daw, "autosave"):
            daw.autosave = False
        await daw.set_project_tempo(bpm)
        report["timings"]["boot"] = round(time.time() - t0, 1)
        say("engine", {"boot_s": report["timings"]["boot"]})

        t0 = time.time()
        ids: dict[str, str] = {}
        for t in tracks:
            try:
                ids[t.name], devs = await _build_track(daw, t, clip_len, report)
                say("track", {"name": t.name, "devices": len(devs), "notes": len(t.notes)})
            except Exception as e:  # noqa: BLE001 - keep building the rest
                report["problems"].append(f"{t.name}: {type(e).__name__}: {getattr(e, 'message', e)}"[:500])
                say("track-failed", {"name": t.name, "error": str(e)[:200]})
        await daw.set_track_volumes({ids[t.name]: t.volume for t in tracks if t.name in ids})
        if hasattr(daw, "save_project"):
            await daw.save_project()
        report["timings"]["build"] = round(time.time() - t0, 1)
        await _verify(daw, tracks, ids, report)
        write_report()

        t0 = time.time()
        jobs = ([("mix", out / "mix.wav", None)] if spec.render.mix else []) + \
               [(f"stem:{s}", out / "stems" / f"{s}.wav", s) for s in stem_names]
        per_render = render_bytes((clip_len * 60.0 / bpm), 1, spec.render.bit_depth)
        for label, path, stem in jobs:
            free = free_bytes(out) + _existing_bytes([path])
            if free - per_render < spec.render.min_free_mb * 2**20:
                raise InsufficientDiskSpace(needed_mb=round(per_render / 2**20, 1),
                                            free_mb=round(free / 2**20, 1), render=label,
                                            hint="free disk space or render fewer stems")
            volumes = {ids[t.name]: (t.volume if stem in (None, t.stem) else 0.0)
                       for t in tracks if t.name in ids}
            await daw.set_track_volumes(volumes)
            r0 = time.time()
            from composerbench.adapter.base import RenderRequest
            await daw.render(RenderRequest(output_path=path, start_beats=0.0, length_beats=clip_len,
                                           bit_depth=spec.render.bit_depth))
            stats = _measure(path)
            stats["render_seconds"] = round(time.time() - r0, 1)
            stats["path"] = str(path)
            report["renders"][label] = stats
            say("render", {"what": label, "seconds": stats["render_seconds"],
                           "peak_dbfs": stats.get("peak_dbfs"), "lufs": stats.get("lufs")})
            write_report()
        if stem_names:
            await daw.set_track_volumes({ids[t.name]: t.volume for t in tracks if t.name in ids})
            if hasattr(daw, "save_project"):
                await daw.save_project()
        report["timings"]["render"] = round(time.time() - t0, 1)
        _sanity(report, tracks)
    finally:
        if own and daw is not None:
            try:
                await daw.shutdown()
            except Exception:  # noqa: BLE001
                pass
        write_report()
    say("done", {"seconds": report["timings"]["total"], "problems": len(report["problems"])})
    return report


def _abs_score(spec: SongSpec, base: Path):
    """The spec's score as an absolute path (or the inline dict / None)."""
    if isinstance(spec.score, str):
        path = Path(spec.score).expanduser()
        return str(path if path.is_absolute() else (base / path).resolve())
    return spec.score


async def _device_id(daw, track_id: str, name: str, before: set) -> str:
    devs = await daw.get_devices_full(track_id)
    new = [d for d in devs if str(d.get("id")) not in before]
    match = [d for d in new if d.get("name") == name] or new
    if not match:
        raise RuntimeError(f"device {name!r} did not appear on the track")
    return str(match[-1]["id"])


async def _build_track(daw, t: ResolvedTrack, clip_len: float, report: dict) -> tuple[str, list]:
    from composerbench.model import Note
    track = await daw.create_track(t.name)
    tid = track.id
    inst = t.instrument
    results: list[dict] = []
    devices: list[tuple[str, str, dict]] = []
    if inst.device:
        before = {str(d.get("id")) for d in await daw.get_devices_full(tid)}
        await daw.load_device(tid, inst.device)
        dev_id = await _device_id(daw, tid, inst.device, before)
    else:
        plugins = await daw.list_plugins()
        found = [p for p in plugins if inst.plugin in (p.get("id"), p.get("name"))]
        if not found:
            raise RuntimeError(f"plugin {inst.plugin!r} not found; scanned: "
                               f"{sorted(p.get('name') for p in plugins)[:20]}")
        dev_id = str(await daw.add_plugin(tid, found[0]["id"], as_instrument=True))
        if inst.init_wait_seconds:
            await asyncio.sleep(inst.init_wait_seconds)
    devices.append((dev_id, inst.label, inst.params))
    for f in t.fx:
        fid = await daw.add_fx(tid, f.label)
        if f.init_wait_seconds:
            await asyncio.sleep(f.init_wait_seconds)
        devices.append((str(fid), f.label, f.params))
    for dev_id, label, params in devices:
        if params:
            res = await daw.set_parameters(tid, dev_id, params)
            for r in res:
                r["device"] = label
                r["device_id"] = dev_id
                if not r.get("ok"):
                    report["problems"].append(f"{t.name}/{label} '{r['param']}': {r.get('error')}")
                elif r.get("how") == "clamped":
                    report["problems"].append(f"{t.name}/{label} '{r['param']}': {r['requested']} clamped "
                                              f"to {r['native']}")
            results += res
    clip = await daw.create_clip(tid, t.name, 0.0, clip_len)
    if t.notes:
        await daw.add_notes(tid, clip.id, [Note(start=n.start, duration=n.duration, pitch=n.pitch,
                                                velocity=n.velocity) for n in t.notes])
    report["param_results"][t.name] = results
    report["tracks"][t.name] = {"id": tid, "stem": t.stem, "volume": t.volume, "preset": t.preset,
                                "instrument": inst.label, "fx": [f.label for f in t.fx],
                                "devices": [{"id": d, "name": n} for d, n, _ in devices],
                                "notes_written": len(t.notes)}
    return tid, devices


async def _verify(daw, tracks: list[ResolvedTrack], ids: dict, report: dict) -> None:
    """Read back every parameter that was set and count the notes per track."""
    for t in tracks:
        if t.name not in ids:
            continue
        tid = ids[t.name]
        try:
            live_notes = len(await daw.get_notes(tid))
        except Exception as e:  # noqa: BLE001
            live_notes = None
            report["problems"].append(f"{t.name}: note read-back failed: {e}")
        report["tracks"][t.name]["notes_live"] = live_notes
        if live_notes is not None and live_notes != len(t.notes):
            report["problems"].append(f"{t.name}: wrote {len(t.notes)} notes, project has {live_notes}")
        by_device: dict[str, list[dict]] = {}
        for r in report["param_results"].get(t.name, []):
            if r.get("ok") and "native" in r:
                by_device.setdefault(r["device_id"], []).append(r)
        for dev_id, rows in by_device.items():
            try:
                params = await daw.get_parameters(tid, dev_id)
            except Exception as e:  # noqa: BLE001
                report["problems"].append(f"{t.name}: parameter read-back failed on device {dev_id}: {e}")
                continue
            by_name: dict[str, list] = {}
            for meta in params.values():
                by_name.setdefault(str(meta.get("name", "")).lower(), []).append(meta)
            for r in rows:
                metas = by_name.get(str(r["param"]).lower())
                if not metas:
                    continue
                own = [m for m in metas if m.get("owner") != "host"] or metas
                got = own[0].get("value")
                if got is None:
                    continue
                want = float(r["native"])
                if abs(float(got) - want) > 1e-3 * max(1.0, abs(want)):
                    report["readback_mismatch"].append({"track": t.name, "device": r["device"],
                                                        "param": r["param"], "wanted": want, "got": got})


def _measure(path: Path) -> dict:
    from composerbench.analysis.audio import analyze_wav
    try:
        stats = analyze_wav(path).to_dict()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    try:
        from composerbench.analysis.wav import read_wav
        from composerbench.mixing.loudness import integrated_lufs, true_peak_db
        x, sr = read_wav(path)
        if len(x) >= sr // 2:
            stats["lufs"] = round(integrated_lufs(x, sr), 2)
            stats["true_peak_dbtp"] = round(true_peak_db(x), 2)
    except Exception as e:  # noqa: BLE001 - loudness is optional
        stats["lufs_error"] = str(e)[:200]
    return stats


def _sanity(report: dict, tracks: list[ResolvedTrack]) -> None:
    """Flag renders that are silent or clipped."""
    for label, st in report["renders"].items():
        if st.get("error"):
            report["problems"].append(f"render {label}: {st['error']}")
            continue
        if st.get("duration_seconds", 0) > 0 and st.get("peak_dbfs", -120) <= -90:
            report["problems"].append(f"render {label} is silent (peak {st.get('peak_dbfs')} dBFS)")
        if st.get("peak_dbfs", -120) >= -0.01:
            report["problems"].append(f"render {label} reaches 0 dBFS: lower track volumes")


def build_song_sync(*args, **kwargs) -> dict:
    return asyncio.run(build_song(*args, **kwargs))
