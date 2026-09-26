/* Host test driver: ir_run [-iq] <in.u16> [bits.u8 [thr.f32]]
 * (-iq selects the I/Q carrier detector; default is the Goertzel)
 * Streams little-endian uint16 ADC codes through the full receiver and
 * prints one line per decoded frame:
 *   F <t_env> <addr> <cmd> <extended>     or     R <t_env>
 * Optional trace files, one entry per envelope sample:
 *   bits.u8   slicer output (0/1)
 *   thr.f32   slicer threshold in ADC codes (NaN before slicing starts) */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "ir_rx.h"

typedef struct { FILE *bits, *thr; } trace_t;

static void write_trace(void *ctx, uint8_t bit, int64_t th_q16)
{
    trace_t *t = ctx;
    fputc(bit, t->bits);
    if (t->thr) {
        float th = th_q16 < 0 ? NAN : (float)((double)th_q16 / 65536.0 / 8.0);
        fwrite(&th, sizeof th, 1, t->thr);
    }
}

int main(int argc, char **argv)
{
    int use_iq = argc > 1 && strcmp(argv[1], "-iq") == 0;
    argc -= use_iq; argv += use_iq;
    if (argc < 2 || argc > 4) { fprintf(stderr, "usage: ir_run [-iq] in.u16 [bits.u8 [thr.f32]]\n"); return 2; }
    FILE *fi = fopen(argv[1], "rb");
    trace_t tr = { argc >= 3 ? fopen(argv[2], "wb") : NULL,
                   argc == 4 ? fopen(argv[3], "wb") : NULL };
    if (!fi || (argc >= 3 && !tr.bits) || (argc == 4 && !tr.thr)) { perror("open"); return 1; }

    static ir_rx_t rx;
    ir_rx_init(&rx, use_iq ? IR_DET_IQ : IR_DET_GOERTZEL);
    if (tr.bits) { rx.dec.bit_hook = write_trace; rx.dec.bit_ctx = &tr; }

    ir_frame_t fr[IR_MAX_FRAMES];
    uint16_t code;
    while (fread(&code, sizeof code, 1, fi) == 1) {
        int n = ir_rx_push(&rx, code, fr);
        for (int i = 0; i < n; i++) {
            if (fr[i].repeat) printf("R %lu\n", (unsigned long)fr[i].t_env);
            else printf("F %lu %u %u %u\n", (unsigned long)fr[i].t_env,
                        fr[i].addr, fr[i].cmd, fr[i].extended);
        }
    }
    fclose(fi);
    if (tr.bits) fclose(tr.bits);
    if (tr.thr) fclose(tr.thr);
    return 0;
}
