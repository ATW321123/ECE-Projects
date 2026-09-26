"""End-to-end test of the C receiver (firmware/ir_rx.c): ADC codes in, NEC
frames out, compared with the Python pipeline in ir_dsp.py.

  python test_ir_rx.py                      # LTspice exports + synthetic cases
  python test_ir_rx.py ../sim/exports/Draft4_gain_20nA.txt

For each input:
  C frames      decoded by firmware/ir_run.exe (fixed-point, streaming)
  Py frames     Goertzel float envelope -> adaptive_slicer -> nec_decode
  slicer agree  C slicer bits vs Python adaptive_slicer on the SAME fixed-point
                envelope, i.e. the cost of the integer slicer alone
"""
import os, subprocess, sys, tempfile
import numpy as np
import ir_dsp as d
import test_goertzel_fixed as g


def run_c(codes, detector="goertzel"):
    """-> (frames, slicer bits) from the C receiver ('goertzel' or 'iq' detector)."""
    with tempfile.TemporaryDirectory() as td:
        fi, fb = os.path.join(td, "in.u16"), os.path.join(td, "bits.u8")
        codes.astype("<u2").tofile(fi)
        flag = ["-iq"] if detector == "iq" else []
        out = subprocess.run([g.IR_EXE] + flag + [fi, fb], check=True, capture_output=True,
                             text=True).stdout
        bits = np.fromfile(fb, dtype=np.uint8).astype(bool)
    frames = []
    for line in out.split("\n"):
        p = line.split()
        if not p: continue
        if p[0] == "R": frames.append(dict(repeat=True, t=int(p[1]) / g.FE))
        else: frames.append(dict(repeat=False, t=int(p[1]) / g.FE, addr=int(p[2]),
                                 cmd=int(p[3]), extended=bool(int(p[4]))))
    return frames, bits


def same(a, b):
    key = lambda f: "R" if f["repeat"] else (f["addr"], f["cmd"], f["extended"])
    return [key(f) for f in a] == [key(f) for f in b]


def check(name, codes, expect=g.EXPECT):
    fr_c, bits_c = run_c(codes)
    t_ref, env_f = d.goertzel_envelope(codes * g.LSB)
    fr_py = d.decode(t_ref, env_f)[0]
    env_fix = g.run_c(codes) / 8.0                      # same envelope the C slicer saw
    bits_py = d.adaptive_slicer(env_fix, g.FE)[0]
    n = min(len(bits_py), len(bits_c))
    agree = np.mean(bits_py[:n] == bits_c[:n]) * 100
    ok_c = g.ok(fr_c) if expect == g.EXPECT else [f for f in fr_c] == expect
    match = same(fr_c, fr_py)
    print(f"{name:34s} C: {g.fmt(fr_c):22s} Py: {g.fmt(fr_py):22s} "
          f"{'same' if match else 'DIFF'} | slicer agree {agree:6.2f}% "
          f"({np.sum(bits_py[:n] != bits_c[:n])} of {n} bits)")
    return match, ok_c


def main():
    g.build()
    files = sys.argv[1:] or [os.path.join(g.HERE, "..", "sim", "exports", f) for f in
                             ("Draft3_nec.txt", "Draft4_gain.txt", "Draft4_gain_20nA.txt")]
    good = True
    for f in files:
        if not os.path.exists(f):
            print(f"skip {f} (missing)"); continue
        _, v = d.load_ltspice_txt(f, "V(tia)")
        match, _ = check("LTspice " + os.path.basename(f), g.to_codes(v))
        good &= match
    if len(sys.argv) == 1:
        seg = d.nec_timeline(0x04, 0x08, repeats=1)
        for isig in (10e-9, 20e-9, 30e-9, 40e-9, 80e-9):
            _, v, _ = d.synth_tia(seg, i_sig=isig, interferer=0.1e-6)
            match, _ = check(f"synthetic {isig*1e9:.0f} nA + 100 nA @45k", g.to_codes(v))
            good &= match
        # extended address, other command, 3 repeats
        seg = d.nec_timeline(0x1234, 0xA5, repeats=3, extended=True)
        _, v, _ = d.synth_tia(seg, i_sig=80e-9)
        fr, _ = run_c(g.to_codes(v))
        want = [(0x1234, 0xA5, True), "R", "R", "R"]
        got = ["R" if f["repeat"] else (f["addr"], f["cmd"], f["extended"]) for f in fr]
        print(f"{'synthetic extended 0x1234/0xA5 x3':34s} C: {got}  {'OK' if got == want else 'BAD'}")
        good &= got == want
        # silence + interference only: must decode nothing
        seg = [(0, 0.14)]
        _, v, _ = d.synth_tia(seg, i_sig=0.0, interferer=0.1e-6)
        fr, bits = run_c(g.to_codes(v))
        print(f"{'no remote, 100 nA @45k only':34s} C frames: {len(fr)}, slicer high "
              f"{bits.mean()*100:.2f}% of the time  {'OK' if not fr else 'BAD'}")
        good &= not fr
    print("\nPASS" if good else "\nFAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
