# When readings stop: night, dropouts and estimates

[← PyTap README](../README.md)

## When readings go to 0 or unavailable

Optimizers stop reporting when it gets dark, and by day one can drop off the radio link to the TAP for a while (2.4 GHz interference) and come back by itself. PyTap therefore treats silence differently depending on the sun's position, which it works out from your Home Assistant location:

| Situation | Live readings | Energy and Readings Today |
| --- | --- | --- |
| Reporting | Latest values | Counting |
| Sun below 10° and no report for 10 minutes (asleep) | Power, voltage, current, performance and duty cycle read **0**; temperature and RSSI keep their last value | Kept |
| Daylight, no report yet since the optimizers went to sleep | Stay at 0 | Kept |
| Daylight, no report for **30 minutes** (silent) | **Unavailable**, and a warning is logged | Kept |
| Bus status not normal: no data from the bus for 5 minutes | **Unavailable** for every optimizer on that gateway, and a warning is logged | Kept |

Short daytime gaps keep the last value, so a brief radio dropout doesn't zero a panel or break its history. If Home Assistant has no location set, PyTap never assumes it is night.

**After a restart at night**, optimizers that were already quiet for 10 minutes read as asleep straight away: PyTap remembers when each one last reported. After a daytime restart, their last values are held until they report again, normally within seconds.

**You can change what an optimizer's own live sensors show** under **Configure → Change stale reading behaviour**:

| Setting | Choices | Default |
| --- | --- | --- |
| At night (asleep) | **Zero** as above, or **Hold last value** | Zero |
| When silent in daylight | **Unavailable** as above, **Hold last value**, or **Zero** (as at night) | Unavailable |
| Silent after | 5–240 minutes | 30 |

Energy and Readings Today are never affected, and the String and Total devices keep their rules whatever you choose: sleeping optimizers count as 0 W, and silent ones as their estimate. Short dropouts are normal: on one 19-panel installation, gaps between reports reached about 76 s during production and 171 s at dawn, so keep *Silent after* well above a few minutes. These settings and the measurements come from [Tim Laing](https://github.com/timlaing)'s upstream [PR #34](https://github.com/azebro/pytap/pull/34).

These rules apply to each optimizer's own sensors. For the String and Total devices, see the next section.

## Estimating silent optimizers

An optimizer that stops reporting hasn't necessarily stopped producing. The optimizers in a string are wired in series and carry the same current, so if the rest of the string is producing normally, the silent one likely is too; it just isn't being heard. If it fails in a way that breaks the string, the others may show it, because the string's current might drop.

PyTap uses this on the String and Total devices:

1. **Learning each optimizer's share.** While an optimizer and the rest of its string are reporting, PyTap keeps a rolling average (over roughly the last 10 minutes) of its power divided by the average power of the others in its string. This captures shading: a panel that is shaded in the afternoon has a share below 1 at that time.
2. **Estimating.** Once an optimizer has been quiet for 2 minutes, and at least half of the other optimizers in its string are reporting, its estimated power is its share × their current average. If no share has been learned yet, it is assumed to produce the average.
3. **Estimated energy.** The estimated power is added up as *estimated energy* from its last report until it reports again, and checked every minute. A gap of 2–3 minutes that ends before the next check is bridged with the average of the optimizer's own readings either side of it, also counted as estimated energy. (Optimizers report power, not energy: PyTap adds up energy from readings no more than 120 s apart, so without this such a gap would be lost.) The same applies after a Home Assistant restart, for gaps of up to 10 minutes: see [Known limitations](troubleshooting.md#known-limitations).

| | Optimizer's own sensors | String and Total devices |
| --- | --- | --- |
| Power | What it reported (held, then 0 or unavailable as above) | The estimate while one is available |
| Daily and total energy | **Only what it reported** | What all optimizers reported **plus** estimated energy |

So a string's energy can be slightly more than the sum of its optimizers' energy sensors, by exactly the string's `estimated_energy` attribute. The **Power** sensors on the String and Total devices have an `estimated_count` attribute showing how many optimizers are being estimated right now, and `reporting_count` showing how many reported within the last 2 minutes (Data validity *Reporting*; asleep, holding, estimated and silent optimizers aren't counted).

Estimating needs neighbours: an optimizer that is alone in its string, or without a string, is never estimated, and nothing is estimated when fewer than half of the others in its string are reporting (for example at night). A silent optimizer without an estimate is left out of the String and Total devices' **Power** until it reports again.

**Turning it off.** Untick **Estimate silent optimizers** under **Configure → Change reporting settings**. The String and Total devices then hold a silent optimizer's last reported power instead, and no energy is added for the time it was silent. Energy already estimated stays in the lifetime totals, since those can't go down.

### Data validity sensor

Each optimizer has a diagnostic **Data validity** sensor that shows how much its current readings can be trusted:

| State | Meaning |
| --- | --- |
| Reporting | Readings received within the last 2 minutes. |
| Holding last value | Quiet for more than 2 minutes; its sensors hold the last values, and no estimate is possible (or estimating is off). |
| Estimated from string | Quiet for less than 30 minutes; the String and Total devices are using an estimate from the rest of its string. Its own sensors hold the last values. |
| Silent, estimated in string | Quiet for 30 minutes or more in daylight. Its own live readings are unavailable, but the rest of its string is producing, so the String and Total devices still use an estimate. A long dropout like this usually means a weak radio link to the TAP. |
| Asleep | Night: sun below 10° and quiet for 10 minutes. Live readings read 0. At dusk an optimizer shows Asleep once it has been quiet that long, even while others in its string are still awake. |
| Silent | Quiet for 30 minutes in daylight with no estimate; live readings are unavailable. |

While the gateway's [Bus status](entities.md#bus-status) isn't normal, Data validity is unavailable too: the problem is the whole gateway, not the optimizer.

Its attributes show the learned share of the string average (to the nearest 0.05), the energy estimated for it today, while it is estimated `estimated_power` (the estimate the String and Total devices are using, updated once a minute) and, once it has stopped reporting, `last_report`: when it last reported. The optimizer's other sensors have no per-reading timestamp attribute: one that changes with every reading would make Home Assistant write a database row on every reading even when the value hasn't changed. A panel that often shows *Estimated*, *Silent, estimated in string* or *Silent* has a weak radio link to the TAP.
