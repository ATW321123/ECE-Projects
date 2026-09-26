#!/usr/bin/env python3
"""
ir_dsp.py -- DSP pipeline for the analog IR receiver's TIA tap ("ANA" pin).

Stages
  0. Signal source: synthetic NEC frame through a TIA model, OR an LTspice
     .txt export (non-uniform timestep -> resampled onto a uniform ADC grid).
  1. Spectrum: Welch PSD + spectrogram.
  2. Demodulators (3 ways, for comparison):
       a) FIR bandpass -> rectify -> lowpass
       b) Blockwise Goertzel (Hann-windowed; MCU-friendly)
       c) I/Q mixer -> FIR lowpass -> |.| (tolerant of 36-40 kHz remotes)
  3. Adaptive threshold slicer: floor/peak trackers + hysteresis + SNR gate.
  4. Run-length -> glitch filter -> NEC decoder (frames + repeat codes).
  Extra: decode_digital_bursts() turns the comparator OUT (38 kHz bursts)
         into an envelope so the analog path can be decoded the same way.

Usage
  python ir_dsp.py                                # synthetic demo + plots
  python ir_dsp.py --sweep                        # sensitivity sweep
  python ir_dsp.py --write-pwl nec.pwl            # stimulus for LTspice
  python ir_dsp.py --ltspice run.txt --node "V(tia)" [--out-node "V(out)"]
"""
import argparse
import os
import numpy as np
from scipy import signal
from scipy.ndimage import maximum_filter1d
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FS = 500_000        # ADC rate (Pico target)
FC = 38_000         # carrier
VDC = 2.5
RF, CF = 330e3, 4.7e-12

# ============================================================== NEC protocol
T_NEC = 562.5e-6


def nec_bytes(addr, cmd, extended=False):
    if extended:
        return [addr & 0xFF, (addr >> 8) & 0xFF, cmd & 0xFF, ~cmd & 0xFF]
    return [addr & 0xFF, ~addr & 0xFF, cmd & 0xFF, ~cmd & 0xFF]


def nec_timeline(addr, cmd, repeats=0, lead=5e-3, tail=10e-3, extended=False):
    """List of (carrier_on, duration) segments for one frame + repeat codes."""
    seg = [(0, lead)]
    frame = [(1, 16 * T_NEC), (0, 8 * T_NEC)]
    for b in nec_bytes(addr, cmd, extended):
        for i in range(8):                       # LSB first
            frame += [(1, T_NEC), (0, (3 if (b >> i) & 1 else 1) * T_NEC)]
    frame += [(1, T_NEC)]
    seg += frame
    period = 108e-3
    used = sum(d for _, d in frame)
    for _ in range(repeats):
        seg += [(0, period - used)]
        rep = [(1, 16 * T_NEC), (0, 4 * T_NEC), (1, T_NEC)]
        seg += rep
        used = sum(d for _, d in rep)
    seg += [(0, tail)]
    return seg


def timeline_to_envelope(seg, fs):
    edges = np.cumsum([0] + [d for _, d in seg])
    n = int(round(edges[-1] * fs))
    t = np.arange(n) / fs
    env = np.zeros(n)
    for (on, _), a, b in zip(seg, edges[:-1], edges[1:]):
        if on:
            env[int(round(a * fs)):int(round(b * fs))] = 1.0
    return t, env


def write_pwl(path, seg, i_pk=0.2e-6, fc=FC, duty=1 / 3, tr=50e-9):
    """PWL photocurrent (carrier bursts only) for an LTspice current source.
    Add ambient DC / flicker as separate sources in parallel."""
    pts, t0 = [(0.0, 0.0)], 0.0
    for on, d in seg:
        if on:
            for k in range(int(round(d * fc))):
                ts = t0 + k / fc
                pts += [(ts, 0.0), (ts + tr, i_pk),
                        (ts + duty / fc, i_pk), (ts + duty / fc + tr, 0.0)]
        t0 += d
    pts.append((t0, 0.0))
    with open(path, "w") as f:
        for t, i in pts:
            f.write(f"{t:.9e}\t{i:.6e}\n")
    return len(pts)


# ======================================================= synthetic front end
def synth_tia(seg, i_sig=0.2e-6, i_amb=2e-6, flicker=0.3e-6, f_flick=120,
              interferer=0.0, f_int=45e3, noise_v=3e-3, fc=FC, duty=1 / 3,
              fs=FS, os_factor=8, adc_bits=12, vref=3.3, seed=0):
    """Photocurrent -> TIA (1st-order, Rf||Cf) -> ADC.  Simulated at
    fs*os_factor and sampled WITHOUT an anti-alias filter, like the real
    ADC, so carrier harmonics fold back realistically."""
    rng = np.random.default_rng(seed)
    fsim = fs * os_factor
    t, env = timeline_to_envelope(seg, fsim)
    carrier = ((t * fc) % 1.0) < duty
    i_pd = (i_sig * env * carrier + i_amb
            + flicker * (1 - np.cos(2 * np.pi * f_flick * t)) / 2
            + interferer * (1 + np.sign(np.sin(2 * np.pi * f_int * t))) / 2)
    b, a = signal.bilinear([1.0], [RF * CF, 1.0], fsim)
    v = VDC - RF * signal.lfilter(b, a, i_pd - i_pd[0], zi=None) - RF * i_pd[0]
    v = v[::os_factor] + noise_v * rng.standard_normal(len(v[::os_factor]))
    v = np.clip(v, 0.0, 5.0 - 1.5)                    # LM324 output swing
    lsb = vref / 2 ** adc_bits
    v = np.clip(np.round(v / lsb), 0, 2 ** adc_bits - 1) * lsb
    return t[::os_factor], v, env[::os_factor]


# ============================================================ LTspice import
def load_ltspice_txt(path, node, fs=FS):
    """Load 'Export data as text' output and resample onto a uniform grid.
    In the .asc use: .options plotwinsize=0   and   .tran 0 <stop> 0 200n"""
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                header = f.readline().strip().split("\t")
                data = np.loadtxt(f)
            break
        except (UnicodeError, ValueError):
            continue
    else:
        raise ValueError(f"could not parse {path}")
    cols = [h.strip() for h in header]
    if node not in cols:
        raise KeyError(f"{node!r} not in {cols}")
    tr, vr = data[:, 0], data[:, cols.index(node)]
    dt = np.diff(tr)
    if np.max(dt) > 1 / fs:
        print(f"warning: max LTspice step {np.max(dt)*1e9:.0f} ns > ADC "
              f"period {1e9/fs:.0f} ns; tighten the max timestep")
    t = np.arange(tr[0], tr[-1], 1 / fs)
    return t, np.interp(t, tr, vr)


# ================================================================== spectrum
def spectrum(x, fs=FS):
    f, pxx = signal.welch(x - np.mean(x), fs, nperseg=4096)
    fs_, ts_, sxx = signal.spectrogram(x - np.mean(x), fs, nperseg=512,
                                       noverlap=384)
    return (f, pxx), (fs_, ts_, sxx)


# ============================================================= demodulators
def fir_envelope(x, fs=FS, band=(34e3, 42e3), ntaps=301, lp=4e3, decim=10):
    bp = signal.firwin(ntaps, band, pass_zero=False, fs=fs)
    y = signal.lfilter(bp, 1.0, x - np.mean(x[:ntaps]))
    lpf = signal.firwin(129, lp, fs=fs)
    env = signal.lfilter(lpf, 1.0, np.abs(y)) * np.pi / 2
    delay = (ntaps - 1) / 2 + 64
    return (np.arange(len(x))[::decim] - delay) / fs, env[::decim], y


def goertzel_envelope(x, fs=FS, f0=FC, N=128, hop=32, window=True):
    """Hann-windowed Goertzel over sliding blocks.  Window kills DC and
    mains-flicker leakage, which is huge on the raw TIA signal."""
    frames = np.lib.stride_tricks.sliding_window_view(x, N)[::hop]
    w = np.hanning(N) if window else np.ones(N)
    frames = (frames - frames.mean(axis=1, keepdims=True)) * w
    coeff = 2 * np.cos(2 * np.pi * f0 / fs)
    s1 = np.zeros(len(frames)); s2 = np.zeros(len(frames))
    for n in range(N):
        s1, s2 = frames[:, n] + coeff * s1 - s2, s1
    p = s1 ** 2 + s2 ** 2 - coeff * s1 * s2
    amp = 2 * np.sqrt(np.maximum(p, 0)) / w.sum()
    t = (np.arange(len(frames)) * hop + N / 2) / fs
    return t, amp


def iq_envelope(x, fs=FS, f0=FC, lp=3e3, ntaps=255, decim=10):
    n = np.arange(len(x))
    bb = (x - np.mean(x)) * np.exp(-2j * np.pi * f0 * n / fs)
    lpf = signal.firwin(ntaps, lp, fs=fs)
    z = signal.lfilter(lpf, 1.0, bb)
    t = (n[::decim] - (ntaps - 1) / 2) / fs
    return t, 2 * np.abs(z[::decim])


def decode_digital_bursts(t, v, thr=1.65, hold=60e-6):
    """Comparator OUT toggles at 38 kHz during marks; turn activity into an
    envelope by stretching each transition over `hold` seconds."""
    d = (v > thr).astype(np.int8)
    act = np.zeros(len(d)); act[1:] = np.abs(np.diff(d))
    fs = 1 / np.median(np.diff(t))
    return maximum_filter1d(act, size=max(1, int(hold * fs)), origin=0)


# ========================================================== adaptive slicer
def adaptive_slicer(env, fe, warmup=1e-3, tau_attack=30e-6,
                    tau_floor=20e-3, tau_peak_decay=150e-3,
                    hi=0.5, lo=0.35, snr_min=4.0):
    """Floor = slow average of the envelope, updated only while the output
    is low (so marks don't drag it up).  Peak = fast attack, slow decay.
    Thresholds sit between them with hysteresis; the SNR gate keeps the
    slicer silent when only noise is present."""
    a_att = 1 - np.exp(-1 / (tau_attack * fe))
    a_flr = 1 - np.exp(-1 / (tau_floor * fe))
    a_dec = 1 - np.exp(-1 / (tau_peak_decay * fe))
    nw = int(warmup * fe)                           # skip filter start-up
    floor = float(np.median(env[nw:nw + max(8, int(2e-3 * fe))])) + 1e-12
    peak = floor
    out = np.zeros(len(env), dtype=bool)
    thr = np.full(len(env), np.nan)
    state = False
    for i in range(nw, len(env)):
        e = env[i]
        if not state:
            floor += a_flr * (e - floor) if e < 2 * floor else 0.0
        peak += (a_att if e > peak else a_dec) * (e - peak)
        th = floor + (lo if state else hi) * (peak - floor)
        state = (peak > snr_min * floor) and (e > th)
        out[i], thr[i] = state, th
    return out, thr


# ============================================================ NEC decoding
def run_lengths(bits, fe, min_pulse=150e-6):
    """(level, duration) runs with glitches shorter than min_pulse merged."""
    idx = np.flatnonzero(np.diff(bits.astype(np.int8))) + 1
    edges = np.concatenate(([0], idx, [len(bits)]))
    runs = [[bool(bits[a]), (b - a) / fe, a / fe]
            for a, b in zip(edges[:-1], edges[1:])]
    merged = []
    for r in runs:
        if merged and (r[1] < min_pulse or merged[-1][0] == r[0]):
            merged[-1][1] += r[1]
        else:
            merged.append(r)
    return merged


def _near(x, target, tol=0.3):
    return abs(x - target) <= tol * target


def nec_decode(runs, t_offset=0.0):
    out, i = [], 0
    while i < len(runs) - 1:
        lvl, d, t0 = runs[i]
        if not (lvl and _near(d, 16 * T_NEC, 0.25)):
            i += 1
            continue
        sp = runs[i + 1][1]
        if _near(sp, 4 * T_NEC, 0.25):
            out.append(dict(t=t0 + t_offset, repeat=True))
            i += 2
            continue
        if not _near(sp, 8 * T_NEC, 0.25) or i + 2 + 64 > len(runs):
            i += 1
            continue
        bits, ok = 0, True
        for k in range(32):
            m, s = runs[i + 2 + 2 * k][1], runs[i + 3 + 2 * k][1]
            if not _near(m, T_NEC, 0.45):
                ok = False; break
            if _near(s, T_NEC, 0.45):
                pass
            elif _near(s, 3 * T_NEC, 0.3):
                bits |= 1 << k
            else:
                ok = False; break
        if ok:
            b = [(bits >> (8 * j)) & 0xFF for j in range(4)]
            if b[2] ^ b[3] == 0xFF:
                std = b[0] ^ b[1] == 0xFF
                out.append(dict(t=t0 + t_offset, repeat=False,
                                addr=b[0] if std else b[0] | b[1] << 8,
                                cmd=b[2], extended=not std))
            i += 66
        else:
            i += 1
    return out


def decode(t_env, env, name=""):
    fe = 1 / np.median(np.diff(t_env))
    bits, thr = adaptive_slicer(env, fe)
    runs = run_lengths(bits, fe)
    return nec_decode(runs, t_offset=t_env[0]), bits, thr


def run_all(t, x):
    res = {}
    for name, fn in [("FIR", lambda: fir_envelope(x)[:2]),
                     ("Goertzel", lambda: goertzel_envelope(x)),
                     ("I/Q", lambda: iq_envelope(x))]:
        te, e = fn()
        frames, bits, thr = decode(te, e)
        res[name] = dict(t=te, env=e, bits=bits, thr=thr, frames=frames)
    return res


# ==================================================================== plots
def plot_all(t, x, truth, res, outdir, tag="synthetic"):
    (f, pxx), (fsg, tsg, sxx) = spectrum(x)
    fig, ax = plt.subplots(5, 1, figsize=(12, 14))
    ax[0].plot(t * 1e3, x, lw=0.4)
    ax[0].set_ylabel("ADC in [V]"); ax[0].set_title(f"TIA tap ({tag})")
    ax[1].semilogy(f / 1e3, pxx); ax[1].axvline(FC / 1e3, c="r", ls="--")
    ax[1].set_xlabel("kHz"); ax[1].set_ylabel("PSD [V²/Hz]")
    ax[2].pcolormesh(tsg * 1e3, fsg / 1e3, 10 * np.log10(sxx + 1e-20),
                     shading="auto", cmap="magma")
    ax[2].set_ylim(0, 120); ax[2].set_ylabel("kHz")
    for name, r in res.items():
        ax[3].plot(r["t"] * 1e3, r["env"] * 1e3, lw=0.8, label=name)
    ax[3].set_ylabel("envelope [mV]"); ax[3].legend(loc="upper right")
    for k, (name, r) in enumerate(res.items()):
        ax[4].step(r["t"] * 1e3, r["bits"] * 0.8 + 1.1 * (k + 1), lw=0.8,
                   label=name)
    if truth is not None:
        ax[4].step(t * 1e3, truth * 0.8, c="k", lw=0.8, label="truth")
    ax[4].set_xlabel("time [ms]"); ax[4].set_yticks([])
    ax[4].legend(loc="upper right")
    for a in (ax[0], ax[3], ax[4]):
        a.set_xlim(t[0] * 1e3, t[-1] * 1e3)
    fig.tight_layout()
    p = os.path.join(outdir, f"pipeline_{tag}.png")
    fig.savefig(p, dpi=110); plt.close(fig)

    # zoom on the first 3 ms of the leader
    fig, ax = plt.subplots(figsize=(12, 4))
    t0 = next(i for i, v in enumerate(truth) if v) / FS if truth is not None \
        else t[0]
    m = (t > t0 - 0.3e-3) & (t < t0 + 1.5e-3)
    ax.plot(t[m] * 1e3, (x[m] - x[m].mean()) * 1e3, lw=0.7, label="ADC (AC)")
    _, _, ybp = fir_envelope(x)
    ax.plot(t[m] * 1e3, ybp[m] * 1e3, lw=0.7, label="FIR bandpass")
    ax.set_xlabel("time [ms]"); ax.set_ylabel("mV"); ax.legend()
    ax.set_title("Leader onset: carrier after TIA, before/after FIR")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, f"zoom_{tag}.png"), dpi=110)
    plt.close(fig)
    return p


# ==================================================================== sweep
def sweep(outdir, levels=(5e-9, 10e-9, 20e-9, 40e-9, 80e-9, 160e-9),
          trials=6):
    rng = np.random.default_rng(1)
    names = ["FIR", "Goertzel", "I/Q"]
    ok = {n: [] for n in names}
    for i_sig in levels:
        hits = {n: 0 for n in names}
        for k in range(trials):
            a, c = int(rng.integers(256)), int(rng.integers(256))
            seg = nec_timeline(a, c)
            t, x, _ = synth_tia(seg, i_sig=i_sig, interferer=0.1e-6,
                                seed=k + 100)
            for n, r in run_all(t, x).items():
                fr = [f for f in r["frames"] if not f["repeat"]]
                hits[n] += len(fr) == 1 and fr[0]["addr"] == a \
                    and fr[0]["cmd"] == c
        for n in names:
            ok[n].append(hits[n] / trials)
        print(f"i_sig={i_sig*1e9:6.1f} nA  " +
              "  ".join(f"{n}:{ok[n][-1]*100:5.0f}%" for n in names))
    fig, ax = plt.subplots(figsize=(7, 4))
    for n in names:
        ax.semilogx(np.array(levels) * 1e9, np.array(ok[n]) * 100, "o-",
                    label=n)
    ax.set_xlabel("photocurrent pk [nA]"); ax.set_ylabel("frames decoded [%]")
    ax.set_title("Sensitivity (3 mV rms noise, 45 kHz interferer, 12-bit ADC)")
    ax.grid(True, which="both", alpha=0.3); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(outdir, "sweep.png"), dpi=110)
    plt.close(fig)


# ===================================================================== main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ltspice"); ap.add_argument("--node", default="V(tia)")
    ap.add_argument("--out-node")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x04)
    ap.add_argument("--cmd", type=lambda s: int(s, 0), default=0x08)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--isig", type=float, default=0.1e-6)
    ap.add_argument("--write-pwl"); ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--outdir", default="out")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    seg = nec_timeline(a.addr, a.cmd, repeats=a.repeats)

    if a.write_pwl:
        n = write_pwl(a.write_pwl, seg, i_pk=a.isig)
        print(f"wrote {n} PWL points to {a.write_pwl}")
        return
    if a.sweep:
        sweep(a.outdir); return

    if a.ltspice:
        t, x = load_ltspice_txt(a.ltspice, a.node)
        truth, tag = None, "ltspice"
    else:
        t, x, truth = synth_tia(seg, i_sig=a.isig, interferer=0.1e-6)
        tag = "synthetic"
        print(f"sent: addr=0x{a.addr:02X} cmd=0x{a.cmd:02X} "
              f"+ {a.repeats} repeat(s)")
    res = run_all(t, x)
    for n, r in res.items():
        print(f"{n:9s}: " + "; ".join(
            "REPEAT" if f["repeat"] else
            f"addr=0x{f['addr']:02X} cmd=0x{f['cmd']:02X}" for f in r["frames"])
              or f"{n:9s}: nothing decoded")
    if a.ltspice and a.out_node:
        t2, v2 = load_ltspice_txt(a.ltspice, a.out_node)
        env = decode_digital_bursts(t2, v2)
        fr, _, _ = decode(t2[::10], env[::10])
        print("comparator:", fr)
    print("plot:", plot_all(t, x, truth, res, a.outdir, tag))


if __name__ == "__main__":
    main()
