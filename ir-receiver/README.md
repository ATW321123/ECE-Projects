# Infrared Receiver Signal Chain

A discrete 38kHz IR Reciever, 4 fundamental analog stages followed by a fixed DSP decoder.

## Repository layout

| Folder | Contents |
|---|---|
| `pcb/` | KiCad 10 project (`.kicad_pro/.kicad_sch/.kicad_pcb`); `pcb/gerbers/` holds the fab outputs |
| `sim/` | Documented LTspice testbenches (Drafts 1-3) and `LM324.lib` |
| `sim/drafts/` | Working LTspice drafts 3-5: NEC-frame stimulus, ×10 gain stage, noise tests, DC servo |
| `sim/exports/` | Waveform exports the DSP scripts read (gitignored; re-export from `sim/drafts/`) |
| `DSP/` | Python + fixed-point C decoders (Goertzel, I/Q) that replace the comparator; see [`DSP/RESULTS.md`](DSP/RESULTS.md) |
| `docs/` | Figures used in this README |

## Signal chain

```
Photodiode → Transimpedance amp → 38 kHz bandpass → Comparator → Output conditioning
```

| Stage | Purpose |
|---|---|
| Transimpedance amplifier | Photodiode current to voltage, 330 kV/A (330 mV/µA) |
| Bandpass filter | 38 kHz center; rejects 120 Hz ambient-light interference |
| Comparator | Squares the recovered envelope to a logic level; Draft 3 adds about 100 mV of hysteresis (LT1011, 10 kΩ / 330 kΩ) |
| Output conditioning | Logic-level output for the ESP32 decoder: 660 Ω / 1 kΩ divider on the board, 4.7 kΩ pull-up to 3.3 V in Draft 3 |

## Schematic

![IR receiver schematic sim in LTSPICE](docs/draft5_servo_schema.png)


