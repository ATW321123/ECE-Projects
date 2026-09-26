"""Decode rate vs signal level: float demodulators vs the fixed-point C receiver.

  python sweep_fixed.py            # writes decode_rate.png and out/decode_rate.json

Each trial: random address/command, synth_tia() front end (3 mV rms noise,
2 uA ambient + 120 Hz flicker, 12-bit ADC at 500 kSPS), with and without a
100 nA 45 kHz interferer.  A trial passes when exactly one frame is decoded
and its address and command are right (same rule as ir_dsp.sweep()).
"""
import json, os
import numpy as np
import matplotlib.pyplot as plt
import ir_dsp as d
import test_goertzel_fixed as g
import test_ir_rx as rx

LEVELS = [5, 7.5, 10, 15, 20, 30, 40, 60, 80]        # nA peak photocurrent
TRIALS = 10
METHODS = ["Goertzel fixed-point (C)", "Goertzel float", "I/Q fixed-point (C)",
           "I/Q float (255-tap ref)", "FIR float"]
STYLE = {  # categorical slots 1-5 of the dataviz default palette, fixed order
    # C versions drawn solid and large; float references as small dashed overlays
    "Goertzel fixed-point (C)": dict(color="#2a78d6", marker="o", ls="-", lw=2.5, ms=10, zorder=4),
    "Goertzel float":           dict(color="#eb6834", marker="s", ls="--", lw=2, ms=5, zorder=5),
    "I/Q fixed-point (C)":      dict(color="#1baf7a", marker="^", ls="-", lw=2.5, ms=10, zorder=6),
    "I/Q float (255-tap ref)":  dict(color="#eda100", marker="D", ls="--", lw=2, ms=5, zorder=7),
    "FIR float":                dict(color="#e87ba4", marker="v", ls=":", lw=2, ms=7, zorder=2),
}
INK, INK2, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"


def hit(frames, a, c):
    fr = [f for f in frames if not f["repeat"]]
    return len(fr) == 1 and fr[0]["addr"] == a and fr[0]["cmd"] == c


def run_sweep():
    rng = np.random.default_rng(7)
    res = {}
    for interferer in (0.0, 100e-9):
        key = "45k" if interferer else "clean"
        res[key] = {m: [] for m in METHODS}
        for lvl in LEVELS:
            hits = {m: 0 for m in METHODS}
            for k in range(TRIALS):
                a, c = int(rng.integers(256)), int(rng.integers(256))
                seg = d.nec_timeline(a, c)
                _, v, _ = d.synth_tia(seg, i_sig=lvl * 1e-9, interferer=interferer, seed=1000 + k)
                codes = g.to_codes(v)
                x = codes * g.LSB
                fl = d.run_all(np.arange(len(x)) / d.FS, x)
                hits["Goertzel fixed-point (C)"] += hit(rx.run_c(codes)[0], a, c)
                hits["Goertzel float"] += hit(fl["Goertzel"]["frames"], a, c)
                hits["I/Q fixed-point (C)"] += hit(rx.run_c(codes, "iq")[0], a, c)
                hits["I/Q float (255-tap ref)"] += hit(fl["I/Q"]["frames"], a, c)
                hits["FIR float"] += hit(fl["FIR"]["frames"], a, c)
            for m in METHODS:
                res[key][m].append(100 * hits[m] / TRIALS)
            print(f"{key:5s} {lvl:5.1f} nA  " + "  ".join(f"{m.split()[0]}{'(C)' if '(C)' in m else ''}:"
                                                         f"{res[key][m][-1]:4.0f}%" for m in METHODS), flush=True)
    return res


def plot(res, path):
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK,
                         "xtick.color": INK2, "ytick.color": INK2, "text.color": INK})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True, facecolor=SURFACE)
    titles = {"clean": "No interferer", "45k": "With a 100 nA interferer at 45 kHz"}
    for ax, key in zip(axes, ("clean", "45k")):
        ax.set_facecolor(SURFACE)
        for m in METHODS:
            ax.plot(LEVELS, res[key][m], label=m, **STYLE[m])
        ax.set_xscale("log")
        ax.set_xticks(LEVELS)
        ax.set_xticklabels([f"{v:g}" for v in LEVELS])
        ax.minorticks_off()
        ax.set_ylim(-5, 105)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.grid(True, color=GRID, lw=1)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.set_title(titles[key], loc="left", fontsize=11, color=INK)
        ax.set_xlabel("Remote signal, peak photocurrent (nA)")
    axes[0].set_ylabel("Frames decoded correctly (%)")
    same = lambda m1, m2: all(res[k][m1] == res[k][m2] for k in res)
    notes = []
    if same(METHODS[0], METHODS[1]):
        notes.append("Goertzel: C = float at every point")
    hidden = [m for m in METHODS[2:] if res["clean"][m] == res["clean"][METHODS[0]]]
    if hidden:
        notes.append("left panel: " + ", ".join(hidden) +
                     ("\nmatches" if len(hidden) == 1 else "\nmatch") + " Goertzel (drawn underneath)")
    if notes:
        axes[0].text(0.50, 0.08, "\n".join(notes), transform=axes[0].transAxes,
                     fontsize=8.5, color=INK2)
    axes[1].legend(loc="lower right", frameon=False, fontsize=9)
    fig.suptitle(f"NEC decode rate vs signal level ({TRIALS} random frames per point, "
                 "12-bit ADC at 500 kSPS)", x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)


if __name__ == "__main__":
    g.build()
    res = run_sweep()
    os.makedirs(os.path.join(g.HERE, "out"), exist_ok=True)
    with open(os.path.join(g.HERE, "out", "decode_rate.json"), "w") as f:
        json.dump(dict(levels_nA=LEVELS, trials=TRIALS, results=res), f, indent=1)
    plot(res, os.path.join(g.HERE, "decode_rate.png"))
    print("wrote decode_rate.png")
