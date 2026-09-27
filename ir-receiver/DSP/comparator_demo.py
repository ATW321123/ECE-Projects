"""Why the analog comparator path fails: its output in silence vs during the NEC leader.

  python comparator_demo.py                        # ../sim/exports/Draft5_allnoises_fixed.txt
  python comparator_demo.py <export> --out x.png

Top: V(out) over the first 20 ms, with the 9 ms leader burst shaded.
Bottom: 300 us zooms of silence and of the leader. If the two look the same,
the comparator is switching on the interferer, not on the remote.
"""
import argparse, os
import numpy as np
import matplotlib.pyplot as plt
import ir_dsp as d

INK, INK2, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"
BLUE, ORANGE = "#2a78d6", "#eb6834"          # dataviz default slots 1 and 2
LEAD0, LEAD1 = 5e-3, 14e-3                   # the PWL stimulus starts the leader at 5 ms


def edges_per_ms(t, hi, a, b):
    m = (t >= a) & (t < b)
    return np.sum(np.abs(np.diff(hi[m].astype(int)))) / ((b - a) * 1e3)


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(True, color=GRID, lw=0.8); ax.set_axisbelow(True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "sim", "exports", "Draft5_allnoises_fixed.txt"))
    ap.add_argument("--node", default="V(out)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "comparator_allnoises.png"))
    a = ap.parse_args()

    t, v = d.load_ltspice_txt(a.file, a.node)
    hi = v > 1.65
    quiet, burst = edges_per_ms(t, hi, 0, LEAD0), edges_per_ms(t, hi, LEAD0, LEAD1)
    env = d.decode_digital_bursts(t, v)            # same path as ir_dsp.py --out-node
    frames, _, _ = d.decode(t[::10], env[::10])

    fig = plt.figure(figsize=(10, 6.2), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.1], hspace=0.55, wspace=0.12)
    top = fig.add_subplot(gs[0, :]); style(top)
    m = t < 20e-3
    top.axvspan(LEAD0 * 1e3, LEAD1 * 1e3, color=ORANGE, alpha=0.12, lw=0)
    top.text((LEAD0 + LEAD1) / 2 * 1e3, 3.62, "9 ms NEC leader should be here",
             ha="center", va="bottom", fontsize=9, color=INK2)
    top.plot(t[m] * 1e3, v[m], color=BLUE, lw=0.6)
    top.set_xlim(0, 20); top.set_ylim(-0.6, 4.1)
    top.set_xlabel("time (ms)", color=INK2, fontsize=9); top.set_ylabel("V(out) (V)", color=INK2, fontsize=9)
    top.set_title("Comparator output: toggles continuously, with or without the remote",
                  loc="left", fontsize=11, color=INK)

    for col, (t0, lab, rate) in enumerate([(2.0e-3, "silence (2.0 ms)", quiet),
                                           (8.0e-3, "during the leader (8.0 ms)", burst)]):
        ax = fig.add_subplot(gs[1, col]); style(ax)
        z = (t >= t0) & (t < t0 + 300e-6)
        ax.plot((t[z] - t0) * 1e6, v[z], color=BLUE, lw=1.4)
        ax.set_xlim(0, 300); ax.set_ylim(-0.6, 3.9)
        ax.set_xlabel("µs", color=INK2, fontsize=9)
        if col == 0: ax.set_ylabel("V(out) (V)", color=INK2, fontsize=9)
        else: ax.tick_params(labelleft=False)
        ax.set_title(f"{lab}: {rate:.0f} edges/ms", loc="left", fontsize=10, color=INK)

    res = "no frame decoded" if not frames else "decoded: " + "; ".join(
        "REPEAT" if f["repeat"] else f"0x{f['addr']:02X}/0x{f['cmd']:02X}" for f in frames)
    fig.text(0.125, 0.015, f"{os.path.basename(a.file)}  ·  {res}  ·  "
             f"~{quiet / 2:.0f} kHz switching = the 45 kHz interferer, not the 38 kHz remote",
             fontsize=8.5, color=INK2)
    fig.savefig(a.out, dpi=130, facecolor=SURFACE)
    print(f"quiet {quiet:.1f} edges/ms, burst {burst:.1f} edges/ms; {res}")
    print("wrote", os.path.basename(a.out))


if __name__ == "__main__":
    main()
