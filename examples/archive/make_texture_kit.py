"""Generate dusty-vinyl texture samples + custom SFZ for sfizz.
Crackle = Poisson impulse pops w/ exponential micro-decay + gentle LP.
Hiss = loop-ready filtered noise bed. 16-bit/44.1k mono."""
import contextlib
import pathlib
import wave

import numpy as np

KIT = pathlib.Path.home() / "Samples" / "AVL" / "DUSTY_TEXTURE"
KIT.mkdir(parents=True, exist_ok=True)
SR = 44100
rng = np.random.default_rng(20260825)


def write_wav(name, x, peak=0.5):
    m = np.abs(x).max()
    if m > 0:
        x = x * (peak / m)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with contextlib.closing(wave.open(str(KIT / name), "wb")) as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"  {name:16s} {len(x)/SR:.2f}s peak {peak}")


def lp1(x, cutoff):
    """One-pole lowpass."""
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


# ---- crackle: 6 variations, sparse pops on near-silence ----
for k in range(1, 7):
    dur = 0.55
    n = int(dur * SR)
    x = np.zeros(n)
    # Poisson tick density varies per variant (dusty vs clean-ish)
    lam = rng.uniform(14, 30) * dur
    ticks = rng.poisson(lam)
    for _ in range(ticks):
        pos = rng.integers(0, n - 400)
        amp = rng.uniform(0.08, 1.0) * rng.choice([-1.0, 1.0])
        decay_len = int(rng.uniform(60, 500))          # 1.4–11 ms
        env = np.exp(-np.linspace(0, 6, decay_len))
        x[pos:pos + decay_len] += amp * env
    # occasional double-tick (stylus bounce)
    if rng.random() < 0.7:
        pos = rng.integers(0, n - 800)
        amp = rng.uniform(0.3, 0.9)
        d = 220
        x[pos:pos + d] += amp * np.exp(-np.linspace(0, 6, d))
        x[pos + 90:pos + 90 + d] += 0.6 * amp * np.exp(-np.linspace(0, 6, d))
    x = lp1(x, 6500)                                    # soften extremes
    x *= 0.5
    write_wav(f"crackle_{k}.wav", x, peak=0.42)

# ---- hiss: FULL-FORM one-shot (no looping anywhere = no loop bugs) ----
dur = 110.0                       # covers the whole 106.7s form
n = int(dur * SR)
print("synthesizing", dur, "s hiss ...")
hiss = rng.standard_normal(n).astype(np.float32)
# block-wise lowpass (one-pole, vectorized via lfilter-style recurrence)
a = np.exp(-2 * np.pi * 3800 / SR)
b0 = 1 - a
acc = 0.0
out = np.empty_like(hiss)
for i in range(len(hiss)):
    acc = b0 * hiss[i] + a * acc
    out[i] = acc
hiss = lp1(out, 3800)             # second pole for the shelf
hiss -= hiss.mean()               # DC block: no drone
xf = int(0.5 * SR)                # long fades at both ends
fade = np.linspace(0, 1, xf)
hiss[:xf] *= fade
hiss[-xf:] *= fade[::-1]
write_wav("dusty_hiss_full.wav", hiss, peak=0.30)

# ---- SFZ ----
sfz = ["<group> amp_veltrack=0 ampeg_release=0.1"]
for k in range(1, 7):
    sfz.append(
        f"<region> sample=crackle_{k}.wav pitch_center={56 + k} "
        f"lokey={56 + k} hikey={56 + k} amp_random=12")
sfz.append(
    "<region> sample=dusty_hiss_full.wav pitch_center=72 lokey=72 "
    "hikey=72")
(KIT / "dusty_texture.sfz").write_text("\n".join(sfz) + "\n")
print("wrote", KIT / "dusty_texture.sfz")
print("keys: crackle 57-62 (A3-D4), hiss 72 (C5)")
