#include "goertzel.h"

/* Right shift with round-half-up.  Relies on arithmetic >> for negatives
 * (GCC/Clang on ARM and Xtensa); test_goertzel_fixed.py models the same. */
#define RSHIFT_R(x, s)   (((x) + ((int64_t)1 << ((s) - 1))) >> (s))
#define RSHIFT_R32(x, s) (((x) + ((int32_t)1 << ((s) - 1))) >> (s))

static uint32_t isqrt64(uint64_t v)
{
    uint64_t r = 0, bit = (uint64_t)1 << 62;
    while (bit > v) bit >>= 2;
    while (bit) {
        if (v >= r + bit) { v -= r + bit; r = (r >> 1) + bit; }
        else              { r >>= 1; }
        bit >>= 2;
    }
    return (uint32_t)r;     /* floor(sqrt(v)) */
}

void gz_init(gz_state_t *st)
{
    for (int i = 0; i < GZ_N; i++) st->buf[i] = 0;
    st->head = 0;
    st->fill = 0;
    st->hop_count = 0;
}

int32_t gz_block(const uint16_t *buf, uint16_t start)
{
    int32_t sum = 0;
    for (int i = 0; i < GZ_N; i++) sum += buf[(start + i) & (GZ_N - 1)];
    int32_t mean = (sum + GZ_N / 2) >> GZ_LOG2N;

    int32_t s1 = 0, s2 = 0;
    for (int i = 0; i < GZ_N; i++) {
        int32_t x  = (int32_t)buf[(start + i) & (GZ_N - 1)] - mean;
        /* |x| <= 4095, w <= 32767: product fits in 32 bits (no 64-bit call on M0+) */
        int32_t xw = RSHIFT_R32(x * GZ_HANN_Q15[i], 12);                  /* Q3 */
        int32_t s0 = xw + (int32_t)RSHIFT_R((int64_t)GZ_COEFF_Q14 * s1, 14) - s2;
        s2 = s1;
        s1 = s0;
    }

    int64_t cs1 = RSHIFT_R((int64_t)GZ_COEFF_Q14 * s1, 14);
    int64_t p = (int64_t)s1 * s1 + (int64_t)s2 * s2 - cs1 * s2;
    if (p < 0) p = 0;

    /* amplitude = 2*|X| / sum(w);  window sum is in Q15 */
    uint64_t mag = isqrt64((uint64_t)p);
    return (int32_t)(((mag << 1) << 15) / GZ_HANN_SUM_Q15);
}

int gz_push(gz_state_t *st, uint16_t code, int32_t *amp_q3)
{
    st->buf[st->head] = code;
    st->head = (st->head + 1) & (GZ_N - 1);
    if (st->fill < GZ_N) st->fill++;
    if (st->fill < GZ_N) return 0;

    if (st->hop_count) { st->hop_count--; return 0; }
    st->hop_count = GZ_HOP - 1;
    *amp_q3 = gz_block(st->buf, st->head);
    return 1;
}
