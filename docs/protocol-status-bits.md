# Power report flag bytes

Each TS4 power report carries two flag bytes that PyTap doesn't turn into entities yet:

- **status**: report bytes 8–9 (a 16-bit word; byte 8 has been 0 in every report seen so far),
- **jstatus**: report byte 7.

They are **bitfields**: read them bit by bit, not as whole codes. PyTap keeps the latest values per optimizer, shows them decoded under `flags` in the integration's diagnostics, and exposes them as diagnostic entities (Status, Low input voltage, Producing; and, disabled by default, Idle pass-through, State field, jstatus level; see [Status flags and reading faults](entities.md#status-flags-and-reading-faults) section).

The meanings below come from a passive capture of two gateways (6 and 12 optimizers) on 2026-11-20 from wake-up (07:26) to full sun (10:35), about 99,600 power reports, compared with the inverters' own string measurements. Meanings that one morning can't settle are marked **tentative** or **open**. Earlier observations: Bobsilvio's [protocol notes](https://github.com/Bobsilvio/tigo_server/blob/main/docs/PROTOCOL-NOTES.md) (bit 5 from the CCA firmware) and Tim Laing's upstream [PR #39](https://github.com/azebro/pytap/pull/39).

## status (bytes 8–9)

| Bit | Mask | Meaning | Evidence |
| --- | --- | --- | --- |
| 7 | `0x80` | **Low input voltage** | Input averaged 15 V when set (34.8 V when clear); 88% of reports below 20 V had it. Not a hard threshold: a few reports at 13.8 V didn't. Seen in bursts when an inverter starts tracking and pulls the string down. |
| 6 | `0x40` | open | Set in every report. |
| 5 | `0x20` | **Temperature valid** | From the CCA firmware; set in every report seen. PyTap only updates temperature when it is set. |
| 3, 2 | `0x08`, `0x04` | **A 2-bit state field** | Exactly one of the two was set in all ~99,600 reports, never both or neither. Bit 2 is the common state. Bit 3 is **open**: PR #39 calls it low current, but one optimizer set it 66% of the time at 2.5 A while a neighbour at 2.4 A never did, so it isn't a simple current threshold. |
| 1 | `0x02` | **Idle pass-through** (tentative) | Whenever set, the optimizer was passing straight through (duty 255, output voltage = input) with almost no current, and almost always jstatus `0x00`. But most pass-through reports don't have it: it appeared only before the inverters loaded the strings, and only on some optimizers (always on 6, never on 11, partly on 1). What decides which optimizers enter this state is open. |
| 4, 0 | `0x10`, `0x01` | open | Never set. |

Whole codes seen: `0x64` (normal), `0x66` (normal + bit 1), `0x68` (bit 3 instead of bit 2), `0xE4` (normal + low input voltage).

## jstatus (byte 7)

| Bits | Meaning | Evidence |
| --- | --- | --- |
| 7 (`0x80`) | **Producing** | Set in every report with current flowing; clear (`0x00`/`0x01`) when idle. PR #39 calls it high output mode. |
| 0–6 | **A level**, tentative | Values 1–4 while producing, tracking how hard the optimizer converts: output/input voltage averaged 0.92, 0.80, 0.69 and 0.73 for 1–4. |

## Open questions

A capture through midday and dusk, and the optimizers' hardware IDs (from their Version answers), would help with: bit 3, bit 1 and which optimizers enter it, bit 6, and the jstatus levels.

## Contributing data

Captures from other installations help most, especially with different optimizer hardware. Record one with the `pytap.start_capture` action (see [Packet capture and replay](capture.md#packet-capture-and-replay)), then run:

```sh
python scripts/capture_reports.py /path/to/capture --output reports.csv --summary
```

The summary lists, per optimizer, how often each status and jstatus value occurred; the CSV has every report with its measurements and decoded bits. Both contain barcodes, so share them deliberately, and delete the capture when you're done.
