/* Fixed-point streaming Goertzel carrier detector (38 kHz @ 500 kSPS).
 *
 * Port of goertzel_envelope() in ../ir_dsp.py: N=128 Hann-windowed blocks,
 * a new block every GZ_HOP samples, per-block mean removal.  Integer only;
 * needs 32x32->64 multiplies (native on ESP32, library call on Cortex-M0+).
 *
 * Output unit is 1/8 ADC code of carrier amplitude (Q3), i.e. the peak
 * amplitude of the 38 kHz component: amp_codes = amp_q3 / 8.0.
 */
#ifndef GOERTZEL_H
#define GOERTZEL_H

#include <stdint.h>
#include "gz_tables.h"

typedef struct {
    uint16_t buf[GZ_N];     /* circular buffer of raw ADC codes */
    uint16_t head;          /* next write position == oldest sample */
    uint16_t fill;          /* samples held, saturates at GZ_N */
    uint16_t hop_count;     /* samples left until the next block */
} gz_state_t;

void gz_init(gz_state_t *st);

/* Push one ADC code (0..4095).  Returns 1 and writes *amp_q3 when a block
 * completes (first after GZ_N samples, then every GZ_HOP), else 0. */
int gz_push(gz_state_t *st, uint16_t code, int32_t *amp_q3);

/* One block over GZ_N samples read from buf starting at 'start' (circular). */
int32_t gz_block(const uint16_t *buf, uint16_t start);

#endif
