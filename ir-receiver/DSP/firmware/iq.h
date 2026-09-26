/* Fixed-point streaming I/Q carrier detector (38 kHz @ 500 kSPS).
 *
 *   DC blocker -> x cos/sin (250-entry table) -> CIC3 /8 -> 48-tap FIR /4 -> |I + jQ|
 *
 * A firmware-sized version of iq_envelope() in ../ir_dsp.py: the 255-tap
 * low-pass is replaced by a CIC + short FIR, and the output rate matches
 * the Goertzel (one value every 32 samples), so both feed the same slicer.
 * Rejects the 45 kHz interferer (7 kHz after mixing) by ~70 dB vs ~25 dB for
 * the Goertzel.  All multiplies are 16x16->32 bit (cheap on Cortex-M0+).
 *
 * Output unit is 1/8 ADC code of carrier amplitude (Q3), like gz_push().
 */
#ifndef IQ_H
#define IQ_H

#include <stdint.h>
#include "gz_tables.h"

typedef struct {
    int32_t  dc_q8;                 /* DC estimate, ADC codes << 8 */
    uint8_t  started;
    uint16_t lo;                    /* LO table phase */
    uint32_t integ_i[IQ_ORDER], integ_q[IQ_ORDER];   /* CIC integrators (wrap) */
    uint32_t comb_i[IQ_ORDER], comb_q[IQ_ORDER];     /* CIC comb delays */
    uint8_t  r1_count, r2_count;
    int16_t  fir_i[IQ_NTAPS], fir_q[IQ_NTAPS];       /* circular, Q4 */
    uint8_t  fir_head;              /* next write == oldest */
} iq_state_t;

void iq_init(iq_state_t *st);

/* Push one ADC code (0..4095).  Returns 1 and writes *amp_q3 every
 * IQ_R1*IQ_R2 samples, else 0. */
int iq_push(iq_state_t *st, uint16_t code, int32_t *amp_q3);

#endif
