"""One-figure demo: an LTspice NEC waveform decoded by the fixed-point C receiver.

  python demo.py                                  # ../sim/exports/Draft4_gain_20nA.txt -> demo.png
  python demo.py ../sim/exports/Draft4_gain.txt --out demo_100nA.png
  python demo.py ../sim/exports/Draft5_servo_allnoises.txt --detector iq --out demo_iq_allnoises.png

Panels (shared time axis), all from firmware/ir_run.exe except the ADC model:
  1. TIA output as 12-bit ADC codes (500 kSPS), with a zoom on the 38 kHz carrier
  2. Goertzel carrier amplitude and the slicer's adaptive threshold
  3. Slicer output with the decoded NEC bytes
"""
import argparse, os, subprocess, tempfile
import numpy as np
import matplotlib.pyplot as plt
import ir_dsp as d
import test_goertzel_fixed as g

INK, INK2, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"
BLUE, ORANGE = "#2a78d6", "#eb6834"          # dataviz default slots 1 and 2


def run_receiver(codes, detector="goertzel"):
    """-> frames, slicer bits, threshold (codes), envelope (codes), all from the C build."""
    with tempfile.TemporaryDirectory() as td:
        fi, fb, ft = (os.path.join(td, n) for n in ("in.u16", "bits.u8", "thr.f32"))
        codes.astype("<u2").tofile(fi)
        flag = ["-iq"] if detector == "iq" else []
        out = subprocess.run([g.IR_EXE] + flag + [fi, fb, ft], check=True, capture_output=True,
                             text=True).stdout
        bits = np.fromfile(fb, dtype=np.uint8).astype(bool)
        thr = np.fromfile(ft, dtype="<f4").astype(float)
    frames = []
    for line in out.split("\n"):
        p = line.split()
        if p and p[0] == "R": frames.append(dict(repeat=True, k=int(p[1])))
        elif p: frames.append(dict(repeat=False, k=int(p[1]), addr=int(p[2]),
                                   cmd=int(p[3]), extended=bool(int(p[4]))))
    env = g.run_c(codes, detector) / 8.0
    return frames, bits, thr, env


def byte_spans(bits, t_env, k_leader):
    """(start, end) time of each of the 4 bytes after the leader at envelope index k_leader."""
    runs = d.run_lengths(bits, g.FE)
    starts = [i for i, r in enumerate(runs) if r[0] and abs(r[2] * g.FE - k_leader) < 1.5]
    if not starts: return []
    marks = [r for r in runs[starts[0] + 2:] if r[0]][:33]      # 32 data marks + stop
    if len(marks) < 33: return []
    t0 = t_env[0]
    edge = lambda r: t0 + r[2]
    return [(edge(marks[8 * b]), edge(marks[8 * b + 8])) for b in range(4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default=os.path.join(g.HERE, "..", "sim", "exports", "Draft4_gain_20nA.txt"))
    ap.add_argument("--node", default="V(tia)")
    ap.add_argument("--out", default=os.path.join(g.HERE, "demo.png"))
    ap.add_argument("--detector", choices=["goertzel", "iq"], default="goertzel")
    a = ap.parse_args()

    g.build()
    t, v = d.load_ltspice_txt(a.file, a.node)
    codes = g.to_codes(v)
    frames, bits, thr, env = run_receiver(codes, a.detector)
    if a.detector == "iq":   # output k at sample 32k+31, minus CIC + FIR group delay
        delay = g.IQ_ORDER * (g.IQ_R1 - 1) / 2 + (g.IQ_NTAPS - 1) / 2 * g.IQ_R1
        t_env = (np.arange(len(env)) * g.HOP + g.HOP - 1 - delay) / d.FS
    else:                    # Goertzel block centre
        t_env = (np.arange(len(env)) * g.HOP + g.N / 2) / d.FS
    det = "I/Q" if a.detector == "iq" else "Goertzel"
    n = min(len(t_env), len(bits))
    t_env, env, bits, thr = t_env[:n], env[:n], bits[:n], thr[:n]
    ms = lambda x: np.asarray(x) * 1e3

    for f in frames:
        print("REPEAT" if f["repeat"] else
              f"frame addr=0x{f['addr']:02X} cmd=0x{f['cmd']:02X}", f"at {t_env[f['k']]*1e3:.2f} ms")

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK,
                         "xtick.color": INK2, "ytick.color": INK2, "text.color": INK})
    fig, ax = plt.subplots(3, 1, figsize=(12, 8.2), sharex=True, facecolor=SURFACE,
                           gridspec_kw=dict(height_ratios=[1.25, 1, 0.8]))
    for x in ax:
        x.set_facecolor(SURFACE)
        x.grid(True, color=GRID, lw=1); x.set_axisbelow(True)
        for s in ("top", "right"): x.spines[s].set_visible(False)

    # 1. ADC codes
    lo, hi = np.percentile(codes, [0.05, 99.95])
    span = max(hi - lo, 4)
    ax[0].plot(ms(t), codes, color=BLUE, lw=0.6)
    ax[0].set_ylim(lo - 1.25 * span, hi + 0.15 * span)     # trace on top, inset below
    ax[0].yaxis.set_major_locator(plt.MaxNLocator(6, integer=True))
    ax[0].set_ylabel("ADC code")
    where = {"V(tia)": "TIA output", "V(bpf)": "Band-pass output"}.get(a.node, a.node)
    ax[0].set_title(f"1  {where}, sampled by a 12-bit ADC at 500 kSPS", loc="left", fontsize=11)

    lead = [f for f in frames if not f["repeat"]]
    if lead:
        tz = t_env[lead[0]["k"]] + 2e-3                          # inside the 9 ms leader
        z = (t >= tz) & (t < tz + 0.25e-3)
        # inset sits below the trace, in the gap between the frame and the repeat
        ins = ax[0].inset_axes([0.53, 0.15, 0.21, 0.27])
        ins.yaxis.set_major_locator(plt.MaxNLocator(4, integer=True))
        ins.set_facecolor(SURFACE)
        ins.step((t[z] - tz) * 1e6, codes[z], where="mid", color=BLUE, lw=1.2)
        ins.set_xlabel("µs", fontsize=8, labelpad=1)
        ins.tick_params(labelsize=8)
        half = (codes[z].max() - codes[z].min()) / 2
        # the zoom shows everything the ADC sees (carrier + any interference), so say that
        ins.set_title(f"zoom inside the leader: signal swings ±{half:.1f} codes",
                      fontsize=8.5, color=INK2)
        for s in ("top", "right"): ins.spines[s].set_visible(False)
        ax[0].annotate("", xy=(ms(tz), lo), xycoords="data",
                       xytext=(0.53, 0.36), textcoords="axes fraction",
                       arrowprops=dict(arrowstyle="->", color=INK2, lw=0.9))

    # 2. envelope + threshold
    ax[1].plot(ms(t_env), env, color=BLUE, lw=1.4, label=f"38 kHz amplitude (C {det})")
    ax[1].plot(ms(t_env), thr, color=ORANGE, lw=1.4, ls="--", label="adaptive threshold (C slicer)")
    ax[1].set_ylabel("amplitude (codes)")
    ax[1].legend(loc="upper left", bbox_to_anchor=(0.53, 1.0), frameon=False, fontsize=9)
    ax[1].set_title("2  Carrier detector output and the slicer's threshold", loc="left", fontsize=11)

    # 3. bits + decoded bytes
    ax[2].step(ms(t_env), bits.astype(int), where="mid", color=BLUE, lw=1.4)
    ax[2].set_ylim(-0.3, 2.3)
    ax[2].set_yticks([0, 1]); ax[2].set_yticklabels(["off", "on"])
    ax[2].set_xlabel("time (ms)")
    ax[2].set_title("3  Slicer output, decoded by the NEC state machine", loc="left", fontsize=11)
    for f in frames:
        t0 = t_env[f["k"]]
        if f["repeat"]:
            ax[2].annotate("REPEAT", (ms(t0), 1.15), fontsize=9.5, fontweight="bold")
            continue
        ax[2].annotate("leader", (ms(t0), 1.15), fontsize=9, color=INK2)
        b1 = f["addr"] >> 8 if f["extended"] else (~f["addr"]) & 0xFF
        labels = [f"addr 0x{f['addr'] & 0xFF:02X}",
                  f"addr_hi 0x{b1:02X}" if f["extended"] else f"~addr 0x{b1:02X}",
                  f"cmd 0x{f['cmd']:02X}", f"~cmd 0x{(~f['cmd']) & 0xFF:02X}"]
        for (s, e), lab in zip(byte_spans(bits, t_env, f["k"]), labels):
            ax[2].annotate("", (ms(s), 1.35), (ms(e), 1.35),
                           arrowprops=dict(arrowstyle="|-|", color=INK2, lw=0.8, mutation_scale=3))
            ax[2].text(ms((s + e) / 2), 1.5, lab, ha="center", fontsize=9)
        ax[2].text(ms(t0), 2.0, f"decoded: address 0x{f['addr']:02X}, command 0x{f['cmd']:02X}",
                   fontsize=10.5, fontweight="bold")

    if not frames:
        ax[2].text(0.5, 0.6, "nothing decoded", transform=ax[2].transAxes, ha="center",
                   fontsize=11, fontweight="bold")
    verb = "decoded" if frames else "NOT decoded"
    fig.suptitle(f"NEC remote frame {verb} by fixed-point C firmware, {det} detector "
                 f"({os.path.basename(a.file)}, LTspice)", x=0.01, ha="left", fontsize=13)
    fig.tight_layout()
    fig.savefig(a.out, dpi=130, facecolor=SURFACE)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
