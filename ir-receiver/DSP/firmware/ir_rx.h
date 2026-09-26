/* Streaming NEC receiver: ADC codes in, decoded frames out.
 *
 *   gz_push()  Goertzel carrier amplitude, every GZ_HOP samples (goertzel.c)
 *   slicer     adaptive floor/peak threshold with hysteresis + SNR gate
 *   runs       mark/space run lengths, glitches < 150 us merged
 *   nec        NEC frame / repeat-code state machine
 *
 * Port of adaptive_slicer(), run_lengths() and nec_decode() in ../ir_dsp.py.
 * Integer only.
 */
#ifndef IR_RX_H
#define IR_RX_H

#include <stdint.h>
#include "goertzel.h"
#include "iq.h"

enum { IR_DET_GOERTZEL = 0, IR_DET_IQ = 1 };   /* carrier detector choice */

typedef struct {
    uint8_t  repeat;        /* 1 = repeat code, addr/cmd not valid */
    uint8_t  extended;      /* 1 = 16-bit address */
    uint16_t addr;
    uint8_t  cmd;
    uint32_t t_env;         /* envelope sample index of the leader mark */
} ir_frame_t;

typedef struct {
    /* slicer */
    uint32_t n_env;                 /* envelope samples seen */
    uint32_t n_bits;                /* slicer bits emitted (lags n_env during init) */
    int32_t  init_buf[SL_INIT_N];   /* first samples after warm-up (floor median) */
    int64_t  floor_q16, peak_q16;   /* in amp_q3 << 16 */
    uint8_t  state;
    /* run lengths */
    uint8_t  raw_level;  uint32_t raw_len;  uint32_t raw_start;
    uint8_t  run_level;  uint32_t run_len;  uint32_t run_start;  uint8_t have_run;
    /* NEC */
    uint8_t  nec_state, nec_bit;
    uint32_t nec_bits, nec_t0;
    int64_t  th_q16;                /* last slicer threshold, -1 before slicing starts */
    /* optional: slicer trace for testing (NULL = off); th_q16 as above */
    void   (*bit_hook)(void *ctx, uint8_t bit, int64_t th_q16);
    void    *bit_ctx;
} ir_dec_t;

typedef struct {
    uint8_t    detector;    /* IR_DET_GOERTZEL or IR_DET_IQ */
    gz_state_t gz;
    iq_state_t iq;
    ir_dec_t   dec;
} ir_rx_t;

#define IR_MAX_FRAMES 4

/* detector: IR_DET_GOERTZEL (cheapest) or IR_DET_IQ (~70 dB rejection of a
 * 45 kHz lamp interferer vs ~25 dB; decodes ~10 nA weaker under it). */
void ir_rx_init(ir_rx_t *rx, int detector);

/* Push one ADC code.  Returns the number of frames written to out[]
 * (0..IR_MAX_FRAMES); almost always 0. */
int ir_rx_push(ir_rx_t *rx, uint16_t code, ir_frame_t out[IR_MAX_FRAMES]);

/* Envelope-level entry point (one Goertzel amp_q3 value); used by ir_rx_push. */
int ir_dec_push_env(ir_dec_t *dec, int32_t amp_q3, ir_frame_t out[IR_MAX_FRAMES]);

#endif
