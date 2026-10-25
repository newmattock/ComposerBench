"""DUSTY CASSETTE v4 — hand-composed lo-fi hip hop. The 'me, manually' pass.

Design decisions (deliberate, not evolved):
- 72 BPM (down from 82 — lo-fi lives slower), 128-beat form kept for tooling.
- Progression: F6/9 | Dm7 | Bbmaj7 | Csus-add9 — ii-V warmth in F.
- Drums: boom-bap cells with ghost snare 16ths, swung off-8th hats,
  snare-roll into a 2-bar B-drop, one fill into the outro.
- Bass: magda_fm, root-anticipation + 5th/3rd walks, not four-on-floor.
- Chords: polysynth @ ~900 Hz cutoff (probe-verified knob), 4-note voicings,
  entering at bar 1 (quiet pickup) so the intro has real music immediately.
- Melody: F-major-pentatonic phrases with RESTS, placed sparsely.
- Texture: synthesized vinyl crackle + tape hiss, overlaid post-render.

v4.1 — SFIZZ IS BROKEN ON THIS MACHINE (2026-08-25): user reported a
key-tracked beeping under the whole track. Forensics: ANY sfz — custom kit,
Black Pearl, minimal single-region, relative OR absolute paths — renders a
pure sine tuned to the pressed key (C4@60 -> 263.8 Hz, C5@72 -> 522.2 Hz,
C6@84 -> 1044.4 Hz, identical -23 dB). sfizz never loads samples here;
that sine is its fallback voice. (Retrospective tell: the "drums-only"
probe centroid of 122 Hz was every drum key's low fallback tone.) Fix:
- drums -> internal magda_kick/snare/hat on separate tracks (doctor-
  manifest verified audible)
- vinyl texture -> numpy post-overlay of synthesized crackle/hiss directly
  onto the rendered master (no sampler involved, nothing left to fail)
"""
from pathlib import Path
import asyncio
import base64
import json
import pathlib
import random
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.model import Note

CLI = pathlib.Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = pathlib.Path("/tmp/mb_v4")
SFZ_DRUMS = pathlib.Path("~/Samples/AVL/BLACK_PEARL_2023_repack/Black_Pearl_2023_repack.sfz").expanduser()
SFZ_TEXTURE = pathlib.Path("~/Samples/AVL/DUSTY_TEXTURE/dusty_texture.sfz").expanduser()
OUT = WORK / "v4.wav"
MASTER = pathlib.Path.home() / "Music" / "ComposerBench" / "dusty_cassette_v4.wav"

BPM = 72.0

rng = random.Random(4)


def N(start, dur, pitch, vel):
    return Note(start=round(start, 3), duration=dur, pitch=pitch,
                velocity=max(1, min(127, int(vel))))


# ------------------------------------------------------------------ drums ----
SW = 0.08  # swing: push off-8ths late


def drum_part():
    """All drum voices in ONE note list -> ONE clip on the drums track
    (same-track clips arrange sequentially — never stack them)."""
    kick, snare, hats = [], [], []
    CELLS = [
        {"k": [0.0, 2.5],       "s": [1.0, 3.0], "g": [(1.75, 24), (3.75, 30)]},
        {"k": [0.0, 1.75, 2.5], "s": [1.0, 3.0], "g": [(0.75, 22), (3.5, 28)]},
        {"k": [0.0, 2.5, 3.5],  "s": [1.0, 3.0], "g": [(1.75, 26)]},
        {"k": [0.0, 2.5],       "s": [1.0, 3.0],
         "g": [(0.75, 22), (2.75, 24), (3.75, 34)]},
    ]
    for bar in range(4, 28):                 # drums enter at A, leave at outro
        b = bar * 4.0
        cell = CELLS[bar % 4]
        in_b = 12 <= bar < 20                # B section: slightly busier
        # B-entrance DROP: two bars without kick/snare (hats+texture only),
        # then the kit slams back at bar 14 -> clear structural marker
        dropped = bar in (12, 13)
        kicks = [] if dropped else list(cell["k"])
        if in_b and not dropped and bar % 2 == 1:
            kicks.append(3.75)
        for off in kicks:
            kick.append(N(b + off, 0.2, 36, rng.uniform(92, 100)))
        if not dropped:
            for off in cell["s"]:
                snare.append(N(b + off, 0.2, 38, rng.uniform(80, 88)))
            for off, v in cell["g"]:
                if in_b or off >= 2.0:
                    snare.append(N(b + off, 0.18, 38, v))
        else:
            snare.append(N(b + 3.0, 0.18, 38, 30))   # lone ghost hint
        # hats: swung 8ths, velocity breathes across the bar
        hv = 0.65 if dropped else 1.0
        for q in range(4):
            v1 = 68 if q == 0 else 62
            hats.append(N(b + q, 0.08, 42,
                          rng.uniform(v1 - 4, v1 + 4) * hv))
            hats.append(N(b + q + 0.5 + SW, 0.08, 42,
                          rng.uniform(42, 50) * hv))
        # open-ish hat at the top of every 4th bar
        if bar % 4 == 3 and bar < 28:
            hats.append(N(b + 3.5 + SW, 0.22, 46, 58))
    # roll INTO the B-drop (beats 46.5-47.75) so the dropout lands musically
    for i, off in enumerate((46.5, 46.75, 47.0, 47.25, 47.5, 47.75)):
        snare.append(N(off, 0.12, 38, 26 + i * 7))
    # fill into the outro (beats 78.5-80)
    for i, off in enumerate((78.5, 78.75, 79.0, 79.25, 79.5)):
        snare.append(N(off, 0.15, 38, 34 + i * 9))
    # outro: drums out but hats keep breathing to the final hit
    for bar in range(28, 31):
        b = bar * 4.0
        for q in range(4):
            hats.append(N(b + q, 0.08, 42, 54))
            hats.append(N(b + q + 0.5 + SW, 0.08, 42, 40))
    # final hit pair
    kick.append(N(112.0, 0.25, 36, 96))
    snare.append(N(113.0, 0.25, 38, 70))
    # intro pickup
    kick.append(N(12.0, 0.2, 36, 88))
    for i, off in enumerate((14.0, 14.5, 15.0)):
        hats.append(N(off, 0.08, 42, 40 + i * 10))
    return sorted(kick + snare + hats, key=lambda n: n.start)


def crackle_part():
    """Crackle events (rendered as overlay impulses post-render)."""
    out = []
    for b in range(0, 128, 2):
        if b <= 15 or b >= 112:
            out.append((float(b), 57 + (b // 2) % 6, 96))
        elif b % 4 == 2:
            out.append((float(b), 57 + (b % 24) // 4, 84))
    return out


# ------------------------------------------------------------- chords/bass ----
VOICINGS = {
    "F":  [53, 60, 64, 69],   # F3 C4 E4 A4  -> F6/9 color
    "Dm": [50, 57, 60, 65],   # D3 A3 C4 F4  -> Dm7
    "Bb": [50, 53, 58, 65],   # D3 F3 Bb3 F4 -> Bbmaj7
    "C":  [55, 60, 62, 67],   # G3 C4 D4 G4  -> Csus add9
}
PROG = ["F", "Dm", "Bb", "C"]
ROOTS = {"F": 29, "Dm": 26, "Bb": 34 - 12, "C": 24}      # F1 D1 Bb0 C0
FIFTS = {"F": 36, "Dm": 33, "Bb": 29, "C": 31}           # C2 A1 F1 G1
THIRDS = {"F": 33, "Dm": 29, "Bb": 34, "C": 28}


def chords_part():
    out = []
    # music starts at bar 1 — no texture-only void; pad swells in
    for bar in range(1, 28):
        ch = PROG[bar % 4]
        b = bar * 4.0
        # gentle anticipation: chord lands a hair early on odd bars
        start = b - (0.06 if bar % 2 else 0.0)
        vel = rng.uniform(52, 58) if bar >= 4 else 40   # quiet 3-bar pickup
        for p in VOICINGS[ch]:
            out.append(N(start, 3.8, p, vel))
    # final whole-form F6/9 with low root
    for p in [41, 53, 60, 64, 69]:
        out.append(N(124.0, 4.0, p, 54))
    return sorted(out, key=lambda n: n.start)


def bass_part():
    out = []
    for bar in range(4, 28):
        ch = PROG[bar % 4]
        b = bar * 4.0
        r = ROOTS[ch]
        out.append(N(b + 0.02, 1.4, r, 92))                  # downbeat root
        if bar % 2 == 0:
            out.append(N(b + 2.5, 0.7, FIFTS[ch], 74))       # lifted 5th
        else:
            out.append(N(b + 2.5, 0.7, r + 12, 76))          # octave push
        if bar % 4 == 3:
            out.append(N(b + 3.5, 0.45, THIRDS[ch], 70))     # walk-in 3rd
    out.append(N(112.0, 3.5, ROOTS["F"], 88))
    out.append(N(124.0, 3.8, ROOTS["F"] - 0 + 12, 70))       # final F2
    return sorted(out, key=lambda n: n.start)


# ------------------------------------------------------------------ melody ----
P1 = [(33.0, 81, 1.2, 66), (34.5, 79, 0.6, 54), (35.0, 77, 1.8, 60),
      (39.0, 72, 1.4, 58)]
P2 = [(41.0, 74, 0.8, 58), (41.5, 72, 0.6, 50), (42.0, 74, 1.2, 56),
      (43.5, 69, 2.0, 62)]
P3 = [(49.0, 81, 0.9, 60), (50.0, 84, 1.4, 66), (51.5, 81, 0.6, 52),
      (52.0, 79, 1.6, 60), (55.0, 77, 1.2, 56)]
P4 = [(65.0, 77, 1.2, 58), (66.5, 81, 0.8, 62), (67.0, 79, 1.8, 64),
      (71.0, 74, 1.0, 54)]
P5 = [(97.0, 72, 1.5, 50), (101.0, 77, 2.0, 48)]


def melody_part():
    out = []
    for beat, pitch, dur, vel in (P1 + P2 + P3 + P4 + P5):
        out.append(Note(start=round(beat, 3), duration=dur, pitch=pitch,
                        velocity=min(127, vel + 10)))
        # B-section shimmer: octave-up echoes of phrases 3/4
        if beat in (49.0, 50.0, 51.5, 52.0, 55.0, 65.0, 66.5, 67.0, 71.0):
            out.append(Note(start=round(beat, 3), duration=dur,
                            pitch=pitch + 12,
                            velocity=min(127, vel - 14)))
    return sorted(out, key=lambda n: n.start)


PARTS = {"drums": drum_part(),
         "chords": chords_part(), "bass": bass_part(),
         "melody": melody_part()}
CRACKLES = crackle_part()


async def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    daw = MagdaServeAdapter(CLI, WORK / "v4.mgd")
    await daw.initialize()
    try:
        await daw._exec("set-tempo", str(BPM))

        tracks = {}
        # ---- drums: sfizz + Black Pearl (setExternalPluginState FIXED
        # 2026-08-25: form-dialect -> native chunk translation in
        # TracktionEngineWrapper::setExternalPluginState). Async-init aware:
        # wait AFTER add_plugin BEFORE setting state, then wait again.
        t = await daw.create_track("drums")
        tracks["drums"] = t.id
        plugs = {p["name"]: p for p in await daw.list_plugins()}
        desc = next(plugs[n] for n in plugs if n.lower() == "sfizz")
        ddev = await daw.add_plugin(t.id, desc["id"], as_instrument=True)
        await asyncio.sleep(5.0)          # async VST3 init
        state_xml = ('<form><param name="file" value="%s"/>'
                     '<param name="volume" value="0"/></form>' % SFZ_DRUMS)
        await daw._request({"op": "set_ext_state", "deviceId": ddev,
                            "state_b64": base64.b64encode(
                                state_xml.encode()).decode()})
        await asyncio.sleep(8.0)          # sample load + second apply
        await daw._request({"op": "set_ext_state", "deviceId": ddev,
                            "state_b64": base64.b64encode(
                                state_xml.encode()).decode()})
        await asyncio.sleep(8.0)
        print("[build] drums: sfizz/Black Pearl loaded", flush=True)

        # chords: polysynth, warm
        t = await daw.create_track("chords")
        tracks["chords"] = t.id
        await daw.load_device(t.id, "magda_polysynth")
        rev = await daw.add_fx(t.id, "magda_reverb")
        # melody: polysynth, brighter but lowpassed
        t = await daw.create_track("melody")
        tracks["melody"] = t.id
        await daw.load_device(t.id, "magda_polysynth")
        mrev = await daw.add_fx(t.id, "magda_reverb")
        # bass: FM
        t = await daw.create_track("bass")
        tracks["bass"] = t.id
        await daw.load_device(t.id, "magda_fm")

        # clips (ONE per track — sequential-arrangement lesson respected)
        clips = {}
        for part, tid in tracks.items():
            c = await daw.create_clip(tid, part, 0.0, 128.0)
            clips[part] = c.id

        # notes
        for part, notes in PARTS.items():
            cid = int(str(clips[part]).removeprefix("clip:"))
            batch = []
            for n in notes:
                assert -1e-6 <= n.start < 128.0, (part, n.start)
                batch += ["add-midi-note", str(cid), f"{n.start:.6f}",
                          str(n.pitch), str(n.duration), str(n.velocity)]
            for i in range(0, len(batch), 600):
                await daw._exec(*batch[i:i + 600])
            live = await daw.get_notes(tracks[part])
            print(f"[notes] {part:8s} wrote {len(notes)}, live {len(live)}",
                  flush=True)
            assert len(live) == len(notes), part

        # ---- sound design (named params, verified maps) ----
        async def design(track, device, params):
            for pname, val in params:
                try:
                    await daw.set_parameter(track, device, pname, val)
                except Exception as e:
                    print(f"  ! {track}/{pname}: {str(e)[:70]}", flush=True)

        PAD = [("Osc 1 Level", -6.0), ("Osc 2 Level", -60.0),
               ("Osc 3 Level", -60.0), ("Osc 4 Level", -60.0),
               ("Cutoff", 900.0), ("Resonance", 0.22),
               ("Amp Attack", 30.0), ("Amp Release", 900.0),
               ("Vel Amp", 0.3)]
        LEAD = [("Osc 1 Level", -4.0), ("Osc 2 Level", -60.0),
                ("Osc 3 Level", -60.0), ("Osc 4 Level", -60.0),
                ("Cutoff", 2600.0), ("Resonance", 0.18),
                ("Amp Attack", 8.0), ("Amp Release", 450.0)]
        SUB = [("Amp Attack", 2.0), ("Amp Decay", 700.0),
               ("Amp Sustain", 0.35), ("Amp Release", 160.0)]
        R_CH = [("Mix", 0.26), ("Predelay", 20.0), ("Decay", 45.0),
                ("Damping", 55.0), ("High Cut", 5200.0), ("Low Cut", 220.0)]
        R_ME = [("Mix", 0.32), ("Predelay", 15.0), ("Decay", 60.0),
                ("Damping", 65.0), ("High Cut", 6500.0), ("Low Cut", 250.0)]
        await design("chords", "magda_polysynth", PAD)
        await design("chords", str(rev), R_CH)
        await design("melody", "magda_polysynth", LEAD)
        await design("melody", str(mrev), R_ME)
        await design("bass", "magda_fm", SUB)

        for name, vol in (("drums", 1.0),
                          ("chords", 0.78), ("melody", 0.85),
                          ("bass", 0.74)):
            await daw.set_track_volume(tracks[name], vol)

        out = OUT
        await asyncio.wait_for(
            daw.render(RenderRequest(output_path=out)), 300)
        await daw.save_project()
        await daw.shutdown()

        # ---- POST-OVERLAY: vinyl crackle + tape hiss, no sampler involved.
        import contextlib
        import wave
        import numpy as np

        def load_wav(p):
            with contextlib.closing(wave.open(str(p))) as w:
                sr = w.getframerate()
                ch = w.getnchannels()
                raw = w.readframes(w.getnframes())
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
            if ch == 2:
                x = x.reshape(-1, 2).mean(axis=1)
            return x, sr

        mix, sr = load_wav(out)
        spb = len(mix) / 128.0                      # samples per beat
        KIT = pathlib.Path("~/Samples/AVL/DUSTY_TEXTURE").expanduser()

        def load_stem(name):
            with contextlib.closing(wave.open(str(KIT / name))) as w:
                raw = w.readframes(w.getnframes())
            return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768

        # hiss: full-form bed at low level
        hiss = load_stem("dusty_hiss_full.wav")
        n = min(len(mix), len(hiss))
        mix[:n] += hiss[:n] * 0.055                 # ~ -25 dB under the mix
        # crackle: each event = one kit sample placed at its beat
        for beat, key, vel in CRACKLES:
            k = int(key) - 57                       # 0..5
            pop = load_stem(f"crackle_{k + 1}.wav")
            pos = int(beat * spb)
            end = min(len(mix), pos + len(pop))
            if end > pos:
                mix[pos:end] += pop[:end - pos] * (vel / 127.0) * 0.30
        peak = float(np.abs(mix).max())
        if peak > 0.89:
            mix *= 0.89 / peak
        pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
        with contextlib.closing(wave.open(str(MASTER), "wb")) as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        print(f"[overlay] texture applied -> {MASTER.name}", flush=True)

        stats = analyze_wav(out)
        expect = 128.0 / BPM * 60.0
        print(f"[render] peak {stats.peak_dbfs:.1f} dBFS  "
              f"dur {stats.duration_seconds:.1f}s (expect {expect:.1f})",
              flush=True)
        if abs(stats.duration_seconds - expect) > 5:
            raise SystemExit("duration wrong — arrangement leak?")
        mstats = analyze_wav(MASTER)
        print(f"[master] peak {mstats.peak_dbfs:.1f} dBFS  "
              f"rms {mstats.rms_dbfs:.1f} dBFS", flush=True)
        print("WROTE:", MASTER, flush=True)
    except Exception:
        try:
            await daw.shutdown()
        except Exception:
            pass
        raise


asyncio.run(main())
