#include <string.h>
#include "iq.h"

/* Round-half-up right shift; arithmetic >> on negatives, as in goertzel.c */
#define RSHIFT_R32(x, s) (((x) + ((int32_t)1 << ((s) - 1))) >> (s))

static int16_t sat16(int32_t v)
{
    return v > 32767 ? 32767 : v < -32767 ? -32767 : (int16_t)v;
}

static uint32_t isqrt32(uint32_t v)
{
    uint32_t r = 0, bit = (uint32_t)1 << 30;
    while (bit > v) bit >>= 2;
    while (bit) {
        if (v >= r + bit) { v -= r + bit; r = (r >> 1) + bit; }
        else              { r >>= 1; }
        bit >>= 2;
    }
    return r;               /* floor(sqrt(v)) */
}

void iq_init(iq_state_t *st)
{
    memset(st, 0, sizeof *st);
}

/* CIC comb section for one channel, run at the decimated rate. Integrators
 * and combs use wrapping uint32 arithmetic; the true output (< 2^25) is exact. */
static int16_t cic_out(uint32_t *integ, uint32_t *comb)
{
    uint32_t y = integ[IQ_ORDER - 1];
    for (int k = 0; k < IQ_ORDER; k++) {
        uint32_t t = y;
        y -= comb[k];
        comb[k] = t;
    }
    return sat16(RSHIFT_R32((int32_t)y, IQ_CIC_SHIFT));    /* / R1^ORDER, Q4 */
}

static int32_t fir(const int16_t *buf, uint8_t head)
{
    int32_t acc = 0;        /* sum|h| * 32767 < 2^31, checked by the table generator */
    for (int k = 0; k < IQ_NTAPS; k++) {
        int idx = (int)head - 1 - k;
        if (idx < 0) idx += IQ_NTAPS;
        acc += (int32_t)IQ_FIR_Q15[k] * buf[idx];
    }
    return RSHIFT_R32(acc, 15);
}

int iq_push(iq_state_t *st, uint16_t code, int32_t *amp_q3)
{
    /* DC blocker: subtract a slow running mean (keeps the mixer input small) */
    if (!st->started) { st->dc_q8 = (int32_t)code << 8; st->started = 1; }
    int16_t x_q4 = sat16(((int32_t)code << 4) - RSHIFT_R32(st->dc_q8, 4));
    st->dc_q8 += RSHIFT_R32(((int32_t)code << 8) - st->dc_q8, IQ_DC_SHIFT);

    /* mix to baseband: 38 kHz -> 0 Hz, 45 kHz interferer -> 7 kHz */
    int32_t mi = RSHIFT_R32((int32_t)x_q4 * IQ_COS_Q14[st->lo], 14);
    int32_t mq = RSHIFT_R32((int32_t)x_q4 * IQ_SIN_Q14[st->lo], 14);
    if (++st->lo == IQ_LO_N) st->lo = 0;

    /* CIC integrators at the full rate */
    uint32_t vi = (uint32_t)mi, vq = (uint32_t)mq;
    for (int k = 0; k < IQ_ORDER; k++) {
        st->integ_i[k] += vi; vi = st->integ_i[k];
        st->integ_q[k] += vq; vq = st->integ_q[k];
    }
    if (++st->r1_count < IQ_R1) return 0;
    st->r1_count = 0;

    /* 62.5 kHz: CIC combs, into the FIR delay line */
    st->fir_i[st->fir_head] = cic_out(st->integ_i, st->comb_i);
    st->fir_q[st->fir_head] = cic_out(st->integ_q, st->comb_q);
    if (++st->fir_head == IQ_NTAPS) st->fir_head = 0;
    if (++st->r2_count < IQ_R2) return 0;
    st->r2_count = 0;

    /* 15.625 kHz: low-pass, then magnitude.  |z| in Q4 is the carrier
     * amplitude in Q3 (mixing halves it, so 2|z| = amplitude). */
    int32_t zi = fir(st->fir_i, st->fir_head);
    int32_t zq = fir(st->fir_q, st->fir_head);
    *amp_q3 = (int32_t)isqrt32((uint32_t)(zi * zi) + (uint32_t)(zq * zq));
    return 1;
}
