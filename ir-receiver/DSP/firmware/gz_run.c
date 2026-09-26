/* Host test driver: gz_run [-iq] <in.u16> <out.i32>
 * Reads little-endian uint16 ADC codes, writes one int32 amp_q3 per
 * envelope sample from the Goertzel (default) or the I/Q detector (-iq). */
#include <stdio.h>
#include <string.h>
#include "goertzel.h"
#include "iq.h"

int main(int argc, char **argv)
{
    int use_iq = argc > 1 && strcmp(argv[1], "-iq") == 0;
    if (argc != 3 + use_iq) { fprintf(stderr, "usage: gz_run [-iq] in.u16 out.i32\n"); return 2; }
    FILE *fi = fopen(argv[1 + use_iq], "rb"), *fo = fopen(argv[2 + use_iq], "wb");
    if (!fi || !fo) { perror("open"); return 1; }

    static gz_state_t gz;
    static iq_state_t iq;
    gz_init(&gz);
    iq_init(&iq);
    uint16_t code;
    int32_t amp;
    long nin = 0, nout = 0;
    while (fread(&code, sizeof code, 1, fi) == 1) {
        nin++;
        int got = use_iq ? iq_push(&iq, code, &amp) : gz_push(&gz, code, &amp);
        if (got) { fwrite(&amp, sizeof amp, 1, fo); nout++; }
    }
    fclose(fi);
    fclose(fo);
    fprintf(stderr, "%ld samples -> %ld envelope samples\n", nin, nout);
    return 0;
}
