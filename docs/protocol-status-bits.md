# Power report flag bytes

[← PyTap README](../README.md)

Each TS4 power report carries two flag bytes:

- **status**: report bytes 8–9 (a 16-bit word; byte 8 has been 0 in every report seen so far),
- **jstatus**: report byte 7.

They are **bitfields**: read them bit by bit, not as whole codes. PyTap keeps the latest values per optimizer, shows them decoded under `flags` in the integration's diagnostics, and exposes them as diagnostic entities (see [Status flags and reading faults](entities.md#status-flags-and-reading-faults)).

## Evidence

Tigo doesn't document these bytes. The meanings below come from passive captures of two gateways (6 and 12 optimizers) on 2026-11-20, 2026-11-21 and 2026-11-22, analysed with [`scripts/capture_reports.py`](../scripts/capture_reports.py):

| Capture | Period | Power reports |
| --- | --- | --- |
| Dawn | 07:26 (wake-up) to 10:35 (full sun) | 99,618 |
| Dusk | 15:22 to 17:46 (last report), then 25 minutes of night | 70,494 |
| Full day | 24 hours from 18:28 on 2026-11-20: reports from 07:33 to 17:43 | 320,128 |
| Second full day | 24 hours from 22:20 on 2026-11-21: reports from 07:47 to 17:32, a dull day | 304,850 |

Readings were compared with the inverters' own string measurements. On the second full day PyTap's live reading counts matched the capture exactly (304,850 reports) and its daily energy matched the energy integrated from the raw reports to within 0.05% per optimizer. Meanings these can't settle are marked **tentative** or **open**. Earlier observations: Bobsilvio's [protocol notes](https://github.com/Bobsilvio/tigo_server/blob/main/docs/PROTOCOL-NOTES.md) (bit 5 from the CCA firmware) and Tim Laing's upstream [PR #39](https://github.com/azebro/pytap/pull/39).

## status (bytes 8–9)

| Bit | Mask | Meaning | Evidence |
| --- | --- | --- | --- |
| 7 | `0x80` | **Low input voltage** | Input averaged 15 V when set at dawn and 13–14 V at dusk, against 32–35 V when clear. 88% (dawn) and 94–98% (dusk) of reports below 20 V had it. Not a hard threshold: a few reports at 13.8 V didn't, and a few up to 24 V did. |
| 6 | `0x40` | open | Set in every report (about 795,000). |
| 5 | `0x20` | **Temperature valid** | From the CCA firmware; set in every report seen. PyTap only updates temperature when it is set. |
| 3, 2 | `0x08`, `0x04` | **A 2-bit state field** | Exactly one of the two was set in all ~795,000 reports, never both or neither. Bit 2 is the common state. Bit 3 is **open**: it appeared only in the morning, never at midday or dusk, and on only one or two optimizers per day (different ones on different days). Over the full day it was set in 910 reports, all on one optimizer between 07:33 and 09:00, almost all idle (median 0.07 A, duty 100 %, jstatus `0x00`), which fits PR #39's "low current". But on the first morning one optimizer set it 66% of the time at 2.5 A while a neighbour at 2.4 A never did. On the second full day it didn't appear at all. |
| 1 | `0x02` | **Idle pass-through** (tentative) | Whenever set, the optimizer was passing straight through (duty 100 %, output voltage = input) with almost no current, and almost always jstatus `0x00`. It appeared only in the morning, before the inverters loaded the strings, and only on some optimizers (always on 6, never on 11, partly on 1). Both full days confirm it: 6,050 and 7,376 reports, all between about 07:30 and 09:00, at a median 0.06 A, on 7 and 8 optimizers. It never appeared at dusk, although the optimizers went idle then too. |
| 4, 0 | `0x10`, `0x01` | open | Never set. |

Whole codes seen: `0x64` (normal), `0x66` (normal + bit 1, mornings only), `0x68` (bit 3 instead of bit 2, mornings only), `0xE4` (normal + low input voltage). At dusk only `0x64` and `0xE4` occurred.

## jstatus (byte 7)

| Bits | Meaning | Evidence |
| --- | --- | --- |
| 7 (`0x80`) | **High output mode** (tentative) | Never set below 0.1 A. Over the full day it was set in 96–99% of reports above 3 A, but between 0.1 and 3 A it depended on the gateway: 63–98% on the 12-optimizer gateway, 28–81% on the 6-optimizer one. It is a **mode with hysteresis**, not a threshold: between 0.25 and 1 A a report almost always repeated the previous report's bit (58,008 stayed set and 10,852 stayed clear, against 92 changes), and each optimizer switched about 21 times in the day. It is **not** simply "current is flowing", nor "converting": half the reports with it set had a 100 % duty cycle. The name is Tim Laing's (PR #39: "set almost only when input current exceeds ~0.5 A", from about 217,000 reports on his installation), which these captures confirm. The second full day repeated the pattern on a dull day: set in 0% below 0.1 A, then 5–43% (6-optimizer gateway) and 64–94% (12-optimizer gateway) between 0.1 and 1 A, and 75% of all reports between 0.1 and 0.5 A. Adam Zebrowski saw it set on all 26 optimizers of an installation at low output ([review of PR #39](https://github.com/azebro/pytap/pull/39)), so the name may mislead at low current. Its entity is *High output mode* (entity ID `binary_sensor.tigo_<barcode>_producing`, from an earlier name). |
| 0–6 | **A level, 0–4**, open | Values 0–4, also when bit 7 is clear. At dawn and dusk (low current) level 1 was pass-through (output/input voltage 0.92–0.99), levels 2 and 3 converted progressively more (0.80–0.93, then 0.58–0.87), and level 4 sat between 2 and 3 (0.65–0.90). The full day doesn't support a conversion level in general: at higher currents levels 1 and 2 were both pass-through (duty 100 %, ratio 0.99), and level 3 and 4 converted only on the 12-optimizer gateway (ratio 0.91–0.93). Level 0 appeared only while idle, mostly before 09:00. Every level occurred at every hour of production. Idle reports carry `0x00`–`0x04`. |

## Traffic

The bus runs at about the same rate day and night: 11.9 MB an hour on the 6-optimizer gateway and 9.7–9.8 MB an hour on the 12-optimizer one, before and after the optimizers went to sleep, and the same averaged over each full 24 hours (284.8 and 233.5–233.6 MB). The CCA keeps polling the TAPs all night. Neither gateway had a frame with a bad checksum, or one the live parser would have dropped, in either capture.

## Open questions

- **Bit 3 and bit 1** appear only in the morning, while idle before the inverters load the strings; two full days confirmed neither appears at any other time, and bit 3 doesn't appear every day. Why only some optimizers set them may need their hardware IDs (from their Version answers).
- **jstatus bit 7**: it latches, so the current alone doesn't decide it. What switches it, and why the two gateways differ below 3 A.
- **Bit 6** (always set) and the **jstatus levels**, which follow conversion only at low current.

## Contributing data

Captures from other installations help most, especially with different optimizer hardware. Record one with the `pytap.start_capture` action (see [Packet capture and replay](capture.md)), then run:

```sh
python scripts/capture_reports.py /path/to/capture --output reports.csv --summary
```

The summary lists, per optimizer, how often each status and jstatus value occurred; the CSV has every report with its measurements and decoded bits. Both contain barcodes, so share them deliberately, and delete the capture when you're done.
