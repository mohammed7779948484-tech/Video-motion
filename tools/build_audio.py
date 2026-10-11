#!/usr/bin/env python3
"""SYRAZEN "Open All Night" - original procedural score and sound design (15 s, 9:16 ad).

Regenerate (deterministic: same numpy/ffmpeg -> byte-identical WAV on every run):

    python3 tools/build_audio.py              # build assets/audio/syrazen-score.wav, then verify
    python3 tools/build_audio.py --no-verify  # build only

Every event time is read from tools/cues.json (key "cues", absolute seconds), so retiming the
picture there and re-running this script keeps sound and image in sync. Nothing is sampled,
downloaded or licensed: every sound is synthesised here from sines and seeded noise.

Pipeline
  1. instruments / SFX ........ one function each (pure numpy, seeded RNG per event)
  2. sequencer ................ places events from the cues on buses (pre-impact / post-impact worlds)
  3. mix bus .................. sidechain pump, convolution reverbs (synthetic IRs), ping-pong echo,
                                a hard ~60 ms gap gate before the impact
  4. master ................... DC blocker, fades, soft clip, true-peak-aware look-ahead limiter,
                                gain staging to -14 LUFS
  5. ffmpeg loudnorm .......... two passes (measure, then linear apply): -14 LUFS integrated,
                                <= -1 dBTP, forced 48 kHz stereo
  6. export ................... TPDF-dithered 16-bit PCM WAV, exactly film.duration (15.000 s)
  7. verify ................... ebur128, ffprobe, onset/gap/click/DC/clip/mono checks, PNGs in
                                review/audio/ (spectrogram, waveform, levels-with-cues)

Requirements: python3, numpy, ffmpeg/ffprobe (6.x). No scipy/matplotlib needed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import wave
import zlib

import numpy as np

# ------------------------------------------------------------------------------------------------
# 0. Paths, constants, timing
# ------------------------------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parent.parent
CUES_FILE = ROOT / "tools" / "cues.json"
OUT_WAV = ROOT / "assets" / "audio" / "syrazen-score.wav"
REVIEW_DIR = ROOT / "review" / "audio"


def _tool(name: str) -> str:
    fixed = pathlib.Path("/usr/bin") / name
    return str(fixed) if fixed.exists() else (shutil.which(name) or name)


FFMPEG, FFPROBE = _tool("ffmpeg"), _tool("ffprobe")

SR = 48_000
SEED = 0x5E_A2E7  # fixed: every random stream below is derived from it
TARGET_LUFS = -14.0
TARGET_TP = -1.0
PRE_TP_CEIL = -1.6  # numpy limiter ceiling (dBTP) so loudnorm can stay in linear mode

DATA = json.loads(CUES_FILE.read_text(encoding="utf-8"))
CUE = DATA["cues"]
FILM = DATA["film"]
GEO = DATA["geometry"]
DUR = float(FILM["duration"])
BEAT = 60.0 / float(FILM["bpm"])
N = int(round(DUR * SR))
if abs(float(CUE.get("end", DUR)) - DUR) > 1e-9:
    raise SystemExit("cues.end and film.duration disagree")


def pan_x(x_px: float, scale: float = 0.8, limit: float = 0.7) -> float:
    """Screen x (px on the 1080 canvas) -> stereo pan (-1..1), kept moderate for mono safety."""
    half = float(FILM["width"]) / 2
    return float(np.clip((x_px - half) / half * scale, -limit, limit))


# ------------------------------------------------------------------------------------------------
# 1. DSP helpers
# ------------------------------------------------------------------------------------------------
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def hz(note: str) -> float:
    m = re.fullmatch(r"([A-G])([b#]?)(-?\d)", note)
    if not m:
        raise ValueError(note)
    midi = _PC[m[1]] + {"": 0, "b": -1, "#": 1}[m[2]] + 12 * (int(m[3]) + 1)
    return 440.0 * 2 ** ((midi - 69) / 12)


def rng(*tags) -> np.random.Generator:
    """Independent, reproducible stream per event (stable across Python runs, unlike hash())."""
    return np.random.default_rng([SEED] + [zlib.crc32(str(t).encode()) for t in tags])


def db(x):
    return 10.0 ** (np.asarray(x, float) / 20.0)


def ns(sec: float) -> int:
    return max(1, int(round(sec * SR)))


def tl(sec: float) -> np.ndarray:
    return np.arange(ns(sec)) / SR


def ramp(n: int) -> np.ndarray:
    """Raised-cosine 0 -> 1 over n samples."""
    return 0.5 - 0.5 * np.cos(np.pi * (np.arange(n) + 0.5) / max(n, 1))


def seal(x: np.ndarray, fin: float = 0.0005, fout: float = 0.005) -> np.ndarray:
    """Short raised-cosine fades on both ends of an event buffer: no clicks at segment edges."""
    x = np.array(x, float, copy=True)
    n = x.shape[-1]
    a, b = min(ns(fin), n // 2), min(ns(fout), n // 2)
    x[..., :a] *= ramp(a)
    x[..., n - b:] *= ramp(b)[::-1]
    return x


def curve(points, n: int, t0: float = 0.0) -> np.ndarray:
    """Breakpoint automation [(time, value), ...] with cosine interpolation, sampled from t0."""
    t = t0 + np.arange(n) / SR
    ts = np.array([p[0] for p in points], float)
    vs = np.array([p[1] for p in points], float)
    i = np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 2)
    u = np.clip((t - ts[i]) / np.maximum(ts[i + 1] - ts[i], 1e-9), 0.0, 1.0)
    out = vs[i] + (vs[i + 1] - vs[i]) * (0.5 - 0.5 * np.cos(np.pi * u))
    out[t < ts[0]] = vs[0]
    out[t >= ts[-1]] = vs[-1]
    return out


def smooth(x: np.ndarray, sec: float) -> np.ndarray:
    k = ns(sec)
    if k < 3:
        return x
    w = np.hanning(k)
    w /= w.sum()
    xp = np.pad(x, (k // 2, k - 1 - k // 2), mode="edge")
    return np.convolve(xp, w, mode="valid")


def lp_gain(f, fc, order=2):
    return 1.0 / np.sqrt(1.0 + (np.asarray(f) / fc) ** (2 * order))


def hp_gain(f, fc, order=2):
    return 1.0 / np.sqrt(1.0 + (fc / np.maximum(np.asarray(f), 1e-3)) ** (2 * order))


def shape(x: np.ndarray, gain_fn) -> np.ndarray:
    """Zero-phase spectral shaping (used for noise beds and IRs, never across the impact gap)."""
    n = x.shape[-1]
    f = np.fft.rfftfreq(n, 1 / SR)
    return np.fft.irfft(np.fft.rfft(x, axis=-1) * gain_fn(f), n=n, axis=-1)


def svf(x: np.ndarray, fc, q: float = 0.707, mode: str = "lp") -> np.ndarray:
    """Causal TPT state-variable filter (Zavalishin/Cytomic); fc may be a per-sample array."""
    x = np.asarray(x, float)
    n = len(x)
    fcv = np.broadcast_to(np.clip(np.asarray(fc, float), 5.0, 0.45 * SR), (n,))
    g = np.tan(np.pi * fcv / SR)
    k = 1.0 / q
    a1 = 1.0 / (1.0 + g * (g + k))
    a2 = g * a1
    a3 = g * a2
    xs, A1, A2, A3 = x.tolist(), a1.tolist(), a2.tolist(), a3.tolist()
    lp, bp = [0.0] * n, [0.0] * n
    ic1 = ic2 = 0.0
    for i in range(n):
        v3 = xs[i] - ic2
        v1 = A1[i] * ic1 + A2[i] * v3
        v2 = ic2 + A2[i] * ic1 + A3[i] * v3
        ic1 = 2.0 * v1 - ic1
        ic2 = 2.0 * v2 - ic2
        lp[i] = v2
        bp[i] = v1
    lpa, bpa = np.array(lp), np.array(bp)
    if mode == "lp":
        return lpa
    if mode == "bp":
        return k * bpa  # unity gain at fc
    if mode == "hp":
        return x - k * bpa - lpa
    raise ValueError(mode)


def pan_gains(p):
    a = (np.clip(p, -1.0, 1.0) + 1.0) * np.pi / 4.0
    return np.cos(a), np.sin(a)


def stereo(x: np.ndarray, pan=0.0) -> np.ndarray:
    """Mono -> constant-power panned stereo; stereo input gets a simple balance. No phase tricks."""
    x = np.asarray(x, float)
    if x.ndim == 2:
        p = np.asarray(pan, float)
        return np.vstack([x[0] * np.minimum(1.0, 1.0 - p), x[1] * np.minimum(1.0, 1.0 + p)])
    gl, gr = pan_gains(pan)
    return np.vstack([x * gl, x * gr])


def phase(f, n: int | None = None) -> np.ndarray:
    if np.isscalar(f):
        return 2 * np.pi * f * np.arange(n) / SR
    return 2 * np.pi * np.cumsum(f) / SR


def fft_conv(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    m = len(x) + len(h) - 1
    nfft = 1 << (m - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(h, nfft), nfft)[: len(x)]


def burst(r, dur, tau, hp=None, lp=None, bp=None, q=0.8) -> np.ndarray:
    """Short filtered noise burst with exponential decay (excitations, clicks, air)."""
    t = tl(dur)
    x = r.standard_normal(len(t))
    if hp:
        x = svf(x, hp, q, "hp")
    if lp:
        x = svf(x, lp, q, "lp")
    if bp:
        x = svf(x, bp, q, "bp")
    return x * np.exp(-t / tau)


def modal(r, t, modes) -> np.ndarray:
    """Sum of exponentially decaying sinusoids [(freq, amp, tau), ...] - resonant bodies, bells."""
    out = np.zeros_like(t)
    for f, a, tau in modes:
        if f < 0.45 * SR:
            out += a * np.sin(2 * np.pi * f * t + r.uniform(0, 2 * np.pi)) * np.exp(-t / tau)
    return out


def attack(x: np.ndarray, sec: float) -> np.ndarray:
    a = min(ns(sec), x.shape[-1])
    x[..., :a] *= ramp(a)
    return x


def norm_rms(x: np.ndarray) -> np.ndarray:
    return x / (np.sqrt(np.mean(np.square(x))) + 1e-12)


# ------------------------------------------------------------------------------------------------
# 2. Instruments and SFX (each returns a mono or (2, n) buffer starting at its own t = 0)
# ------------------------------------------------------------------------------------------------
# --- ambience -------------------------------------------------------------------------------------
def amb_street(r, t_end: float) -> dict:
    """Dusk -> night street bed: air (bright/dark versions), wide night air, city hum tuned to F."""
    n = ns(t_end)
    t = np.arange(n) / SR
    w = r.standard_normal((2, n))  # decorrelated L/R noise -> width without phase tricks
    air = shape(w, lambda f: (1 / np.sqrt(np.maximum(f, 30) / 1000)) * hp_gain(f, 160, 1))
    bright = norm_rms(shape(air, lambda f: lp_gain(f, 5200, 2)))
    dark = norm_rms(shape(air, lambda f: lp_gain(f, 850, 2) * hp_gain(f, 110, 1)))
    w2 = r.standard_normal((2, n))
    night = norm_rms(shape(w2, lambda f: lp_gain(f, 520, 2) * hp_gain(f, 70, 2)))
    wind = smooth(r.standard_normal(n), 0.9)
    night *= 1.0 + 0.35 * wind / (np.abs(wind).max() + 1e-9)
    rumble = norm_rms(shape(r.standard_normal(n), lambda f: lp_gain(f, 120, 3) * hp_gain(f, 45, 2)))
    fF = hz("F2")  # distant city hum, sitting on the tonic
    wob = 1 + 0.25 * np.sin(2 * np.pi * 0.31 * t + 1.0)
    hum = wob * (np.sin(2 * np.pi * fF * t) + 0.55 * np.sin(4 * np.pi * fF * t + 0.7)
                 + 0.22 * np.sin(6 * np.pi * fF * t + 2.1) + 0.1 * np.sin(8 * np.pi * fF * t + 0.3))
    # electrical buzz of the shop lights (switched off by lightsOff, one third per click)
    buzz = sum(a * np.sin(2 * np.pi * fF * k * t + k) for k, a in ((2, 1.0), (3, 0.6), (4, 0.45), (6, 0.25)))
    return {"bright": bright, "dark": dark, "night": night, "rumble": rumble, "hum": hum, "buzz": buzz}


def crickets(r, dur: float) -> np.ndarray:
    n = ns(dur)
    t = np.arange(n) / SR
    out = np.zeros((2, n))
    for fc, pan, period, lvl in ((4420.0, -0.6, 0.83, 1.0), (4960.0, 0.55, 0.69, 0.75)):
        sig = np.zeros(n)
        tc = r.uniform(0.05, 0.35)
        L = ns(0.021)
        win = np.hanning(L)
        while tc < dur - 0.25:
            for p in range(int(r.integers(3, 5))):
                i0 = ns(tc + p * 0.034)
                if i0 + L < n:
                    sig[i0:i0 + L] += win * np.sin(2 * np.pi * fc * t[i0:i0 + L]) * (0.65 + 0.35 * r.random())
            tc += period * r.uniform(0.85, 1.18)
        out += stereo(sig * lvl, pan)
    return out


# --- dusk SFX -------------------------------------------------------------------------------------
def clock_tick(r) -> np.ndarray:
    t = tl(1.0)
    tick = modal(r, t, [(3150, 1.0, 0.006), (5230, 0.6, 0.004), (1720, 0.35, 0.01)])
    tick[: ns(0.02)] += 0.5 * burst(r, 0.02, 0.0012, hp=4500)
    chime = modal(r, t, [(hz("C6"), 1.0, 0.42), (hz("C6") * 2.0, 0.28, 0.22),
                         (hz("C6") * 2.76, 0.16, 0.11), (hz("C6") * 5.40, 0.06, 0.05)])
    out = 0.6 * tick
    d = ns(0.05)
    out[d:] += 0.32 * attack(chime, 0.002)[: len(t) - d]
    return attack(out, 0.0004)


def wood_knock(r, vel=1.0) -> np.ndarray:
    t = tl(0.4)
    det = r.uniform(0.97, 1.03)
    x = modal(r, t, [(182 * det, 0.55, 0.06), (431 * det, 1.0, 0.045), (968 * det, 0.5, 0.028),
                     (1830 * det, 0.28, 0.016), (2920 * det, 0.13, 0.009)])
    x[: ns(0.02)] += 0.45 * burst(r, 0.02, 0.0018, lp=3500)
    return attack(x, 0.0005) * vel


def sign_creak(r, dur: float, amp_points) -> np.ndarray:
    """Swinging sign: friction pulse train through small wooden/metal modes, wobbling rate."""
    n = ns(dur)
    t = np.arange(n) / SR
    rate = 150 + 80 * np.sin(2 * np.pi * 2.2 * t + 0.4) + 25 * np.sin(2 * np.pi * 7.1 * t)
    ph = np.cumsum(rate) / SR
    idx = np.nonzero(np.diff(np.floor(ph)) > 0)[0] + 1
    imp = np.zeros(n)
    imp[idx] = 0.5 + 0.5 * r.random(len(idx))
    ir = modal(r, tl(0.03), [(1010, 1.0, 0.005), (2030, 0.5, 0.0035), (2890, 0.3, 0.0025), (660, 0.4, 0.007)])
    return fft_conv(imp, ir) * curve(amp_points, n)


def light_click(r) -> np.ndarray:
    """Crisp wall-switch: toggle click + a smaller secondary click 9 ms later."""
    t = tl(0.09)

    def one(scale):
        c = modal(r, t, [(3100, 1.0, 0.005), (5650, 0.6, 0.003), (1240, 0.5, 0.009)])
        c[: ns(0.012)] += 0.8 * burst(r, 0.012, 0.0007, hp=3000)
        return attack(c, 0.0003) * scale

    x = one(1.0)
    d = ns(0.009)
    x[d:] += one(0.45)[: len(t) - d]
    return x


def power_down(r) -> np.ndarray:
    """Tiny low 'thoomp' as a light dies + its ballast buzz winding down (harmonics for phones)."""
    t = tl(0.4)
    f = 42 + (128 - 42) * np.exp(-t / 0.05)
    body = np.sin(phase(f)) * np.exp(-t / 0.075)
    body = np.tanh(1.8 * body) / np.tanh(1.8)
    fb = hz("F2") * (1 - 0.12 * (1 - np.exp(-t / 0.08)))
    buzz = (np.sin(2 * phase(fb)) + 0.5 * np.sin(3 * phase(fb)) + 0.3 * np.sin(4 * phase(fb))) * np.exp(-t / 0.06)
    return attack(body + 0.18 * buzz, 0.002)


# --- night ----------------------------------------------------------------------------------------
def pad(r, chords, cutoff_points, *, tilt=1.25, voices=3, detune=7.0, width=0.55, drive=0.0) -> tuple:
    """Warm detuned additive-saw pad. chords = [(t0, t1, attack, release, [notes], gain), ...].
    The 'filter' is applied per harmonic (Butterworth-like magnitude vs. a cutoff curve over time),
    so sweeps are smooth and alias-free. Returns (stereo buffer, absolute start time)."""
    start = min(c[0] for c in chords)
    end = max(c[1] + c[3] for c in chords)
    n = ns(end - start)
    t = np.arange(n) / SR
    fc = curve(cutoff_points, n, start)
    out = np.zeros((2, n))
    for ci, (t0, t1, att, rel, notes, gain) in enumerate(chords):
        env = curve([(t0, 0.0), (t0 + att, 1.0), (t1, 1.0), (t1 + rel, 0.0)], n, start) * gain
        live = np.nonzero(env > 1e-5)[0]
        a, b = live[0], live[-1] + 1
        tt = t[a:b]
        fcs = fc[a:b]
        for note in notes:
            f0 = hz(note)
            for v in range(voices):
                cents = (v - (voices - 1) / 2) * detune
                drift = 1 + 0.0011 * np.sin(2 * np.pi * r.uniform(0.09, 0.21) * tt + r.uniform(0, 6.28))
                ph = phase(f0 * 2 ** (cents / 1200) * drift) + r.uniform(0, 2 * np.pi)
                sig = np.zeros(b - a)
                for k in range(1, 48):
                    if k * f0 > 15000:
                        break
                    g = (1.0 / k ** tilt) / np.sqrt(1.0 + (k * f0 / fcs) ** 4)
                    if g.max() < 1.5e-3:
                        break
                    sig += g * np.sin(k * ph + r.uniform(0, 2 * np.pi))
                p = (v - (voices - 1) / 2) / max((voices - 1) / 2, 1) * width + r.uniform(-0.12, 0.12)
                gl, gr = pan_gains(p)
                out[0, a:b] += sig * gl * env[a:b]
                out[1, a:b] += sig * gr * env[a:b]
    out /= np.sqrt(voices * 4.0)
    if drive:
        out = np.tanh(drive * out) / np.tanh(drive)
    return out, start


def lonely_ping(r, note: str, dur=1.3) -> np.ndarray:
    """Soft, round 'bloop' (search bubble): sine that settles up into pitch, few harmonics."""
    t = tl(dur)
    f = hz(note) * 2 ** ((-55 / 1200) * np.exp(-t / 0.03))
    ph = phase(f) + r.uniform(0, 6.28)
    x = np.sin(ph) + 0.12 * np.sin(2 * ph) + 0.03 * np.sin(3 * ph)
    return x * (1 - np.exp(-t / 0.006)) * np.exp(-t / 0.30)


def whoosh(r, dur, fc_points, amp_points, pan_points, q=1.0) -> np.ndarray:
    n = ns(dur)
    x = r.standard_normal(n)
    y = svf(x, curve(fc_points, n), q, "bp") + 0.35 * svf(x, curve(fc_points, n) * 0.6, 0.7, "lp")
    return stereo(y * curve(amp_points, n), curve(pan_points, n))


def warm_glow(r, notes, dur, amp_points) -> np.ndarray:
    """Competitor's warm light: soft triangle-ish cluster + glowing band of air."""
    n = ns(dur)
    t = np.arange(n) / SR
    x = np.zeros(n)
    for note in notes:
        ph = phase(hz(note), n) + r.uniform(0, 6.28)
        for k, a in ((1, 1.0), (3, 1 / 9), (5, 1 / 25)):
            x += a * np.sin(k * ph)
    x *= 1 + 0.08 * np.sin(2 * np.pi * 4.2 * t)
    air = norm_rms(shape(r.standard_normal(n), lambda f: hp_gain(f, 600, 2) * lp_gain(f, 1800, 2)))
    return (x / len(notes) + 0.25 * air) * curve(amp_points, n)


def heart_thump(r) -> np.ndarray:
    t = tl(0.6)
    f = 44 + (96 - 44) * np.exp(-t / 0.04)
    body = np.sin(phase(f)) * np.exp(-t / 0.12)
    body = np.tanh(1.6 * body) / np.tanh(1.6)  # odd harmonics -> audible on phone speakers
    return attack(body, 0.004)


# --- the turn ---------------------------------------------------------------------------------------
def line_drop(r, dur: float) -> np.ndarray:
    """Hero zip: a glassy tone + resonant noise sweeping DOWN two octaves onto Ab (the new tonic),
    growing, with a 20 ms breath right before the landing so the landing transient reads clearly."""
    n = ns(dur)
    t = np.arange(n) / SR
    u = t / dur
    ease = 1 - (1 - u) ** 2.4  # fast start, decelerating landing
    f = hz("Ab6") * 2 ** (-2.0 * ease)
    ph = phase(f) + r.uniform(0, 6.28)
    glass = np.sin(ph) + 0.28 * np.sin(2 * ph) + 0.1 * np.sin(3 * ph) + 0.05 * np.sin(5 * ph)
    glass *= 1 + 0.03 * np.sin(2 * np.pi * 9 * t)
    fc = 9000 * 2 ** (-3.5 * ease)
    zipn = np.vstack([svf(r.standard_normal(n), fc, 4.5, "bp") for _ in range(2)])
    air = np.vstack([svf(r.standard_normal(n), fc * 1.6, 0.7, "lp") for _ in range(2)])
    grow = 0.22 + 0.78 * u ** 1.6
    sub = np.sin(phase(hz("Ab1"), n)) + 0.5 * np.sin(phase(2 * hz("Ab1"), n))
    sub *= 0.35 * u ** 3
    breath = curve([(0.0, 1.0), (dur - 0.034, 1.0), (dur - 0.012, 0.06), (dur, 0.0)], n)
    mono = (0.55 * glass + sub) * grow
    out = stereo(mono, 0.0) + (0.55 * zipn + 0.18 * norm_rms(air) * 0.3) * grow
    out *= breath
    # spark as the line appears at the top of the shop
    spark = (np.sin(2 * np.pi * hz("Ab7") * t[: ns(0.12)]) * np.exp(-t[: ns(0.12)] / 0.025)
             + 0.6 * burst(r, 0.12, 0.002, hp=5000))
    out[:, : len(spark)] += stereo(attack(spark, 0.0005) * 0.45, 0.0)
    return out


def power_on(r) -> np.ndarray:
    """The website switching on: bright click + a short glassy Ab/Eb ping."""
    t = tl(0.35)
    x = 0.7 * burst(r, 0.35, 0.0018, hp=2200)
    x += 0.28 * np.sin(2 * np.pi * hz("Ab6") * t) * np.exp(-t / 0.06)
    x += 0.14 * np.sin(2 * np.pi * hz("Eb7") * t) * np.exp(-t / 0.035)
    return attack(x, 0.0003)


def badge_blip(r) -> np.ndarray:
    t = tl(0.3)
    f = hz("Ab6") * 2 ** ((7 / 12) * (1 - np.exp(-t / 0.012)))  # Ab6 flicks up to Eb7: "on"
    ph = phase(f)
    x = (np.sin(ph) + 0.3 * np.sin(2 * ph) * np.exp(-t / 0.03)) * np.exp(-t / 0.075)
    x[: ns(0.01)] += 0.25 * burst(r, 0.01, 0.0012, hp=4000)
    return attack(x, 0.0008)


# --- groove -----------------------------------------------------------------------------------------
def hp4(x: np.ndarray, fc: float) -> np.ndarray:
    return svf(svf(x, fc, 0.707, "hp"), fc, 0.707, "hp")


def kick(r, vel=1.0) -> np.ndarray:
    """Clean sub body tuned to Ab1 + a parallel, high-passed saturated copy (its 260-900 Hz harmonics
    imply the low pitch on phone speakers) + a short C5 'tok' and a click for the attack."""
    t = tl(0.55)
    f1 = hz("Ab1")
    f = f1 + (175 - f1) * np.exp(-t / 0.022)
    raw = np.sin(phase(f)) * np.exp(-t / 0.2)
    body = np.tanh(1.5 * raw) / np.tanh(1.5)
    grit = hp4(np.tanh(5.0 * raw), 280.0) * np.exp(-t / 0.08)
    knock = 0.2 * np.sin(2 * np.pi * 4 * f1 * t) * np.exp(-t / 0.035)
    tok = 0.22 * np.sin(2 * np.pi * hz("C5") * t) * np.exp(-t / 0.014)
    x = 0.85 * body + knock + 0.55 * grit + tok
    x[: ns(0.02)] += 0.32 * burst(r, 0.02, 0.0025, hp=2500)
    return attack(x, 0.0004) * vel


def clap(r, vel=1.0) -> np.ndarray:
    """Soft clap/rim: three micro-bursts + short tail, band-passed, with a woody rim tone."""
    t = tl(0.32)
    w = r.standard_normal(len(t))
    nz = svf(w, 1350, 1.1, "bp") + 0.45 * svf(w, 2700, 1.5, "bp")
    env = np.zeros_like(t)
    for d, a in ((0.0, 1.0), (0.0085, 0.8), (0.0175, 0.7)):
        tt = np.maximum(t - d, 0)
        env += a * (t >= d) * np.exp(-tt / 0.0042)
    env += 0.55 * (t >= 0.024) * np.exp(-np.maximum(t - 0.024, 0) / 0.065)
    rim = 0.3 * np.sin(2 * np.pi * 1650 * t) * np.exp(-t / 0.011) + 0.2 * np.sin(2 * np.pi * 820 * t) * np.exp(-t / 0.018)
    return attack(nz * env * 0.8 + rim, 0.0003) * vel


def shaker(r, vel=1.0, soft=False) -> np.ndarray:
    t = tl(0.11)
    w = r.standard_normal(len(t))
    x = svf(w, 6800, 0.7, "hp") + 0.5 * svf(w, 9500, 1.0, "bp")
    env = (1 - np.exp(-t / (0.006 if soft else 0.0025))) * np.exp(-t / (0.022 if soft else 0.03))
    return x * env * vel


def sub_bass(r, notes, t_start, t_end, fade_points) -> np.ndarray:
    """Continuous-phase sub with 2nd/3rd harmonics (audible on phones) and gentle saturation."""
    n = ns(t_end - t_start)
    t = t_start + np.arange(n) / SR
    f = np.zeros(n)
    for ts, note in notes:
        f[t >= ts] = hz(note)
    f = smooth(f, 0.014)
    ph = phase(f)
    x = np.sin(ph) + 0.45 * np.sin(2 * ph + 0.3) + 0.2 * np.sin(3 * ph + 0.8)
    x = np.tanh(1.3 * x) / np.tanh(1.3)
    x += 0.22 * shape(np.tanh(3.0 * x), lambda fr: hp_gain(fr, 260, 4) * lp_gain(fr, 1600, 2))  # phone-audible
    return x * curve(fade_points, n, t_start)


def ep_note(r, note, dur, vel=0.8) -> np.ndarray:
    """FM electric-piano voice: warm 1:1 FM with decaying index + a quick 'tine' partial."""
    f = hz(note)
    t = tl(dur)
    index = 0.35 + (0.9 + 1.5 * vel) * np.exp(-t / 0.28)
    car = np.sin(2 * np.pi * f * t + index * np.sin(2 * np.pi * f * t + r.uniform(0, 6.28)))
    tine = 0.1 * vel * np.sin(2 * np.pi * f * 13.9 * t) * np.exp(-t / 0.018) if f * 13.9 < 16000 else 0
    decay = 1.5 * (220 / f) ** 0.35
    return (car + tine) * (1 - np.exp(-t / 0.0025)) * np.exp(-t / decay) * vel


def ep_chord(r, notes, dur, vel=0.8, strum=0.007, spread=0.35) -> np.ndarray:
    n = ns(dur) + int(round(strum * len(notes) * SR))
    out = np.zeros((2, n))
    for i, note in enumerate(notes):
        x = ep_note(r, note, dur, vel * (0.9 + 0.1 * r.random()))
        p = (i / max(len(notes) - 1, 1) - 0.5) * 2 * spread
        d = int(round(strum * i * SR))
        out[:, d:d + len(x)] += stereo(x, p)
    return out / np.sqrt(len(notes))


def pluck(r, note, dur=1.1, vel=1.0) -> np.ndarray:
    """Bright, satisfying arrival pluck: harmonic series, higher partials die faster, tiny pitch flick."""
    t = tl(dur)
    f = hz(note)
    ph = phase(f * 2 ** ((18 / 1200) * np.exp(-t / 0.02))) + r.uniform(0, 6.28)
    x = np.zeros_like(t)
    for k in range(1, 15):
        if k * f > 12000:
            break
        x += (1 / k ** 1.1) * np.sin(k * ph + r.uniform(0, 0.4)) * np.exp(-t / (0.55 / (1 + 0.5 * (k - 1))))
    x += 0.22 * np.sin(2 * ph) * np.exp(-t / 0.16)
    return attack(x, 0.0008) * vel


def glass(r, note, dur=1.3, vel=1.0, bright=1.0) -> np.ndarray:
    """Soft glass-mallet chime (notification): near-harmonic partials + a felt mallet tap."""
    t = tl(dur)
    f = hz(note)
    x = modal(r, t, [(f, 1.0, 0.7), (2 * f, 0.3 * bright, 0.3), (3.0 * f, 0.1 * bright, 0.16),
                     (4.17 * f, 0.07 * bright, 0.07), (5.43 * f, 0.04 * bright, 0.045)])
    x[: ns(0.012)] += 0.12 * burst(r, 0.012, 0.0015, lp=4500)
    return attack(x, 0.0012) * vel


def chime2(r, n1, n2, gap=0.085, bright=1.0) -> np.ndarray:
    a = glass(r, n1, 1.3, 0.78, bright)
    b = glass(r, n2, 1.3, 1.0, bright)
    d = ns(gap)
    out = np.zeros(len(b) + d)
    out[: len(a)] += a
    out[d:] += b
    return out


def riser(r, dur) -> np.ndarray:
    """Into the lock-up: wide noise riser + reversed glass-chord bloom (Ab add9) peaking at the cut."""
    n = ns(dur)
    t = np.arange(n) / SR
    u = t / dur
    fc = 450 * 2 ** (4.2 * u ** 1.4)
    noise = np.vstack([svf(r.standard_normal(n), fc, 1.1, "bp") for _ in range(2)])
    noise *= u ** 1.8
    # reversed bloom: glass chord through a short reverb, flipped in time
    m = ns(1.6)
    chord = np.zeros(m)
    for note in ("Ab5", "C6", "Eb6", "Bb6"):
        chord += glass(r, note, 1.6, 1.0, 1.4)
    ir = make_ir("riser", 1.2, 0.9, 0.0)[0]
    rev = fft_conv(np.concatenate([chord, np.zeros(ns(0.9))]), ir)
    rev = (0.6 * np.concatenate([chord, np.zeros(ns(0.9))]) + rev)[::-1]
    rev = rev[len(rev) - n:] * u ** 1.2
    out = 1.1 * noise + stereo(0.6 * rev / (np.abs(rev).max() + 1e-9), 0.0)
    return out


# --- impact & end card ------------------------------------------------------------------------------
def impact_boom(r) -> np.ndarray:
    t = tl(2.6)
    f1 = hz("Ab1")
    f = f1 + (150 - f1) * np.exp(-t / 0.035)
    ph = phase(f)
    body = np.sin(ph) * np.exp(-t / 0.55)
    body = np.tanh(1.5 * body) / np.tanh(1.5)
    body += 0.32 * np.sin(2 * ph) * np.exp(-t / 0.25)  # 104 Hz octave: carries on small speakers
    return attack(body, 0.0005)


def impact_transient(r) -> np.ndarray:
    t = tl(0.3)
    x = 0.9 * burst(r, 0.3, 0.004, hp=1500)
    x += 0.5 * burst(r, 0.3, 0.012, bp=3500, q=0.7)
    x += 0.5 * np.sin(phase(120 + 60 * np.exp(-t / 0.02))) * np.exp(-t / 0.04)
    return attack(x, 0.0003)


def chord_stab(r, notes, dur) -> np.ndarray:
    """Full Ab add9 stab: detuned additive saws through a fast-closing filter envelope."""
    n = ns(dur)
    t = np.arange(n) / SR
    fc = 1300 + 5600 * np.exp(-t / 0.17)
    amp = 0.55 * np.exp(-t / 0.33) + 0.45 * np.exp(-t / 1.7)
    out = np.zeros((2, n))
    for i, note in enumerate(notes):
        f0 = hz(note)
        for v, cents in enumerate((-5.0, 5.0)):
            ph = phase(f0 * 2 ** (cents / 1200), n) + r.uniform(0, 6.28)
            sig = np.zeros(n)
            for k in range(1, 40):
                if k * f0 > 14000:
                    break
                g = (1 / k) / np.sqrt(1 + (k * f0 / fc) ** 4)
                if g.max() < 1.5e-3:
                    break
                sig += g * np.sin(k * ph + r.uniform(0, 6.28))
            p = (-0.45 if v == 0 else 0.45) * (0.4 + 0.6 * i / max(len(notes) - 1, 1))
            out += stereo(sig, p)
    return attack(out * amp / np.sqrt(2 * len(notes)), 0.0005)


def shimmer(r, notes, dur, att=0.03, tau=1.4) -> np.ndarray:
    n = ns(dur)
    t = np.arange(n) / SR
    out = np.zeros((2, n))
    for i, note in enumerate(notes):
        ph = phase(hz(note), n) + r.uniform(0, 6.28)
        x = (np.sin(ph) + 0.18 * np.sin(2 * ph)) * (1 + 0.15 * np.sin(2 * np.pi * (5.1 + 0.4 * i) * t + i))
        out += stereo(x, (i / max(len(notes) - 1, 1) - 0.5) * 1.1)
    env = (1 - np.exp(-t / att)) * np.exp(-t / tau)
    return out * env / len(notes)


def soft_tick(r) -> np.ndarray:
    t = tl(0.06)
    x = modal(r, t, [(4200, 1.0, 0.003), (7100, 0.5, 0.002)])
    x[: ns(0.01)] += 0.5 * burst(r, 0.01, 0.0009, hp=4000)
    return attack(x, 0.0003)


def sparkle(r, notes, gap=0.03) -> np.ndarray:
    d = ns(gap)
    out = np.zeros(ns(0.9) + d * len(notes))
    for i, note in enumerate(notes):
        g = glass(r, note, 0.9, 0.9 ** i, 1.3)
        out[i * d:i * d + len(g)] += g
    return out


# ------------------------------------------------------------------------------------------------
# 3. Mixer, sends, reverbs
# ------------------------------------------------------------------------------------------------
class Mixer:
    def __init__(self, name: str):
        self.name = name
        self.buses: dict[str, np.ndarray] = {}

    def bus(self, key: str) -> np.ndarray:
        if key not in self.buses:
            self.buses[key] = np.zeros((2, N))
        return self.buses[key]

    def add(self, sig, t, *, bus, gain=0.0, pan=0.0, sends=None, fin=0.0005, fout=0.005):
        st = stereo(seal(sig, fin, fout), pan) * db(gain)
        i0 = int(round(t * SR))
        a, b = max(i0, 0), min(i0 + st.shape[1], N)
        if b <= a:
            return
        seg = st[:, a - i0:b - i0]
        self.bus(bus)[:, a:b] += seg
        for name, sgain in (sends or {}).items():
            self.bus("send:" + name)[:, a:b] += seg * db(sgain)


def make_ir(tag, length, rt60, predelay, hp=170.0, lp=9500.0) -> np.ndarray:
    """Synthetic stereo IR: decorrelated band-split noise, higher bands decaying faster."""
    r = rng("ir", tag)
    n = ns(length)
    t = np.arange(n) / SR
    edges = (0.0, 600.0, 2500.0, 6500.0, SR / 2)
    damp = (1.0, 0.85, 0.6, 0.38)
    ir = np.zeros((2, n))
    f = np.fft.rfftfreq(n, 1 / SR)
    base = hp_gain(f, hp, 2) * lp_gain(f, lp, 2)
    for ch in range(2):
        W = np.fft.rfft(r.standard_normal(n))
        for bnd in range(4):
            g = base.copy()
            if edges[bnd] > 0:
                g *= hp_gain(f, edges[bnd], 4)
            if edges[bnd + 1] < SR / 2:
                g *= lp_gain(f, edges[bnd + 1], 4)
            ir[ch] += np.fft.irfft(W * g, n) * 10 ** (-3 * t / (rt60 * damp[bnd]))
    a = ns(0.006)
    ir[:, :a] *= ramp(a)
    ir = np.concatenate([np.zeros((2, ns(predelay) if predelay else 0)), ir], axis=1)
    return ir / np.sqrt(np.sum(ir ** 2, axis=1, keepdims=True))


def reverb(send: np.ndarray, ir: np.ndarray) -> np.ndarray:
    l_in = 0.75 * send[0] + 0.25 * send[1]
    r_in = 0.25 * send[0] + 0.75 * send[1]
    return np.vstack([fft_conv(l_in, ir[0]), fft_conv(r_in, ir[1])])


def pingpong(send: np.ndarray, delay: float, fb: float, repeats: int, lp0: float) -> np.ndarray:
    """Ping-pong echo, each repeat darker (mono source alternately panned; no polarity tricks)."""
    mono = 0.5 * (send[0] + send[1])
    out = np.zeros_like(send)
    for k in range(1, repeats + 1):
        d = ns(delay * k)
        if d >= N:
            break
        seg = np.zeros(N)
        seg[d:] = mono[: N - d]
        seg = shape(seg, lambda f, k=k: lp_gain(f, lp0 / (1 + 0.6 * (k - 1)), 2) * hp_gain(f, 250, 1))
        out += stereo(seg * fb ** k, 0.55 if k % 2 else -0.55)
    return out


def kick_duck(kicks, depth_db=7.0, release=0.12) -> np.ndarray:
    t = np.arange(N) / SR
    d = np.zeros(N)
    for tk in kicks:
        m = t >= tk - 0.004
        x = t[m] - tk
        shape_ = np.where(x < 0, (x + 0.004) / 0.004, np.exp(-np.maximum(x, 0) / release))
        d[m] = np.maximum(d[m], shape_)
    return db(-depth_db * d)


def render_world(mx: Mixer, irs: dict, echo: dict | None = None) -> np.ndarray:
    out = np.zeros((2, N))
    for key, sig in mx.buses.items():
        if not key.startswith("send:"):
            out += sig
    for name, ir in irs.items():
        if "send:" + name in mx.buses:
            out += reverb(mx.buses["send:" + name], ir)
    if echo and "send:echo" in mx.buses:
        out += pingpong(mx.buses["send:echo"], **echo)
    return out


# ------------------------------------------------------------------------------------------------
# 4. Sequencer: the film, cue by cue
# ------------------------------------------------------------------------------------------------
def seq_open_and_night(mx: Mixer):
    """0 - 5.85: dusk bed, clock, sign, lights, night, minor pad, search pings, leaving, hush."""
    L = CUE["lightsOff"]
    nf0, nf1 = CUE["nightFall"]
    h0, h1 = CUE["hush"]
    web = CUE["webOn"]
    r = rng("amb")
    amb = amb_street(r, web + 0.8)
    n = amb["bright"].shape[1]
    step = [(0.0, 1.0)]
    for i, tc in enumerate(L):  # each click darkens the street one notch
        lvl = (0.72, 0.48, 0.30)[i] if i < 3 else 0.3
        step += [(tc + 0.012, step[-1][1]), (tc + 0.05, lvl)]
    step += [(nf0, 0.30), (nf1, 0.0)]
    bright = curve(step, n)
    master = curve([(0.0, 0.0), (0.22, 1.0), (h0 - 0.2, 1.0), (h0 + 0.12, 0.16), (web - 0.1, 0.16), (web + 0.5, 0.0)], n)
    dusk = curve([(0.0, 1.0), (nf0, 1.0), (nf1, 0.35)], n)
    night = curve([(0.0, 0.0), (L[-1], 0.05), (nf0, 0.2), (nf1, 1.0)], n)
    air = (bright * amb["bright"] + (1 - bright) * 0.9 * amb["dark"]) * dusk
    bed = (db(-31) * air + db(-29) * night * amb["night"]) * master
    mx.add(bed, 0.0, bus="amb", fin=0.01, fout=0.05)
    hum_env = curve([(0.0, 1.0), (nf1, 0.65), (h0 - 0.2, 0.65), (h0 + 0.1, 0.0)], n) * master
    mx.add(db(-41) * amb["hum"] * hum_env + db(-37) * amb["rumble"] * hum_env, 0.0, bus="amb", fin=0.05, fout=0.05)
    lights = [(0.0, 1.0)] + sum(([(tc, 1 - i / 3), (tc + 0.02, 1 - (i + 1) / 3)] for i, tc in enumerate(L)), [])
    mx.add(db(-50) * amb["buzz"] * curve(lights, n) * curve([(0, 0), (0.25, 1), (1, 1)], n), 0.0,
           bus="amb", pan=0.15, fin=0.05)

    # clock tick + small chime (sky clock, left)
    mx.add(clock_tick(rng("clock")), CUE["clockTick"], bus="sfx", gain=-15, pan=pan_x(GEO["skyClock"]["x"]),
           sends={"room": -14, "hall": -22})
    # sign flip: knock, swing wobble, softer settle knock (door sign, right)
    sx = pan_x(GEO["doorSign"]["x"] + GEO["doorSign"]["w"] / 2)
    sf = CUE["signFlip"]
    mx.add(wood_knock(rng("knock", 1), 1.0), sf, bus="sfx", gain=-12, pan=sx, sends={"room": -12})
    mx.add(sign_creak(rng("creak"), 0.3, [(0, 0), (0.03, 1), (0.13, 0.35), (0.19, 0.6), (0.3, 0)]),
           sf + 0.02, bus="sfx", gain=-27, pan=sx, sends={"room": -14})
    mx.add(wood_knock(rng("knock", 2), 0.5), sf + 0.30, bus="sfx", gain=-15, pan=sx, sends={"room": -12})
    # lights off: three clicks, each with a tiny power-down thump
    for i, tc in enumerate(L):
        p = (-0.25, 0.05, 0.3)[i % 3]
        mx.add(light_click(rng("click", i)), tc, bus="sfx", gain=-11, pan=p, sends={"room": -13})
        mx.add(power_down(rng("pdown", i)), tc + 0.008, bus="sfx", gain=-17 - 1.5 * i, pan=p * 0.5)

    # night: crickets (subtle), minor pad Fm9 -> Dbmaj9 -> Bbm11 (lonely), dropping away at the hush
    mx.add(crickets(rng("crickets"), h0 + 0.1 - (nf0 + 0.6)), nf0 + 0.6, bus="amb", gain=-47,
           fin=0.3, fout=0.25, sends={"hall": -20})
    s0, lv0 = CUE["searchPings"][0], CUE["pingsLeave"][0]
    chords = [
        (nf0, s0, 1.3, 0.45, ["F2", "F3", "Ab3", "C4", "G4"], 1.0),
        (s0, lv0, 0.35, 0.45, ["Db3", "F3", "Ab3", "C4", "Eb4"], 1.0),
        (lv0, h0 - 0.12, 0.35, 0.3, ["Bb2", "F3", "Ab3", "Db4", "Eb4"], 0.9),
    ]
    cut = [(nf0, 420), (nf1, 780), (s0, 950), (lv0, 1100), (h0, 650)]
    sig, st = pad(rng("pad-minor"), chords, cut, tilt=1.3)
    mx.add(sig, st, bus="pad", gain=-19, sends={"hall": -9}, fin=0.05, fout=0.05)

    # search pings: F-minor-pentatonic, falling like unanswered questions; panned to each searcher
    notes = ["C6", "Eb6", "Bb5", "Ab5", "F5"]
    for i, tp in enumerate(CUE["searchPings"]):
        s = GEO["searchers"][i % len(GEO["searchers"])]
        p = {"left": -0.6, "right": 0.6}.get(s["side"], 0.0)
        mx.add(lonely_ping(rng("ping", i), notes[i % 5]), tp, bus="sfx", gain=-12.5, pan=p,
               sends={"echo": -9, "hall": -13})

    # they drift away to the right; a faint warm glow from someone else's shop (right)
    p0, p1 = CUE["pingsLeave"]
    d = p1 - p0
    mx.add(whoosh(rng("leave"), d, [(0, 520), (d * 0.5, 2300), (d, 900)], [(0, 0), (d * 0.45, 1), (d, 0)],
                  [(0, -0.7), (d, 0.85)], q=1.0), p0, bus="sfx", gain=-21, sends={"hall": -14})
    cg = CUE["competitorGlow"]
    gd = h0 + 0.15 - (cg - 0.3)
    mx.add(warm_glow(rng("glow"), ["Db4", "F4", "Ab4"], gd,
                     [(0, 0), (0.55, 1), (h0 - 0.1 - (cg - 0.3), 0.55), (gd, 0)]),
           cg - 0.3, bus="sfx", gain=-26, pan=pan_x(GEO["competitorGlow"]["x"]), sends={"hall": -10})

    # hush: one heartbeat-like thump and one quiet ping (Ab: a hint of where we are going)
    lp_ = CUE["lonePing"]
    mx.add(heart_thump(rng("thump")), lp_, bus="sfx", gain=-15)
    mx.add(lonely_ping(rng("lone"), "Ab5", 1.5), lp_, bus="sfx", gain=-21, sends={"echo": -6, "hall": -9})


def seq_turn_and_groove(mx: Mixer) -> list:
    """5.85 - 11.94: line drop, web on, groove in Ab major, arrivals, sunrise, notifications, riser."""
    ld0, ld1 = CUE["lineDrop"]
    web = CUE["webOn"]
    imp = CUE["impact"]
    m0, _m1 = CUE["morphToLockup"]
    su0, su1 = CUE["sunrise"]
    gap0 = imp - 0.06

    mx.add(line_drop(rng("line"), ld1 - ld0), ld0, bus="sfx", gain=-1.5, sends={"hall": -12, "room": -16},
           fin=0.0005, fout=0.002)
    mx.add(power_on(rng("on")), web, bus="sfx", gain=-11, sends={"room": -10, "hall": -16})

    # drums on the 120 BPM grid anchored at webOn (webOn = beat 1)
    kicks = [web + i * BEAT for i in range(64) if web + i * BEAT < m0 - 0.2]
    for i, tk in enumerate(kicks):
        mx.add(kick(rng("kick", i), 1.0 if i == 0 else (0.86 if i % 2 == 0 else 0.8)), tk, bus="drums", gain=-1.5)
    for j, tc in enumerate([web + BEAT * (1 + 2 * j) for j in range(32)]):
        if tc <= kicks[-1] + 1e-6:
            mx.add(clap(rng("clap", j), 1.0), tc, bus="drums", gain=-15, pan=-0.08, sends={"room": -8})
    hat_t = [web + BEAT + 0.25 * j for j in range(64)]
    for j, th in enumerate(t for t in hat_t if t < m0 - 0.05):
        off = j % 2 == 1
        mx.add(shaker(rng("hat", j), 1.0 if off else 0.55), th, bus="drums", gain=-23, pan=0.22,
               sends={"room": -12})
    ghost = [web + 0.125 * j for j in range(1, 80) if j % 2 == 1]
    for j, tg in enumerate(t for t in ghost if su0 <= t < m0 - 0.05):
        mx.add(shaker(rng("ghost", j), 0.5, soft=True), tg, bus="drums", gain=-28, pan=-0.3)

    # harmony: Ab add9 -> Db maj9 -> Eb sus4 -> Eb (V) into the impact
    c2, c3 = web + 4 * BEAT, imp - 3 * BEAT
    chords = [
        (web, c2, 0.06, 0.3, ["Eb3", "Ab3", "C4", "Eb4", "Bb4"], 1.0),
        (c2, c3, 0.12, 0.3, ["Db3", "F3", "C4", "Eb4", "Ab4"], 1.0),
        (c3, m0, 0.12, 0.2, ["Eb3", "Bb3", "Eb4", "Ab4"], 1.0),
        (m0, gap0 + 0.02, 0.1, 0.02, ["Eb3", "Bb3", "Eb4", "G4"], 1.1),
    ]
    cut = [(web, 900), (web + 0.6, 2300), (su0, 2300), (su1, 5200), (m0, 4200), (gap0, 7000)]
    sig, st = pad(rng("pad-major"), chords, cut, tilt=1.25)
    mx.add(sig, st, bus="pad", gain=-18.5, sends={"hall": -10}, fin=0.002, fout=0.01)
    for tc, notes, vel in ((web, ["Ab3", "Eb4", "Bb4", "C5"], 0.95), (c2, ["F4", "Ab4", "C5", "Eb5"], 0.8),
                           (c3, ["Eb4", "Ab4", "Bb4", "Eb5"], 0.75)):
        mx.add(ep_chord(rng("ep", tc), notes, 2.2, vel), tc, bus="ep", gain=-12, sends={"hall": -12, "room": -14})
    mx.add(sub_bass(rng("bass"), [(web, "Ab1"), (c2, "Db2"), (c3, "Eb2")], web, m0 + 0.4,
                    [(web, 0.0), (web + 0.004, 1.0), (m0, 1.0), (m0 + 0.35, 0.0)]),
           web, bus="bass", gain=-9.5, fout=0.02)

    # status blip on the OPEN 24/7 badge, then the customers' pings arrive (rising, major pentatonic)
    dsx = pan_x(GEO["doorSign"]["x"] + GEO["doorSign"]["w"] / 2)
    mx.add(badge_blip(rng("badge")), CUE["badgePulse"], bus="sfx", gain=-17, pan=dsx, sends={"room": -10, "hall": -18})
    bx = pan_x(GEO["bookButton"]["cx"])
    for i, (ta, note) in enumerate(zip(CUE["pingsArrive"], ["Ab5", "Bb5", "C6", "Eb6"])):
        side = GEO["searchers"][i % len(GEO["searchers"])]["side"]
        src = {"left": -0.65, "right": 0.65}.get(side, 0.0)
        mx.add(whoosh(rng("in", i), 0.16, [(0, 1300), (0.16, 5200)], [(0, 0), (0.14, 1), (0.16, 0)],
                      [(0, src), (0.16, bx)], q=1.4), ta - 0.16, bus="sfx", gain=-29)
        mx.add(pluck(rng("pluck", i), note), ta, bus="sfx", gain=-15, pan=bx, sends={"room": -10, "hall": -15})

    # sunrise: brightness swell (pad filter above), airy wash, high shimmer
    sd = su1 - su0
    nz = rng("sunair").standard_normal((2, ns(sd + 1.0)))
    wash = norm_rms(shape(nz, lambda f: hp_gain(f, 3000, 2) * lp_gain(f, 9000, 2)))
    wash *= curve([(0, 0), (sd * 0.7, 1), (sd + 1.0, 0)], wash.shape[1])
    mx.add(wash, su0, bus="sfx", gain=-37, fin=0.05, fout=0.05)
    sh = shimmer(rng("sunshim"), ["Eb6", "Bb6"], gap0 - su0, att=0.9, tau=30.0)
    mx.add(sh, su0, bus="pad", gain=-25, sends={"hall": -6}, fout=0.02)
    for i, (tn, (a, b)) in enumerate(zip(CUE["notifications"],
                                         [("Ab5", "C6"), ("Bb5", "Eb6"), ("C6", "F6"), ("Eb6", "Ab6")])):
        mx.add(chime2(rng("notif", i), a, b, bright=0.9 + 0.1 * i), tn, bus="sfx", gain=-15,
               pan=(-0.15, 0.15)[i % 2], sends={"room": -9, "hall": -14})

    # riser into the lock-up; ends exactly where the gap begins
    mx.add(riser(rng("riser"), gap0 - m0), m0, bus="sfx", gain=-2.5, fin=0.005, fout=0.002)
    return kicks


def seq_impact_and_card(mx: Mixer):
    imp, end = CUE["impact"], CUE["end"]
    mx.add(impact_boom(rng("boom")), imp, bus="impact", gain=-2.5, fin=0.0003)
    mx.add(impact_transient(rng("hit")), imp, bus="impact", gain=-7, sends={"room": -6}, fin=0.0002)
    stab = ["Ab2", "Eb3", "Ab3", "C4", "Eb4", "Bb4", "C5", "Eb5"]
    mx.add(chord_stab(rng("stab"), stab, 3.0), imp, bus="impact", gain=-7, sends={"hall": -7}, fin=0.0003)
    mx.add(ep_chord(rng("ep-imp"), ["Ab3", "C4", "Eb4", "Bb4", "C5"], 2.8, 1.0, strum=0.0), imp,
           bus="impact", gain=-12, sends={"hall": -9}, fin=0.0003)
    mx.add(shimmer(rng("shim"), ["Ab5", "C6", "Eb6", "Bb6", "C7"], 3.0, att=0.03, tau=1.3), imp,
           bus="impact", gain=-17, sends={"hall": 0}, fin=0.0003)
    ring, st = pad(rng("pad-end"), [(imp, end - 1.6, 0.4, 1.1, ["Ab2", "Eb3", "Ab3", "C4", "Eb4", "Bb4"], 1.0)],
                   [(imp, 2600), (end - 1.0, 900)], tilt=1.3)
    mx.add(ring, st, bus="pad", gain=-17, sends={"hall": -8}, fin=0.01)
    mx.add(soft_tick(rng("tick", 1)), CUE["taglineIn"], bus="sfx", gain=-26, pan=-0.1, sends={"room": -8})
    mx.add(sparkle(rng("spark", 1), ["Eb6", "Bb6"]), CUE["taglineIn"] + 0.01, bus="sfx", gain=-28, pan=-0.2,
           sends={"hall": -6})
    mx.add(soft_tick(rng("tick", 2)), CUE["serviceIn"], bus="sfx", gain=-27, pan=0.1, sends={"room": -8})
    mx.add(sparkle(rng("spark", 2), ["Ab6", "C7"]), CUE["serviceIn"] + 0.01, bus="sfx", gain=-30, pan=0.2,
           sends={"hall": -6})


def mixdown() -> np.ndarray:
    imp = CUE["impact"]
    pre, post = Mixer("pre"), Mixer("post")
    seq_open_and_night(pre)
    kicks = seq_turn_and_groove(pre)
    seq_impact_and_card(post)
    duck = kick_duck(kicks)
    for key, depth in (("pad", 0.8), ("bass", 1.0), ("ep", 0.4)):  # the pump is the pulse on phones
        if key in pre.buses:
            pre.buses[key] *= duck ** depth
    rooms = {"room": make_ir("room", 1.4, 0.75, 0.010), "hall": make_ir("hall", 4.0, 2.4, 0.022)}
    a = dc_block(render_world(pre, rooms, echo={"delay": 3 * BEAT / 4, "fb": 0.42, "repeats": 6, "lp0": 3200.0}))
    # the gap: everything before the impact (incl. reverb/echo tails) is cut 60 ms before it
    a *= curve([(0.0, 1.0), (imp - 0.075, 1.0), (imp - 0.06, 0.0)], N)
    b = dc_block(render_world(post, rooms))
    return a + b


# ------------------------------------------------------------------------------------------------
# 5. Master
# ------------------------------------------------------------------------------------------------
def dc_block(x: np.ndarray, fc=12.0) -> np.ndarray:
    R = 1.0 - 2 * np.pi * fc / SR
    out = np.empty_like(x)
    for ch in range(x.shape[0]):
        xs = x[ch].tolist()
        ys = [0.0] * len(xs)
        px = py = 0.0
        for i, v in enumerate(xs):
            py = v - px + R * py
            px = v
            ys[i] = py
        out[ch] = ys
    return out


def soft_clip(x: np.ndarray, knee=0.75) -> np.ndarray:
    a = np.abs(x)
    y = x.copy()
    m = a > knee
    y[m] = np.sign(x[m]) * (knee + (1 - knee) * np.tanh((a[m] - knee) / (1 - knee)))
    return y


def tp_env(x: np.ndarray, os_=4) -> np.ndarray:
    """Per-sample true-peak estimate (FFT x4 oversampling, max over channels)."""
    n = x.shape[1]
    up = np.fft.irfft(np.fft.rfft(x, axis=1), n=os_ * n, axis=1) * os_
    return np.abs(up).reshape(x.shape[0], n, os_).max(axis=2).max(axis=0)


def limiter(x: np.ndarray, ceiling_db: float, look=0.002, release=0.06) -> np.ndarray:
    """Look-ahead brickwall: hold-min of needed gain, linear release, moving-average smoothing.
    Smoothing radius <= hold radius guarantees the gain never exceeds what each sample needs."""
    need = np.minimum(1.0, db(ceiling_db) / np.maximum(tp_env(x), 1e-9))
    if need.min() >= 1.0:
        return x
    H = ns(look)
    g = np.lib.stride_tricks.sliding_window_view(np.pad(need, H, mode="edge"), 2 * H + 1).min(axis=1)
    step = 1.0 / ns(release)
    gl = g.tolist()
    cur = 1.0
    for i, v in enumerate(gl):
        cur = min(v, cur + step)
        gl[i] = cur
    g = np.array(gl)
    c = np.cumsum(np.pad(g, (H + 1, H), mode="edge"))
    g = (c[2 * H + 1:] - c[: -(2 * H + 1)]) / (2 * H + 1)
    return x * g[: x.shape[1]]


def k_weight_mag(f):
    """|H(f)| of the ITU-R BS.1770 K-weighting pre-filter (standard 48 kHz biquads)."""
    z = np.exp(1j * 2 * np.pi * np.asarray(f) / SR)

    def biquad(b, a):
        return (b[0] + b[1] / z + b[2] / z ** 2) / (a[0] + a[1] / z + a[2] / z ** 2)

    return np.abs(biquad([1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585])
                  * biquad([1.0, -2.0, 1.0], [1, -1.99004745483398, 0.99007225036621]))


def lufs(y: np.ndarray) -> float:
    """BS.1770-4 integrated loudness (400 ms blocks, 75 % overlap, -70 LUFS abs. / -10 LU rel. gates)."""
    k = shape(y, k_weight_mag)
    c = np.concatenate([[0.0], np.cumsum(np.sum(k ** 2, axis=0))])
    w, hop = ns(0.4), ns(0.1)
    st = np.arange(0, y.shape[1] - w + 1, hop)
    z = (c[st + w] - c[st]) / w
    lv = -0.691 + 10 * np.log10(np.maximum(z, 1e-20))
    ab = lv > -70
    rel = -0.691 + 10 * np.log10(z[ab].mean()) - 10
    return float(-0.691 + 10 * np.log10(z[ab & (lv > rel)].mean()))


def master_fades(x: np.ndarray) -> np.ndarray:
    end = CUE["end"]
    env = curve([(0.0, 0.0), (0.12, 1.0), (end - 1.3, 1.0), (end - 0.42, 0.0)], N)
    return x * env


# ------------------------------------------------------------------------------------------------
# 6. ffmpeg loudnorm (two-pass) and export
# ------------------------------------------------------------------------------------------------
def _ff(args, data: bytes) -> subprocess.CompletedProcess:
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0"] + args
    p = subprocess.run(cmd, input=data, capture_output=True)
    if p.returncode != 0:
        raise SystemExit(p.stderr.decode(errors="replace")[-2000:])
    return p


def _json_tail(text: str) -> dict:
    return json.loads(text[text.rindex("{"): text.rindex("}") + 1])


def _raw(y: np.ndarray) -> bytes:
    return np.ascontiguousarray(y.T, dtype="<f4").tobytes()


def loudnorm_measure(y: np.ndarray) -> dict:
    p = _ff(["-af", f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=50:print_format=json", "-f", "null", "-"], _raw(y))
    return {k: float(v) if k != "normalization_type" else v for k, v in _json_tail(p.stderr.decode()).items()}


def loudnorm_apply(y: np.ndarray, m: dict, target_i: float) -> tuple:
    lra = min(50.0, max(11.0, m["input_lra"] + 1.0))  # keep loudnorm linear: never compress the score
    af = (f"loudnorm=I={target_i:.3f}:TP={TARGET_TP}:LRA={lra:.2f}:measured_I={m['input_i']}:"
          f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true:print_format=json")
    p = _ff(["-af", af, "-ar", str(SR), "-ac", "2", "-f", "f32le", "pipe:1"], _raw(y))
    out = np.frombuffer(p.stdout, dtype="<f4").reshape(-1, 2).T.astype(float)
    return out, _json_tail(p.stderr.decode())


def master(mix: np.ndarray) -> np.ndarray:
    x = master_fades(mix)  # (each world was DC-blocked before the gap gate)
    tp0 = 20 * np.log10(tp_env(x).max())
    g = PRE_TP_CEIL - 4.0 - tp0  # first guess; refined by measurement below
    for it in range(8):
        y = limiter(soft_clip(x * db(g)), PRE_TP_CEIL)
        lv = lufs(y)
        print(f"  gain stage {it}: gain {g:+.2f} dB -> {lv:.2f} LUFS (BS.1770 @ 48 kHz)")
        if abs(TARGET_LUFS - lv) < 0.02:
            break
        g += TARGET_LUFS - lv
    m = loudnorm_measure(y)  # pass 1
    # loudnorm's pass-1 analysis reads this material ~0.1 LU apart from a 48 kHz BS.1770 meter
    # (ffmpeg's own ebur128 included); fold that bias into the pass-2 target so the delivered file
    # meters at TARGET_LUFS on the standard meter.
    bias = m["input_i"] - lv
    print(f"  loudnorm pass 1: {m['input_i']:.2f} LUFS, {m['input_tp']:.2f} dBTP, LRA {m['input_lra']:.1f} (bias {bias:+.2f})")
    out, info = loudnorm_apply(y, m, TARGET_LUFS + bias)  # pass 2 (linear)
    print(f"  loudnorm pass 2: {info['normalization_type']}, out {info['output_i']} LUFS / {info['output_tp']} dBTP")
    if info["normalization_type"] != "linear":
        raise SystemExit("loudnorm fell back to dynamic mode; lower PRE_TP_CEIL")
    fixed = np.zeros((2, N))
    k = min(N, out.shape[1])
    fixed[:, :k] = out[:, :k]  # exact film length regardless of filter padding
    return fixed * master_fades(np.ones((2, N)))  # re-assert the silent tail


def write_wav(y: np.ndarray, path: pathlib.Path):
    r = rng("dither")
    d = r.random((2, N)) - r.random((2, N))  # TPDF, +-1 LSB
    q = np.clip(np.round(y * 32767.0 + d), -32768, 32767).astype("<i2")
    q[:, np.all(np.abs(y) < 0.5 / 32768, axis=0)] = 0  # true digital silence where the score is silent
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.ascontiguousarray(q.T).tobytes())


def read_wav(path: pathlib.Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, w.getnchannels())
    return data.T.astype(float) / 32768.0


# ------------------------------------------------------------------------------------------------
# 7. Verification (analysis only; writes PNGs to review/audio/)
# ------------------------------------------------------------------------------------------------
def _png(path: pathlib.Path, rgb: np.ndarray):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[i].tobytes() for i in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _rms_db(x: np.ndarray, win=0.010, hop=0.001) -> tuple:
    mono = 0.5 * (x[0] + x[1])
    c = np.concatenate([[0.0], np.cumsum(mono ** 2)])
    w, h = ns(win), ns(hop)
    starts = np.arange(0, len(mono) - w + 1, h)
    rms = np.sqrt((c[starts + w] - c[starts]) / w)
    return starts / SR, 20 * np.log10(np.maximum(rms, 1e-9))


def levels_png(x: np.ndarray, path: pathlib.Path):
    """10 ms RMS (dBFS, -80..0) per pixel column, 100 px/s, cue markers, 1 s grid."""
    t, lv = _rms_db(x, 0.010, 0.010)
    W, H = len(t), 420
    img = np.full((H, W, 3), (18, 20, 30), np.uint8)
    for s in range(int(DUR) + 1):
        img[:, min(W - 1, s * 100)] = (45, 48, 62)
    for d in (-60, -40, -20):
        img[int(-d / 80 * (H - 1))] = (40, 42, 55)
    col = {"webOn": (255, 74, 28), "impact": (255, 74, 28), "hush": (120, 140, 255), "lineDrop": (255, 181, 71)}
    for name, v in CUE.items():
        for tv in (v if isinstance(v, list) else [v]):
            xi = int(round(tv * 100))
            if 0 <= xi < W:
                img[: H // 6 if name not in col else H, xi] = col.get(name, (110, 110, 120))
    for i, v in enumerate(lv):
        y = int(np.clip(-v / 80, 0, 1) * (H - 1))
        img[y:, i] = np.maximum(img[y:, i], (246, 239, 227) if v > -60 else (120, 118, 110))
    _png(path, img)


def verify(path: pathlib.Path) -> dict:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    rep = {}
    pr = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=sample_rate,channels,codec_name,duration",
                         "-of", "json", str(path)], capture_output=True, text=True, check=True)
    rep["ffprobe"] = json.loads(pr.stdout)["streams"][0]
    eb = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                         "-f", "null", "-"], capture_output=True, text=True).stderr
    summ = eb[eb.rfind("Summary:"):]
    rep["integrated_lufs"] = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ)[1])
    rep["integrated_lufs_bs1770_numpy"] = round(lufs(read_wav(path)), 3)
    rep["lra_lu"] = float(re.search(r"LRA:\s+(-?[\d.]+) LU", summ)[1])
    rep["true_peak_dbtp"] = float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summ)[1])
    mom = [(float(a), float(b)) for a, b in re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+)", eb)]
    x = read_wav(path)
    rep["frames"] = x.shape[1]
    rep["sample_peak_dbfs"] = float(20 * np.log10(np.abs(x).max()))
    rep["clipped_samples"] = int(np.sum(np.abs(x) >= 32767 / 32768))
    rep["dc_offset"] = [float(v) for v in x.mean(axis=1)]
    t, lv = _rms_db(x)
    onsets = {}
    for name in ("webOn", "impact"):
        T = CUE[name]
        w = ns(0.010) // ns(0.001)
        best = (-1e9, 0.0, 0.0, 0.0)
        for i in np.nonzero(np.abs(t - T) <= 0.033)[0]:
            if i - w >= 0:
                jump = lv[i] - lv[i - w]
                if jump > best[0]:
                    best = (jump, t[i], lv[i - w], lv[i])
        onsets[name] = {"cue": T, "onset_at": round(best[1], 4), "offset_ms": round((best[1] - T) * 1000, 1),
                        "rms_before_dbfs": round(best[2], 1), "rms_after_dbfs": round(best[3], 1),
                        "jump_db": round(best[0], 1)}
    rep["onsets"] = onsets
    imp = CUE["impact"]
    g = x[:, ns(imp - 0.058):ns(imp - 0.002)]
    rep["gap_rms_dbfs"] = float(20 * np.log10(max(np.sqrt(np.mean(g ** 2)), 1e-9)))
    rep["gap_peak_dbfs"] = float(20 * np.log10(max(np.abs(g).max(), 1e-9)))
    tail = x[:, N - ns(0.4):]
    rep["tail_last_0.4s_rms_dbfs"] = float(20 * np.log10(max(np.sqrt(np.mean(tail ** 2)), 1e-9)))
    rep["first_last_sample"] = [float(np.abs(x[:, 0]).max()), float(np.abs(x[:, -1]).max())]
    # clicks: biggest 2nd-difference spikes vs. their local 20 ms RMS, away from intended transients
    mono = 0.5 * (x[0] + x[1])
    d2 = np.abs(mono[2:] - 2 * mono[1:-1] + mono[:-2])
    c = np.concatenate([[0.0], np.cumsum(mono ** 2)])
    k = ns(0.010)
    idx = np.arange(2, len(mono))
    lo, hi = np.clip(idx - k, 0, N), np.clip(idx + k, 0, N)
    loc = np.sqrt((c[hi] - c[lo]) / (hi - lo)) + 1e-6
    ratio = d2 / loc
    rep["max_d2_over_local_rms"] = float(ratio.max())
    rep["max_d2_at"] = float((np.argmax(ratio) + 2) / SR)
    rep["lr_correlation"] = float(np.corrcoef(x[0], x[1])[0, 1])
    st_pow = 0.5 * (np.mean(x[0] ** 2) + np.mean(x[1] ** 2))
    # (L+R)/2 power x2 vs. mean channel power: +3 dB = identical channels, 0 = uncorrelated, <0 = cancellation
    rep["mono_sum_vs_stereo_db"] = float(10 * np.log10(np.mean((0.5 * (x[0] + x[1])) ** 2) * 2 / st_pow))
    C_ = CUE
    secs = [("open", 0.0, C_["nightFall"][0]), ("night", C_["nightFall"][0], C_["searchPings"][0]),
            ("search", C_["searchPings"][0], C_["pingsLeave"][0]), ("leave", C_["pingsLeave"][0], C_["hush"][0]),
            ("hush", *C_["hush"]), ("lineDrop", *C_["lineDrop"]), ("groove", C_["webOn"], C_["sunrise"][0]),
            ("sunrise", C_["sunrise"][0], C_["morphToLockup"][0]), ("riser", C_["morphToLockup"][0], imp - 0.06),
            ("impact", imp, imp + 1.0), ("ring-out", imp + 1.0, C_["end"] - 0.4)]
    # max momentary (400 ms) loudness of windows lying inside each section, from ffmpeg ebur128
    rep["momentary_max_lufs"] = {f"{s} {a:.2f}-{b:.2f}": max((m for tt_, m in mom if a + 0.4 <= tt_ <= b + 0.02),
                                                               default=None) for s, a, b in secs}
    # pictures
    for args, name in (
        (["-lavfi", "showspectrumpic=s=1600x800:legend=1:scale=log:fscale=log:stop=20000:color=intensity"], "spectrogram.png"),
        (["-lavfi", "showwavespic=s=1600x500:split_channels=1:colors=#2F6FD0|#FF4A1C"], "waveform.png"),
    ):
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path)] + args
                       + ["-frames:v", "1", str(REVIEW_DIR / name)], check=True)
    levels_png(x, REVIEW_DIR / "levels-with-cues.png")
    return rep


# ------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()
    print(f"cues: {CUES_FILE.relative_to(ROOT)}  ({DUR:.3f} s, {N} frames @ {SR} Hz)")
    print("synthesising + mixing ...")
    mix = mixdown()
    print("mastering ...")
    y = master(mix)
    write_wav(y, OUT_WAV)
    print(f"wrote {OUT_WAV.relative_to(ROOT)}")
    if not args.no_verify:
        rep = verify(OUT_WAV)
        print(json.dumps(rep, indent=2))
        print(f"review images: {REVIEW_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    sys.exit(main())
