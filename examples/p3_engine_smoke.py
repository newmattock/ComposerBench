"""Engine-level smoke test for MAGDA serve protocol 3 (items 5-10).

Drives `magda_cli serve` directly over its socket -- no ComposerBench adapter --
builds a small project that uses every new op, renders it and measures the
audio, then restarts the engine to check what was saved.

    .venv/bin/python examples/p3_engine_smoke.py [--cli PATH] [--out DIR]

Writes <out>/report.json (and ~/Music/ComposerBench/p3smoke/latest.json).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from composerbench.engine import discover_cli  # noqa: E402

CLI = discover_cli() or Path("magda_cli")
BPM = 120.0
BEAT = 60.0 / BPM


class Engine:
    def __init__(self, cli: Path, project: Path, sock: Path):
        self.cli, self.project, self.sock = cli, project, sock
        self.proc = None
        self.conn = None
        self.buf = b""

    def start(self):
        if self.sock.exists():
            self.sock.unlink()
        self.log = open(self.project.parent / "serve.log", "ab")
        self.proc = subprocess.Popen([str(self.cli), "serve", str(self.project), "--sock", str(self.sock)],
                                     stdout=self.log, stderr=self.log, stdin=subprocess.DEVNULL)
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"serve exited with {self.proc.returncode}")
            if self.sock.exists():
                try:
                    self.conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.conn.connect(str(self.sock))
                    self.req({"op": "ping"})
                    return
                except OSError:
                    self.conn = None
            time.sleep(0.2)
        raise RuntimeError("serve did not come up")

    def req(self, payload: dict, timeout: float = 600) -> dict:
        self.conn.settimeout(timeout)
        self.conn.sendall((json.dumps(payload) + "\n").encode())
        while b"\n" not in self.buf:
            chunk = self.conn.recv(1 << 20)
            if not chunk:
                raise RuntimeError("engine closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def ok(self, payload: dict, timeout: float = 600) -> dict:
        reply = self.req(payload, timeout)
        if not reply.get("ok", "tracks" in reply):
            raise RuntimeError(f"{payload.get('op')} failed: {reply.get('error')} <- {json.dumps(payload)[:300]}")
        return reply

    def stop(self):
        try:
            if self.conn:
                self.req({"op": "shutdown"}, timeout=30)
        except Exception:  # noqa: BLE001
            pass
        if self.proc:
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.conn = None


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float64) / 8388608.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    return data.reshape(-1, ch), rate


def db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def window(data: np.ndarray, rate: int, t0: float, t1: float) -> np.ndarray:
    return data[int(t0 * rate):int(t1 * rate)]


def onsets(mono: np.ndarray, rate: int, min_gap: float = 0.2) -> list[float]:
    hop = int(rate * 0.005)
    env = np.array([np.max(np.abs(mono[i:i + hop])) for i in range(0, len(mono) - hop, hop)])
    if env.size == 0 or env.max() <= 0:
        return []
    thr = env.max() * 0.3
    times, last = [], -1e9
    for i in range(1, len(env)):
        if env[i] >= thr and env[i - 1] < thr and (i * hop / rate - last) >= min_gap:
            last = i * hop / rate
            times.append(round(last, 3))
    return times


def centroid(mono: np.ndarray, rate: int) -> float:
    if mono.size < 1024:
        return 0.0
    spec = np.abs(np.fft.rfft(mono * np.hanning(mono.size)))
    freqs = np.fft.rfftfreq(mono.size, 1.0 / rate)
    return float(np.sum(freqs * spec) / max(np.sum(spec), 1e-12))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default=str(CLI))
    ap.add_argument("--out", default=str(Path("~/Music/ComposerBench/p3smoke").expanduser() / time.strftime("%Y%m%d-%H%M%S")))
    args = ap.parse_args()
    cli, out = Path(args.cli), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"checks": {}, "measure": {}, "errors": []}
    checks, measure = report["checks"], report["measure"]

    def check(name: str, passed: bool, detail=None):
        checks[name] = {"ok": bool(passed), **({"detail": detail} if detail is not None else {})}
        print(("PASS " if passed else "FAIL ") + name + (f"  {detail}" if detail is not None else ""), flush=True)

    project = out / "smoke.mgd"
    subprocess.run([str(cli), "init", str(project)], check=True, capture_output=True)
    if not project.exists():
        nested = out / "smoke" / "smoke.mgd"
        project = nested if nested.exists() else project
    eng = Engine(cli, project, Path(f"/tmp/p3smoke-{os.getpid()}.sock"))
    eng.start()
    try:
        d = eng.ok({"op": "describe"})
        report["describe"] = d
        check("describe.protocol3", d.get("protocol") == 3, d.get("protocol"))
        cmds = eng.ok({"op": "commands"})
        names = {c["name"] for c in cmds.get("commands", [])}
        check("commands.listed", {"set-track-pan", "add-send", "add-automation-point", "move-clip"} <= names, len(names))
        check("commands.summaries", all(c.get("summary") for c in cmds.get("commands", [])))
        cat = eng.ok({"op": "catalog"})
        inst = {i["id"] for i in cat["instruments"]}
        fx = {e["id"]: e for e in cat["effects"]}
        check("catalog.contents", "magda_polysynth" in inst and "sidechain" in fx and "magda_reverb" in fx,
              {"instruments": len(inst), "effects": len(fx)})

        eng.ok({"op": "exec", "commands": ["set-tempo", str(BPM)], "save": False})

        def track(kind: str, name: str) -> int:
            before = {t["id"] for t in eng.ok({"op": "dump"})["tracks"]}
            eng.ok({"op": "exec", "commands": ["add-track", kind, name], "save": False})
            return next(t["id"] for t in eng.ok({"op": "dump"})["tracks"] if t["id"] not in before)

        def instrument(tid: int, plugin: str):
            eng.ok({"op": "exec", "commands": ["add-internal-instrument", str(tid), plugin], "save": False})

        def clip(tid: int, start: float, length: float, notes: list[dict], **extra) -> dict:
            return eng.ok({"op": "clip", "action": "create", "trackId": tid, "start": start, "length": length,
                           "notes": notes, "save": False, **extra})["clip"]

        kick, pad, lead, hat = (track("audio", n) for n in ("Kick", "Pad", "Lead", "Hat"))
        verb = track("aux", "Verb")
        instrument(kick, "magda_kick"); instrument(pad, "magda_polysynth")
        instrument(lead, "magda_polysynth"); instrument(hat, "magda_hat")
        clip(kick, 0, 16, [{"start": b, "length": 0.25, "pitch": 36, "velocity": 120} for b in range(16)])
        pad_clip = clip(pad, 0, 16, [{"start": 0, "length": 16, "pitch": p, "velocity": 90} for p in (48, 55, 60, 64)],
                        name="Pad chord")
        check("clip.create.named", pad_clip.get("name") == "Pad chord", pad_clip.get("name"))
        clip(lead, 0, 16, [{"start": 0, "length": 16, "pitch": 57, "velocity": 100}])
        hat_clip = clip(hat, 0, 4, [{"start": b, "length": 0.1, "pitch": 42, "velocity": 110} for b in range(4)])
        eng.ok({"op": "clip", "action": "loop", "clipId": hat_clip["id"], "loopLength": 4, "length": 16, "save": False})

        # overlap policy
        r = eng.req({"op": "clip", "action": "create", "trackId": hat, "start": 8, "length": 4, "save": False})
        check("clip.overlap.error", not r.get("ok") and "overlap" in r.get("error", ""), r.get("error"))

        # mixer
        m = eng.ok({"op": "mixer", "trackId": kick, "volumeDb": -6, "save": False})["track"]
        check("mixer.volumeDb", abs(m["volume"] - 0.501) < 0.01, m)
        m = eng.ok({"op": "mixer", "trackId": pad, "pan": -0.8, "save": False})["track"]
        check("mixer.pan", abs(m["pan"] + 0.8) < 1e-6, m["pan"])
        r = eng.req({"op": "mixer", "trackId": "master", "solo": True})
        check("mixer.master.no_solo", not r.get("ok"), r.get("error"))

        # send + reverb on the aux (main chain), sidechain ducker on the pad
        eng.ok({"op": "add_fx", "trackId": verb, "plugin": "magda_reverb", "save": False})
        s = eng.ok({"op": "send", "from": pad, "to": verb, "levelDb": -6, "save": False})["track"]["sends"]
        check("send.added", len(s) == 1 and abs(s[0]["levelDb"] + 6) < 0.05, s)
        r = eng.req({"op": "send", "from": verb, "to": pad, "save": False})
        check("send.feedback.refused", not r.get("ok"), r.get("error"))
        ducker = eng.ok({"op": "add_fx", "trackId": pad, "plugin": "sidechain", "save": False})
        check("add_fx.ducker.pre", ducker.get("position") == "pre", ducker)
        sc = eng.ok({"op": "sidechain", "deviceId": ducker["device"], "source": kick, "save": False})
        check("sidechain.midi", sc["sidechain"] == {"type": "midi", "source": kick}, sc)
        post = eng.ok({"op": "add_fx", "trackId": lead, "plugin": "magda_eq", "position": "post", "save": False})
        r = eng.req({"op": "sidechain", "deviceId": post["device"], "source": kick, "type": "audio"})
        check("sidechain.post_refused", not r.get("ok"), r.get("error"))

        # automation: lead fade-in and a cutoff sweep on the lead synth
        a = eng.ok({"op": "automation", "target": {"kind": "volume", "trackId": lead},
                    "points": [{"beat": 0, "value": -40}, {"beat": 8, "value": 0}], "save": False})
        check("automation.volume", a.get("points") == 2, a)
        dump = eng.ok({"op": "dump"})
        lead_synth = next(dv for dv in dump["devices"] if dv["trackId"] == lead and dv.get("pluginId") == "magda_polysynth")
        cutoff = next((p for p in lead_synth["parameters"] if "cutoff" in p["name"].lower()), None)
        report["lead_cutoff_param"] = cutoff
        if cutoff:
            lo, hi = cutoff["min"], cutoff["max"]
            eng.ok({"op": "automation", "target": {"kind": "param", "deviceId": lead_synth["id"], "param": cutoff["name"]},
                    "points": [{"beat": 8, "value": lo + (hi - lo) * 0.05}, {"beat": 16, "value": hi}], "save": False})
        r = eng.req({"op": "automation", "target": {"kind": "param", "deviceId": post["device"], "param": 0},
                     "points": [{"beat": 0, "value": 0}]})
        check("automation.postfx_refused", not r.get("ok"), r.get("error"))

        # master bus
        lim = eng.ok({"op": "add_fx", "trackId": "master", "plugin": "magda_limiter", "save": False})
        dump = eng.ok({"op": "dump"})
        check("master.insert", lim["device"] in dump.get("master", {}).get("devices", []), dump.get("master"))
        check("dump.automation", len(dump.get("automation", [])) >= 2, len(dump.get("automation", [])))
        eng.ok({"op": "save"})

        # ---- renders ----------------------------------------------------------
        def render(name: str, **extra) -> tuple[np.ndarray, int]:
            path = out / f"{name}.wav"
            eng.ok({"op": "render", "wav": str(path), "bitDepth": 16, "to": "16beats", **extra})
            return read_wav(path)

        mix, rate = render("mix")
        measure["mix_peak_db"] = db(float(np.max(np.abs(mix))))
        stems = eng.ok({"op": "render_stems", "dir": str(out / "stems"), "bitDepth": 16, "to": "16beats"})["stems"]
        report["stems"] = stems
        levels = {}
        for st in stems:
            data, _ = read_wav(Path(st["file"]))
            levels[st["name"]] = round(db(rms(data)), 1)
        measure["stem_rms_db"] = levels
        check("stems.all_audible", len(stems) == 5 and all(v > -60 for v in levels.values()), levels)

        pad_stem, _ = read_wav(next(Path(s["file"]) for s in stems if s["trackId"] == pad))
        mono = pad_stem.mean(axis=1)
        early = np.mean([rms(window(mono, rate, b * BEAT + 0.02, b * BEAT + 0.08)) for b in range(2, 14)])
        late = np.mean([rms(window(mono, rate, b * BEAT + 0.38, b * BEAT + 0.46)) for b in range(2, 14)])
        measure["pad_duck_early_late_db"] = round(db(early) - db(late), 1)
        check("sidechain.ducks_pad", db(early) - db(late) < -6, measure["pad_duck_early_late_db"])
        left, right = rms(pad_stem[:, 0]), rms(pad_stem[:, 1])
        measure["pad_left_right_db"] = round(db(left) - db(right), 1)
        check("pan.left", db(left) - db(right) > 6, measure["pad_left_right_db"])

        lead_stem, _ = read_wav(next(Path(s["file"]) for s in stems if s["trackId"] == lead))
        lm = lead_stem.mean(axis=1)
        first, eighth = rms(window(lm, rate, 0.0, BEAT)), rms(window(lm, rate, 7 * BEAT, 8 * BEAT))
        measure["lead_fade_db"] = round(db(eighth) - db(first), 1)
        check("automation.fade_in_audible", db(eighth) - db(first) > 15, measure["lead_fade_db"])
        if cutoff:
            c1, c2 = centroid(window(lm, rate, 8 * BEAT, 9 * BEAT), rate), centroid(window(lm, rate, 15 * BEAT, 16 * BEAT), rate)
            measure["lead_centroid_hz"] = [round(c1), round(c2)]
            check("automation.cutoff_sweep", c2 > c1 * 1.3, measure["lead_centroid_hz"])

        hat_stem, _ = read_wav(next(Path(s["file"]) for s in stems if s["trackId"] == hat))
        hits = onsets(hat_stem.mean(axis=1), rate)
        measure["hat_onsets"] = len(hits)
        check("clip.loop_repeats", 14 <= len(hits) <= 17, hits[:20])

        verb_stem = levels.get("Verb", -200)
        check("send.reaches_aux", verb_stem > -60, verb_stem)

        # master volume: -6 dB halves the mix (limiter off for a clean ratio)
        eng.ok({"op": "set_ext_bypass", "deviceId": lim["device"], "bypassed": True, "save": False})
        a_mix, _ = render("mix_master_0db")
        eng.ok({"op": "mixer", "trackId": "master", "volumeDb": -6})
        b_mix, _ = render("mix_master_-6db")
        measure["master_ratio_db"] = round(db(rms(b_mix)) - db(rms(a_mix)), 2)
        check("master.volume", abs(measure["master_ratio_db"] + 6) < 0.6, measure["master_ratio_db"])
        eng.ok({"op": "set_ext_bypass", "deviceId": lim["device"], "bypassed": False})

        # clip edits after the renders
        mv = eng.ok({"op": "clip", "action": "create", "trackId": lead, "start": 32, "length": 4, "save": False})["clip"]
        moved = eng.ok({"op": "clip", "action": "move", "clipId": mv["id"], "start": 40, "trackId": pad, "save": False})["clip"]
        check("clip.move_across_tracks", moved["trackId"] == pad and abs(moved["startBeat"] - 40) < 1e-6, moved)
        dup = eng.ok({"op": "clip", "action": "duplicate", "clipId": hat_clip["id"], "start": 16, "save": False})["clip"]
        check("clip.duplicate_keeps_loop", abs(dup["startBeat"] - 16) < 1e-6 and dup.get("loopEnabled"), dup)
        rep = eng.ok({"op": "clip", "action": "create", "trackId": kick, "start": 4, "length": 4,
                      "overlap": "replace", "notes": [], "save": False})
        kick_clips = [c for t in eng.ok({"op": "dump"})["tracks"] if t["id"] == kick for c in t["clips"]]
        check("clip.overlap.replace", len(kick_clips) == 3, [(c["startBeat"], c["lengthBeats"]) for c in kick_clips])
        eng.ok({"op": "save"})
        before = eng.ok({"op": "dump"})
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"{type(e).__name__}: {e}")
        print("ERROR", e, flush=True)
        before = None
    finally:
        eng.stop()

    # ---- persistence ---------------------------------------------------------
    if before is not None:
        eng2 = Engine(cli, project, Path(f"/tmp/p3smoke-{os.getpid()}-b.sock"))
        try:
            eng2.start()
            after = eng2.ok({"op": "dump"})
            check("persist.master_volume", abs(after["master"]["volumeDb"] + 6) < 0.1, after["master"])
            check("persist.master_insert", len(after["master"].get("devices", [])) == 1, after["master"].get("devices"))
            pad_t = next(t for t in after["tracks"] if t["name"] == "Pad")
            check("persist.sends", len(pad_t.get("sends", [])) == 1, pad_t.get("sends"))
            check("persist.pan", abs(pad_t["pan"] + 0.8) < 1e-6, pad_t["pan"])
            check("persist.automation", len(after.get("automation", [])) == len(before.get("automation", [])),
                  [len(before.get("automation", [])), len(after.get("automation", []))])
            keyed = [dv for dv in after["devices"] if dv.get("sidechain")]
            check("persist.sidechain", len(keyed) == 1 and keyed[0]["sidechain"]["type"] == "midi", keyed[:1])
            loops = [c for t in after["tracks"] for c in t["clips"] if c.get("loopEnabled")]
            check("persist.clip_loop", len(loops) == 2, len(loops))
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"reload: {type(e).__name__}: {e}")
            print("ERROR reload", e, flush=True)
        finally:
            eng2.stop()

    report["passed"] = sum(1 for c in checks.values() if c["ok"])
    report["failed"] = [k for k, c in checks.items() if not c["ok"]]
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    latest = Path("~/Music/ComposerBench/p3smoke/latest.json").expanduser()
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(report, indent=1, default=str))
    print(f"\n{report['passed']} passed, {len(report['failed'])} failed, {len(report['errors'])} errors -> {out}")
    return 0 if not report["failed"] and not report["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
