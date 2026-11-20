# Power report flag bytes

[← PyTap README](../README.md)

Each TS4 power report carries two flag bytes:

- **status**: report bytes 8–9 (a 16-bit word; byte 8 has been 0 in every report seen so far),
- **jstatus**: report byte 7.

They are **bitfields**: read them bit by bit, not as whole codes. PyTap keeps the latest values per optimizer, shows them decoded under `flags` in the integration's diagnostics, and exposes them as diagnostic entities (see [Status flags and reading faults](entities.md#status-flags-and-reading-faults)).

## Evidence

Tigo doesn't document these bytes. The meanings below come from passive captures of two gateways (6 and 12 optimizers) on 2026-11-20, analysed with [`scripts/capture_reports.py`](../scripts/capture_reports.py):

| Capture | Period | Power reports |
| --- | --- | --- |
| Dawn | 07:26 (wake-up) to 10:35 (full sun) | 99,618 |
| Dusk | 15:22 to 17:46 (last report), then 25 minutes of night | 70,494 |

Readings were compared with the inverters' own string measurements. Meanings these can't settle are marked **tentative** or **open**. Earlier observations: Bobsilvio's [protocol notes](https://github.com/Bobsilvio/tigo_server/blob/main/docs/PROTOCOL-NOTES.md) (bit 5 from the CCA firmware) and Tim Laing's upstream [PR #39](https://github.com/azebro/pytap/pull/39).

## status (bytes 8–9)

| Bit | Mask | Meaning | Evidence |
| --- | --- | --- | --- |
| 7 | `0x80` | **Low input voltage** | Input averaged 15 V when set at dawn and 13–14 V at dusk, against 32–35 V when clear. 88% (dawn) and 94–98% (dusk) of reports below 20 V had it. Not a hard threshold: a few reports at 13.8 V didn't, and a few up to 24 V did. |
| 6 | `0x40` | open | Set in every report. |
| 5 | `0x20` | **Temperature valid** | From the CCA firmware; set in every report seen. PyTap only updates temperature when it is set. |
| 3, 2 | `0x08`, `0x04` | **A 2-bit state field** | Exactly one of the two was set in all ~170,000 reports, never both or neither. Bit 2 is the common state. Bit 3 is **open**: it appeared only in the morning (PR #39 calls it low current, but one optimizer set it 66% of the time at 2.5 A while a neighbour at 2.4 A never did), and never at dusk. |
| 1 | `0x02` | **Idle pass-through** (tentative) | Whenever set, the optimizer was passing straight through (duty 100 %, output voltage = input) with almost no current, and almost always jstatus `0x00`. It appeared only in the morning, before the inverters loaded the strings, and only on some optimizers (always on 6, never on 11, partly on 1). It never appeared at dusk, although the optimizers went idle then too. |
| 4, 0 | `0x10`, `0x01` | open | Never set. |

Whole codes seen: `0x64` (normal), `0x66` (normal + bit 1, mornings only), `0x68` (bit 3 instead of bit 2, mornings only), `0xE4` (normal + low input voltage). At dusk only `0x64` and `0xE4` occurred.

## jstatus (byte 7)

| Bits | Meaning | Evidence |
| --- | --- | --- |
| 7 (`0x80`) | **High output mode** (tentative) | Set in nearly every report above about **0.5 A** input current (94–100% at dawn, 77–100% on the 12-optimizer gateway at dusk) and rarely below 0.25 A; mixed in between. It is **not** simply "current is flowing": thousands of reports with current had it clear, at dusk occasionally up to 2.7 A. The name is Tim Laing's (PR #39: "set almost only when input current exceeds ~0.5 A", from about 217,000 reports on his installation), which these captures confirm. Its entity is *High output mode* (entity ID `binary_sensor.tigo_<barcode>_producing`, from an earlier name). |
| 0–6 | **A conversion level**, tentative | Values 1–4, also when bit 7 is clear. In all three data sets (dawn, and dusk on each gateway), level 1 is pass-through (output/input voltage 0.92–0.99), levels 2 and 3 convert progressively more (0.80–0.93, then 0.58–0.87), and level 4 sits between 2 and 3 (0.65–0.90). Idle reports carry `0x00`–`0x04`. |

## Traffic

The bus runs at about the same rate day and night: 11.9 MB an hour on the 6-optimizer gateway and 9.7–9.8 MB an hour on the 12-optimizer one, before and after the optimizers went to sleep. The CCA keeps polling the TAPs all night. Neither gateway had a frame with a bad checksum, or one the live parser would have dropped, in either capture.

## Open questions

- **Bit 3 and bit 1** appear only in the morning. A capture through a full day (midday, dusk and dawn together), and the optimizers' hardware IDs (from their Version answers), would help.
- **jstatus bit 7**: what decides it when the current is between 0.25 and 1 A.
- **Bit 6** and the **jstatus levels**.

## Contributing data

Captures from other installations help most, especially with different optimizer hardware. Record one with the `pytap.start_capture` action (see [Packet capture and replay](capture.md)), then run:

```sh
python scripts/capture_reports.py /path/to/capture --output reports.csv --summary
```

The summary lists, per optimizer, how often each status and jstatus value occurred; the CSV has every report with its measurements and decoded bits. Both contain barcodes, so share them deliberately, and delete the capture when you're done.
