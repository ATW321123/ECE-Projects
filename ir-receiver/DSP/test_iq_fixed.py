"""Test the fixed-point C I/Q detector (firmware/iq.c) against
   1. a bit-exact integer model (any mismatch = firmware bug),
   2. a float model of the same structure (DC blocker, mixer, CIC, FIR),
   3. end-to-end NEC decoding through the C receiver (ir_run -iq).

  python test_iq_fixed.py                         # LTspice exports + synthetic cases
  python test_iq_fixed.py ../sim/exports/Draft5_servo_allnoises.txt
"""
import math, os, sys
import numpy as np
from scipy import signal
import ir_dsp as d
import test_goertzel_fixed as g
import test_ir_rx as rx

R1, R2, ORD = g.IQ_R1, g.IQ_R2, g.IQ_ORDER
CIC_KERNEL = np.ones(1, np.int64)
for _ in range(ORD):
    CIC_KERNEL = np.convolve(CIC_KERNEL, np.ones(R1, np.int64))     # CIC == this FIR
LO_IDX = lambda n: np.arange(n) % g.IQ_LO_N


def sat16(v):
    return np.clip(v, -32767, 32767)


def model(codes):
    """Bit-exact integer model of iq_push(), one value per envelope sample."""
    c = [int(v) for v in codes]
    dc, xh = c[0] << 8, []
    for v in c:                                                      # DC blocker
        xh.append(max(-32767, min(32767, (v << 4) - g.rshift_r(dc, 4))))
        dc += g.rshift_r((v << 8) - dc, g.IQ_DC_SHIFT)
    xh = np.array(xh, np.int64)
    lo = LO_IDX(len(xh))
    out = []
    for tab in (g.IQ_COS_Q14, g.IQ_SIN_Q14):
        m = g.rshift_r(xh * tab[lo], 14)
        y = np.convolve(m, CIC_KERNEL)[:len(m)][R1 - 1::R1]         # CIC, decimated
        cc = sat16(g.rshift_r(y, g.IQ_CIC_SHIFT))
        acc = np.convolve(cc, g.IQ_FIR_Q15)[:len(cc)][R2 - 1::R2]    # FIR, decimated
        out.append(g.rshift_r(acc, 15))
    zi, zq = out
    return np.array([math.isqrt(int(a * a + b * b)) for a, b in zip(zi, zq)], np.int64)


def float_model(codes):
    """Same structure in floating point (unquantized tables, exact cos/sin)."""
    x = codes.astype(float)
    a = 2.0 ** -g.IQ_DC_SHIFT
    y = signal.lfilter([a], [1, -(1 - a)], x, zi=[(1 - a) * x[0]])[0]  # dc after each sample
    dc_before = np.concatenate(([x[0]], y[:-1]))
    xh = x - dc_before
    n = np.arange(len(x))
    bb = xh * np.exp(-2j * np.pi * d.FC * n / d.FS)
    cic = np.convolve(bb, CIC_KERNEL / R1 ** ORD)[:len(bb)][R1 - 1::R1]
    h = signal.firwin(g.IQ_NTAPS, g.IQ_CUTOFF, window=("kaiser", g.IQ_BETA), fs=d.FS / R1)
    z = signal.lfilter(h, 1, cic)[R2 - 1::R2]
    return 2 * np.abs(z)                                             # amplitude, ADC codes


def decode_py(env_codes):
    bits = d.adaptive_slicer(env_codes, g.FE)[0]
    return d.nec_decode(d.run_lengths(bits, g.FE))


def check(name, codes):
    env_c = g.run_c(codes, "iq")
    env_m = model(codes)
    exact = len(env_c) == len(env_m) and np.array_equal(env_c, env_m)
    env_f = float_model(codes)
    n = min(len(env_f), len(env_c))
    err = np.max(np.abs(env_c[:n] / 8.0 - env_f[:n]))
    fr_c, _ = rx.run_c(codes, "iq")
    fr_f = decode_py(env_f)
    fr_gz, _ = rx.run_c(codes, "goertzel")
    same = rx.same(fr_c, fr_f)
    print(f"{name:36s} bit-exact {'yes' if exact else 'NO '} | max err {err:5.2f} codes "
          f"(peak {env_f.max():5.1f}) | I/Q C: {g.fmt(fr_c):20s} float: {'same' if same else 'DIFF'}"
          f" | Goertzel C: {g.fmt(fr_gz)}")
    return exact and same, g.ok(fr_c)


def main():
    g.build()
    files = sys.argv[1:] or [os.path.join(g.HERE, "..", "sim", "exports", f) for f in
                             ("Draft3_nec.txt", "Draft4_gain.txt", "Draft4_gain_20nA.txt",
                              "Draft5_servo_allnoises.txt")]
    good = True
    for f in files:
        if not os.path.exists(f):
            print(f"skip {f} (missing)"); continue
        _, v = d.load_ltspice_txt(f, "V(tia)")
        ok_model, ok_dec = check("LTspice " + os.path.basename(f), g.to_codes(v))
        good &= ok_model and ok_dec
    if len(sys.argv) == 1:
        seg = d.nec_timeline(0x04, 0x08, repeats=1)
        for isig in (10e-9, 20e-9, 30e-9, 80e-9):
            _, v, _ = d.synth_tia(seg, i_sig=isig, interferer=0.1e-6)
            ok_model, _ = check(f"synthetic {isig*1e9:.0f} nA + 100 nA @45k", g.to_codes(v))
            good &= ok_model
        _, v, _ = d.synth_tia([(0, 0.14)], i_sig=0.0, interferer=0.1e-6)
        fr, _ = rx.run_c(g.to_codes(v), "iq")
        print(f"{'no remote, 100 nA @45k only':36s} I/Q C frames: {len(fr)}  {'OK' if not fr else 'BAD'}")
        good &= not fr
    print("\nPASS" if good else "\nFAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
