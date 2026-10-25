#!/usr/bin/env python3
"""A tiny stand-in for `magda_cli` (init / serve) used by adapter tests.

It speaks the serve socket protocol closely enough to exercise
MagdaServeAdapter without the real engine:

    EMU_PROTOCOL=1  legacy server: no describe/notes/save flag; the live dump
                    carries `"choices": null` (only the saved .mgd has the
                    labels); external plugins live outside the device model
                    and are addressed by name only
    EMU_PROTOCOL=2  protocol 2 (default)
    EMU_CATALOG=1   every internal device gets its full parameter list from
                    ComposerBench's device catalog (otherwise only devices missing
                    from DEVICE_SPECS do)
    EMU_RENDER_AUDIO=1  renders write a sine burst per audible note instead of
                    an empty WAV

Hosted plugins mimic Tracktion: a host Dry Level / Wet Level wrapper pair
comes first, then the plugin's own parameters (Dragonfly has its own
"Dry Level" too). Surge's filter type is a labelled parameter that the
plugin does not declare as discrete, so its labels only come back from
`param_info` -- at Surge's own positions (0.005 + 0.99 * i / (n - 1)).

Every request is tallied in <project>.emu.json so tests can assert how the
adapter talked to the engine (requests per op, saves, notes per request).
"""
import json
import os
import socket
import sys
import zlib
from pathlib import Path

PROTOCOL = int(os.environ.get("EMU_PROTOCOL", "2"))

SURGE_FILTERS = ["Off", "LP 12 dB", "LP 24 dB", "LP Legacy Ladder", "HP 12 dB", "HP 24 dB"]


def P(name, value, lo=0.0, hi=1.0, unit="", choices=None, **extra):
    return {"name": name, "value": value, "min": lo, "max": hi, "unit": unit,
            "choices": choices, **extra}


HOST_PAIR = [P("Dry Level", 0.0, owner="host"), P("Wet Level", 1.0, owner="host")]

DEVICE_SPECS = {
    "magda_polysynth": [P("Cutoff", 3000.0, 50.0, 18000.0, "Hz"),
                        P("Voice Mode", 0.0, 0.0, 2.0, choices=["Poly", "Mono", "Legato"])],
    "magda_reverb": [P("Engine", 0.0, 0.0, 2.0, choices=["Plate", "Hall", "Room"]),
                     P("Mix", 0.3)],
    "magda_delay": [P("Division", 8.0, 0.0, 13.0,
                      choices=["1/32", "1/16T", "1/16", "1/8T", "1/16.", "1/8", "1/4T", "1/8.",
                               "1/4", "1/2T", "1/4.", "1/2", "1/2.", "1/1"])],
    "Surge XT": HOST_PAIR + [
        P("A Filter 1 Type", 0.005, hidden_labels=SURGE_FILTERS),
        P("A Filter 1 Cutoff", 0.4846),
        P("B Filter 1 Cutoff", 0.4846)],
    "Dragonfly Hall Reverb": HOST_PAIR + [
        P("Dry Level", 0.8), P("Early Level", 0.1), P("Late Level", 0.2)],
}

PLUGINS = [{"id": "VST3-Surge XT-1", "name": "Surge XT", "format": "VST3", "isInstrument": True},
           {"id": "VST3-Dragonfly Hall Reverb-2", "name": "Dragonfly Hall Reverb", "format": "VST3",
            "isInstrument": False}]


def catalog_specs(name):
    """Devices the table above does not cover get their parameters from
    ComposerBench's device catalog (names, ranges, defaults, labels)."""
    try:
        from composerbench.devices.catalog import device
    except Exception:
        return []
    entry = device(name)
    if not entry or entry.get("format", "internal") != "internal":
        return []
    out = []
    for p in entry.get("params", []):
        lo = p.get("min") if p.get("min") is not None else 0.0
        hi = p.get("max") if p.get("max") is not None else 1.0
        default = p.get("default") if p.get("default") is not None else lo
        out.append(P(p["name"], float(default), float(lo), float(hi), p.get("unit") or "",
                     list(p["choices"]) if p.get("choices") else None))
    return out


def render_audio(project, req):
    """EMU_RENDER_AUDIO=1: a short sine burst per note on every audible track
    (instrument loaded, volume > 0), so stem/mute logic can be checked."""
    import math
    import struct
    import wave
    sr = 44100
    spb = 60.0 / project.tempo

    def beats(v, default):
        if v is None:
            return default
        return float(str(v).replace("beats", ""))
    start = beats(req.get("from"), 0.0)
    end = beats(req.get("to"), max([c["startBeat"] + c["lengthBeats"] for c in project.clips] or [4.0]))
    n = int((end - start) * spb * sr)
    buf = [0.0] * n
    burst = int(0.05 * sr)
    for t in project.tracks:
        if t["volume"] <= 0 or not any(d["trackId"] == t["id"] and d["type"] == "instrument"
                                       for d in project.devices):
            continue
        for c in project.clips:
            if c["trackId"] != t["id"]:
                continue
            for note in c["notes"]:
                i0 = int(((c["startBeat"] + note["startBeat"]) - start) * spb * sr)
                f = 440.0 * 2 ** ((note["note"] - 69) / 12)
                amp = 0.05 * t["volume"] * note["velocity"] / 127
                for k in range(burst):
                    if 0 <= i0 + k < n:
                        buf[i0 + k] += amp * math.sin(2 * math.pi * f * k / sr)
    with wave.open(req["wav"], "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<hh", v, v) for v in
                               (int(max(-1.0, min(1.0, x)) * 32767) for x in buf)))


def surge_position(i, n):
    return 0.005 + 0.99 * i / (n - 1)


def display_text(p):
    labels = p.get("choices") or p.get("hidden_labels")
    if not labels:
        return f"{p['value']:.3g}{(' ' + p['unit']) if p.get('unit') else ''}"
    if p.get("hidden_labels"):
        n = len(labels)
        i = int((p["value"] - 0.005) / 0.99 * (n - 1) + 0.5)
    else:
        i = round((p["value"] - p["min"]) / ((p["max"] - p["min"]) or 1) * (len(labels) - 1))
    return labels[max(0, min(len(labels) - 1, i))]


class Project:
    def __init__(self, path: Path):
        self.path = path
        self.stats_path = path.with_name(path.name + ".emu.json")
        self.stats = {"requests": {}, "saves": 0, "max_notes_per_request": 0, "exec_tokens": 0}
        self.tempo = 120.0
        self.tracks = []          # {id, name, volume}
        self.clips = []           # {id, trackId, startBeat, lengthBeats, notes[]}
        self.devices = []         # {id, trackId, name, type, format, params[]}
        self.ext = {}             # protocol-1 plugins outside the model: id -> params[]
        self.next_id = 1
        if path.exists():
            self._load()

    def nid(self):
        self.next_id += 1
        return self.next_id - 1

    # ---- persistence -----------------------------------------------------
    def _load(self):
        doc = json.loads(zlib.decompress(self.path.read_bytes()))
        self.tempo = doc["project"]["tempo"]
        self.next_id = doc.get("nextId", 1)
        self.clips = doc.get("clips", [])
        self.tracks, self.devices = [], []
        for t in doc["tracks"]:
            self.tracks.append({"id": t["id"], "name": t["name"], "volume": t["volume"]})
            for slot in ("chainElements", "postFxChainElements"):
                for el in t.get(slot, []):
                    dev = el["device"] if slot == "chainElements" else el
                    self.devices.append({"id": dev["id"], "trackId": t["id"], "name": dev["name"],
                                         "type": "instrument" if slot == "chainElements" else "effect",
                                         "format": dev.get("formatName", "internal"),
                                         "params": [dict(p) for p in dev["parameters"]]})

    def save(self):
        self.stats["saves"] += 1
        if self.stats_path.parent.exists():
            self.stats_path.write_text(json.dumps(self.stats))
        tracks = []
        for t in self.tracks:
            chain = [{"type": "device", "device": self._mgd_device(d)}
                     for d in self.devices if d["trackId"] == t["id"] and d["type"] == "instrument"]
            post = [self._mgd_device(d) for d in self.devices
                    if d["trackId"] == t["id"] and d["type"] == "effect"]
            tracks.append({**t, "chainElements": chain, "postFxChainElements": post})
        doc = {"project": {"name": self.path.stem, "tempo": self.tempo}, "tracks": tracks,
               "clips": self.clips, "nextId": self.next_id}
        self.path.write_bytes(zlib.compress(json.dumps(doc).encode()))

    @staticmethod
    def _mgd_device(d):
        # The saved project keeps labels (as MAGDA's .mgd does) plus our own
        # bookkeeping fields so a reload restores everything.
        return {"id": d["id"], "name": d["name"], "pluginId": d["name"], "formatName": d["format"],
                "parameters": [{**p, "paramIndex": p["index"], "currentValue": p["value"],
                                "minValue": p["min"], "maxValue": p["max"], "defaultValue": p["default"],
                                "choices": p.get("choices") or []}
                               for p in d["params"]]}

    def tally(self, op, notes=0, tokens=0):
        self.stats["requests"][op] = self.stats["requests"].get(op, 0) + 1
        self.stats["max_notes_per_request"] = max(self.stats["max_notes_per_request"], notes)
        self.stats["exec_tokens"] += tokens
        self.stats_path.write_text(json.dumps(self.stats))

    # ---- model ----------------------------------------------------------------
    @staticmethod
    def make_params(spec):
        specs = DEVICE_SPECS.get(spec)
        if specs is None or (os.environ.get("EMU_CATALOG") and catalog_specs(spec)):
            specs = catalog_specs(spec)
        return [{**p, "index": i, "default": p["value"]} for i, p in enumerate(specs)]

    def add_device(self, track_id, name, kind, fmt="internal", spec=None):
        dev = {"id": self.nid(), "trackId": track_id, "name": name, "type": kind,
               "format": fmt, "params": self.make_params(spec or name)}
        self.devices.append(dev)
        return dev

    def clip(self, cid):
        return next(c for c in self.clips if c["id"] == cid)

    def dump(self):
        def param_view(p):
            view = {k: p[k] for k in ("index", "name", "value", "min", "max")}
            if PROTOCOL >= 2:
                view["unit"] = p.get("unit") or ""
                view["text"] = display_text(p)
                view["default"] = p["default"]
                if p.get("owner"):
                    view["owner"] = p["owner"]
                if p.get("choices"):
                    view["choices"] = p["choices"]
                    view["steps"] = len(p["choices"])
            else:   # what the legacy binary really emits
                view.update(unit=p.get("unit") or None, stableId=None, choices=None)
            return view
        return {"name": self.path.stem, "tempo": self.tempo,
                "tracks": [{**t, "clips": [dict(c) for c in self.clips if c["trackId"] == t["id"]]}
                           for t in self.tracks],
                "devices": [{"id": d["id"], "trackId": d["trackId"], "name": d["name"], "type": d["type"],
                             "format": d["format"], "parameters": [param_view(p) for p in d["params"]]}
                            for d in self.devices]}

    def find_param(self, params, ref):
        """Like MAGDA: index, else the plugin's own parameter by name, else the
        engine-wrapper pair (also reachable as "host:<name>")."""
        ref = str(ref)
        by_index = [p for p in params if str(p["index"]) == ref]
        if by_index:
            return by_index[0]
        host_only = ref.lower().startswith("host:")
        name = ref[5:].strip() if host_only else ref
        own = [] if host_only else [p for p in params if not p.get("owner") and p["name"].lower() == name.lower()]
        host = [p for p in params if p.get("owner") == "host" and p["name"].lower() == name.lower()]
        return (own or host)[0]

    # ---- commands ------------------------------------------------------------
    def exec_tokens(self, tokens):
        i = 0
        while i < len(tokens):
            cmd = tokens[i]
            i += 1
            if cmd == "set-tempo":
                self.tempo = float(tokens[i]); i += 1
            elif cmd == "add-track":
                i += 1
                name = tokens[i]; i += 1
                self.tracks.append({"id": self.nid(), "name": name, "volume": 1.0})
            elif cmd == "delete-track":
                tid = int(tokens[i]); i += 1
                self.tracks = [t for t in self.tracks if t["id"] != tid]
                self.devices = [d for d in self.devices if d["trackId"] != tid]
                self.clips = [c for c in self.clips if c["trackId"] != tid]
            elif cmd == "add-internal-instrument":
                self.add_device(int(tokens[i]), tokens[i + 1], "instrument"); i += 2
            elif cmd == "add-fx":
                self.add_device(int(tokens[i]), tokens[i + 1], "effect"); i += 2
            elif cmd == "add-midi-clip":
                self.clips.append({"id": self.nid(), "trackId": int(tokens[i]), "startBeat": float(tokens[i + 1]),
                                   "lengthBeats": float(tokens[i + 2]), "notes": []}); i += 3
            elif cmd == "delete-clip":
                cid = int(tokens[i]); i += 1
                self.clips = [c for c in self.clips if c["id"] != cid]
            elif cmd == "add-midi-note":
                c = self.clip(int(tokens[i]))
                c["notes"].append({"startBeat": float(tokens[i + 1]), "note": int(tokens[i + 2]),
                                   "lengthBeats": float(tokens[i + 3]), "velocity": int(tokens[i + 4])})
                i += 5
            elif cmd == "set-track-volume":
                next(t for t in self.tracks if t["id"] == int(tokens[i]))["volume"] = float(tokens[i + 1]); i += 2
            elif cmd == "set-device-parameter":
                dev = next(d for d in self.devices if d["id"] == int(tokens[i]))
                self.find_param(dev["params"], tokens[i + 1])["value"] = float(tokens[i + 2]); i += 3
            else:
                raise ValueError(f"Unknown command: {cmd}")

    def handle(self, req):
        op = req.get("op")
        if op == "ping":
            self.tally(op)
            return {"ok": True}
        if op == "describe" and PROTOCOL >= 2:
            self.tally(op)
            return {"ok": True, "protocol": 2, "magdaVersion": "emulator",
                    "features": {"execSaveFlag": True, "bulkNotes": True, "paramLabels": True,
                                 "paramInfo": True, "pluginPersistence": True, "extParamIndex": True}}
        if op == "exec":
            tokens = req.get("commands", [])
            self.tally(op, notes=tokens.count("add-midi-note"), tokens=len(tokens))
            self.exec_tokens(tokens)
            if req.get("save", True) or PROTOCOL < 2:
                self.save()
            return {"ok": True}
        if op == "notes" and PROTOCOL >= 2:
            notes = req["notes"]
            self.tally(op, notes=len(notes))
            c = self.clip(int(req["clipId"]))
            new = [{"startBeat": n["start"], "note": n["pitch"], "lengthBeats": n["length"],
                    "velocity": n["velocity"]} for n in notes]
            removed = len(c["notes"]) if req.get("mode") == "replace" else 0
            c["notes"] = new if req.get("mode") == "replace" else c["notes"] + new
            if req.get("save", True):
                self.save()
            return {"ok": True, "added": len(new), "removed": removed, "total": len(c["notes"])}
        if op == "dump":
            self.tally(op)
            return self.dump()
        if op == "save":
            self.tally(op)
            self.save()
            return {"ok": True}
        if op == "render":
            self.tally(op)
            if os.environ.get("EMU_RENDER_AUDIO"):
                render_audio(self, req)
                return {"ok": True}
            Path(req["wav"]).write_bytes(b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00"
                                         b"D\xac\x00\x00\x10\xb1\x02\x00\x04\x00\x10\x00data\x00\x00\x00\x00")
            return {"ok": True}
        if op == "plugins":
            self.tally(op)
            return {"ok": True, "plugins": PLUGINS}
        if op == "add_plugin":
            self.tally(op)
            desc = next(p for p in PLUGINS if req["plugin"] in (p["id"], p["name"]))
            kind = "instrument" if req.get("asInstrument") else "effect"
            if PROTOCOL >= 2:
                dev = self.add_device(int(req["trackId"]), desc["name"], kind, desc["format"])
                return {"ok": True, "device": dev["id"], "persistent": True}
            ext_id = 1000 + len(self.ext)
            self.ext[ext_id] = self.make_params(desc["name"])
            return {"ok": True, "device": ext_id}
        if op == "remove_device":
            self.tally(op)
            did = int(req["deviceId"])
            if self.ext.pop(did, None) is None:
                self.devices = [d for d in self.devices if d["id"] != did]
            return {"ok": True}
        if op == "ext_params":
            self.tally(op)
            params = self.ext[int(req["deviceId"])]
            return {"ok": True, "parameters": [{"name": p["name"], "value": p["value"]} for p in params]}
        if op == "set_ext_param":
            self.tally(op)
            params = self.ext[int(req["deviceId"])]
            if PROTOCOL >= 2 and "index" in req:
                target = params[int(req["index"])]
            else:   # legacy: first parameter carrying the name
                target = next(p for p in params if p["name"] == req["name"])
            target["value"] = float(req["value"])
            return {"ok": True}
        if op == "param_info" and PROTOCOL >= 2:
            self.tally(op)
            dev = next(d for d in self.devices if d["id"] == int(req["deviceId"]))
            p = self.find_param(dev["params"], req["param"])
            labels = p.get("choices") or p.get("hidden_labels") or []
            info = {"ok": True, "name": p["name"], "text": display_text(p), "default": p["default"]}
            if labels:
                info["choices"] = labels
                info["valueTable"] = labels
                if p.get("hidden_labels"):
                    info["choiceValues"] = [surge_position(i, len(labels)) for i in range(len(labels))]
            else:
                info["valueTable"] = [f"{p['min'] + (p['max'] - p['min']) * i / 4:.2f}" for i in range(5)]
            return info
        self.tally("unknown")
        return {"ok": False, "error": f"Unknown op: {op}"}


def main(argv):
    if len(argv) >= 2 and argv[0] == "init":
        path = Path(argv[1])
        path.parent.mkdir(parents=True, exist_ok=True)
        Project(path).save()
        print(f"Saved {path}")
        return 0
    if len(argv) >= 4 and argv[0] == "serve" and argv[2] == "--sock":
        project = Project(Path(argv[1]))
        sock_path = argv[3]
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        print("engine chatter before the banner", flush=True)
        print(f"SERVING {sock_path}", flush=True)
        conn, _ = server.accept()
        buf = b""
        with conn:
            while True:
                chunk = conn.recv(1 << 20)
                if not chunk:
                    return 0
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    req = json.loads(line)
                    if req.get("op") == "shutdown":
                        conn.sendall(b'{"ok":true}\n')
                        return 0
                    try:
                        reply = project.handle(req)
                    except Exception as e:
                        reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    conn.sendall((json.dumps(reply) + "\n").encode())
    print("usage: emulator init <file> | serve <file> --sock <path>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
