#include <string.h>
#include "ir_rx.h"

/* NEC timing in half-microseconds (T = 562.5 us) */
#define NEC_T_H          1125u
#define RUN_MIN_US       150u

enum { NEC_IDLE, NEC_LEAD_SPACE, NEC_BIT_MARK, NEC_BIT_SPACE };

/* |x - t| <= (num/den) * t, same test as ir_dsp._near() */
static int near(uint32_t x, uint32_t t, uint32_t num, uint32_t den)
{
    uint32_t diff = x > t ? x - t : t - x;
    return (uint64_t)diff * den <= (uint64_t)num * t;
}

/* ------------------------------------------------------------------ NEC */
static int nec_push(ir_dec_t *d, uint8_t level, uint32_t len, uint32_t start,
                    ir_frame_t *out, int cap)
{
    uint32_t dur = len * IR_ENV_PERIOD_US * 2u;         /* half-microseconds */

    switch (d->nec_state) {
    case NEC_LEAD_SPACE:
        if (near(dur, 4 * NEC_T_H, 1, 4)) {              /* repeat code */
            d->nec_state = NEC_IDLE;
            if (cap < 1) return 0;
            memset(out, 0, sizeof *out);
            out->repeat = 1;
            out->t_env = d->nec_t0;
            return 1;
        }
        if (near(dur, 8 * NEC_T_H, 1, 4)) {
            d->nec_state = NEC_BIT_MARK;
            d->nec_bit = 0;
            d->nec_bits = 0;
            return 0;
        }
        d->nec_state = NEC_IDLE;                         /* a space is never a leader */
        return 0;

    case NEC_BIT_MARK:
        if (near(dur, NEC_T_H, 9, 20)) { d->nec_state = NEC_BIT_SPACE; return 0; }
        d->nec_state = NEC_IDLE;
        return nec_push(d, level, len, start, out, cap); /* maybe a new leader */

    case NEC_BIT_SPACE:
        if (near(dur, 3 * NEC_T_H, 3, 10)) {
            d->nec_bits |= (uint32_t)1 << d->nec_bit;    /* LSB first */
        } else if (!near(dur, NEC_T_H, 9, 20)) {
            d->nec_state = NEC_IDLE;
            return 0;
        }
        if (++d->nec_bit < 32) { d->nec_state = NEC_BIT_MARK; return 0; }
        d->nec_state = NEC_IDLE;
        {
            uint8_t b0 = d->nec_bits, b1 = d->nec_bits >> 8,
                    b2 = d->nec_bits >> 16, b3 = d->nec_bits >> 24;
            if ((uint8_t)(b2 ^ b3) != 0xFF || cap < 1) return 0;
            int std = (uint8_t)(b0 ^ b1) == 0xFF;
            memset(out, 0, sizeof *out);
            out->extended = !std;
            out->addr = std ? b0 : (uint16_t)(b0 | (uint16_t)b1 << 8);
            out->cmd = b2;
            out->t_env = d->nec_t0;
            return 1;
        }

    default: /* NEC_IDLE */
        if (level && near(dur, 16 * NEC_T_H, 1, 4)) {
            d->nec_state = NEC_LEAD_SPACE;
            d->nec_t0 = start;
        }
        return 0;
    }
}

/* ---------------------------------------------------- run-length merge */
/* A run shorter than RUN_MIN_US, or at the same level as the previous one,
 * is added to the previous run (ir_dsp.run_lengths).  A merged run is final
 * once a long run of the other level starts, so it is handed on then. */
static int run_push(ir_dec_t *d, uint8_t level, uint32_t len, uint32_t start,
                    ir_frame_t *out, int cap)
{
    if (d->have_run && (len * IR_ENV_PERIOD_US < RUN_MIN_US || level == d->run_level)) {
        d->run_len += len;
        return 0;
    }
    int n = d->have_run ? nec_push(d, d->run_level, d->run_len, d->run_start, out, cap) : 0;
    d->run_level = level;
    d->run_len = len;
    d->run_start = start;
    d->have_run = 1;
    return n;
}

static int feed_bit(ir_dec_t *d, uint8_t bit, ir_frame_t *out, int cap)
{
    if (d->bit_hook) d->bit_hook(d->bit_ctx, bit, d->th_q16);
    uint32_t idx = d->n_bits++;
    if (idx == 0) { d->raw_level = bit; d->raw_len = 1; d->raw_start = 0; return 0; }
    if (bit == d->raw_level) { d->raw_len++; return 0; }
    int n = run_push(d, d->raw_level, d->raw_len, d->raw_start, out, cap);
    d->raw_level = bit;
    d->raw_len = 1;
    d->raw_start = idx;
    return n;
}

/* --------------------------------------------------------------- slicer */
static uint8_t slice(ir_dec_t *d, int32_t e)
{
    int64_t E = (int64_t)e << 16;
    if (!d->state && E < 2 * d->floor_q16) {
        d->floor_q16 += (SL_A_FLR_Q16 * (E - d->floor_q16)) >> 16;
        if (d->floor_q16 < 1) d->floor_q16 = 1;          /* python adds 1e-12 */
    }
    int64_t a = E > d->peak_q16 ? SL_A_ATT_Q16 : SL_A_DEC_Q16;
    d->peak_q16 += (a * (E - d->peak_q16)) >> 16;
    int64_t th = d->floor_q16 +
        (((int64_t)(d->state ? SL_LO_Q16 : SL_HI_Q16) * (d->peak_q16 - d->floor_q16)) >> 16);
    d->state = d->peak_q16 > SL_SNR * d->floor_q16 && E > th;
    d->th_q16 = th;
    return d->state;
}

static int32_t median_init(const int32_t *src)
{
    int32_t v[SL_INIT_N];
    memcpy(v, src, sizeof v);
    for (int i = 1; i < SL_INIT_N; i++) {               /* insertion sort, 31 items */
        int32_t x = v[i];
        int j = i - 1;
        while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; }
        v[j + 1] = x;
    }
    return (SL_INIT_N & 1) ? v[SL_INIT_N / 2]
                           : (v[SL_INIT_N / 2 - 1] + v[SL_INIT_N / 2]) / 2;
}

int ir_dec_push_env(ir_dec_t *d, int32_t e, ir_frame_t out[IR_MAX_FRAMES])
{
    uint32_t i = d->n_env++;
    if (i < SL_WARMUP) return feed_bit(d, 0, out, IR_MAX_FRAMES);

    uint32_t k = i - SL_WARMUP;
    if (k >= SL_INIT_N) return feed_bit(d, slice(d, e), out, IR_MAX_FRAMES);

    /* The floor starts at the median of the first 2 ms after warm-up, so
     * those samples are held back and sliced once the median is known. */
    d->init_buf[k] = e;
    if (k < SL_INIT_N - 1) return 0;
    d->floor_q16 = (int64_t)median_init(d->init_buf) << 16;
    if (d->floor_q16 < 1) d->floor_q16 = 1;
    d->peak_q16 = d->floor_q16;
    int n = 0;
    for (int j = 0; j < SL_INIT_N; j++)
        n += feed_bit(d, slice(d, d->init_buf[j]), out + n, IR_MAX_FRAMES - n);
    return n;
}

void ir_rx_init(ir_rx_t *rx, int detector)
{
    rx->detector = (uint8_t)detector;
    gz_init(&rx->gz);
    iq_init(&rx->iq);
    memset(&rx->dec, 0, sizeof rx->dec);
    rx->dec.th_q16 = -1;
}

int ir_rx_push(ir_rx_t *rx, uint16_t code, ir_frame_t out[IR_MAX_FRAMES])
{
    int32_t amp;
    int got = rx->detector == IR_DET_IQ ? iq_push(&rx->iq, code, &amp)
                                        : gz_push(&rx->gz, code, &amp);
    if (!got) return 0;
    return ir_dec_push_env(&rx->dec, amp, out);
}
