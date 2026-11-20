# Credits

[← PyTap README](../README.md)

PyTap is an independently maintained fork of [azebro/pytap](https://github.com/azebro/pytap). It builds on the work of the people below.

## Adam Zebrowski — wrote PyTap

<a href="https://github.com/azebro"><img src="https://github.com/azebro.png?size=96" width="64" height="64" alt="Adam Zebrowski" align="left"></a>

**PyTap is the work of [Adam Zebrowski](https://github.com/azebro)** ([azebro/pytap](https://github.com/azebro/pytap)), and this fork stands entirely on it. Adam wrote the integration from the ground up: the embedded Python parser for the Tigo gateway protocol, the push-based coordinator that turns bus traffic into Home Assistant entities, node-table and barcode mapping that survives restarts, per-optimizer energy integration, the string and installation totals, the configuration flow, and the PV Production dashboard and its Grafana version. Everything this fork adds builds on that foundation, and it's a pleasure to build on: a clean design, thorough tests and careful documentation. Thank you, Adam.

<br clear="left">

## Tim Laing — reliability, connections, discovery, capture and status

<a href="https://github.com/timlaing"><img src="https://github.com/timlaing.png?size=96" width="64" height="64" alt="Tim Laing" align="left"></a>

**[Tim Laing](https://github.com/timlaing)** wrote a careful series of upstream pull requests that this fork has adopted, fitting each to the fork's design:

<br clear="left">

| PR | What it brought |
| --- | --- |
| [#32](https://github.com/azebro/pytap/pull/32) | Parser hardening against invalid slot counters |
| [#33](https://github.com/azebro/pytap/pull/33) | Reliability: the listener's state separated from Home Assistant's, bounded shutdown, lifecycle fixes |
| [#34](https://github.com/azebro/pytap/pull/34) | Configurable stale-reading behaviour |
| [#35](https://github.com/azebro/pytap/pull/35) | String and total energy that survives membership changes |
| [#36](https://github.com/azebro/pytap/pull/36) | Serial and ESPHome serial-proxy connections |
| [#37](https://github.com/azebro/pytap/pull/37) | Passive module discovery and module editing |
| [#38](https://github.com/azebro/pytap/pull/38) | Opt-in raw packet capture and offline replay, the basis of the fork's research tools |
| [#39](https://github.com/azebro/pytap/pull/39) | Per-optimizer status entities and detection of impossible readings |

The series is thoughtfully designed and thoroughly tested, and it made PyTap sturdier, easier to set up and easier to maintain. Thank you, Tim.

## Kyrill — averaged reporting

<a href="https://github.com/poolski"><img src="https://github.com/poolski.png?size=96" width="64" height="64" alt="Kyrill" align="left"></a>

[Kyrill](https://github.com/poolski) contributed the averaged reporting interval upstream: the configurable write interval and the time-weighted averaging of readings across it, which this fork now uses by default.

<br clear="left">

## seaosair — database load

<a href="https://github.com/seaosair"><img src="https://github.com/seaosair.png?size=96" width="64" height="64" alt="seaosair" align="left"></a>

[seaosair](https://github.com/seaosair) worked out why PyTap wrote so much to the recorder, and proposed rounding states and fixing the per-reading `last_update` attribute in [PR #25](https://github.com/azebro/pytap/pull/25). They backed the proposal with careful measurements from a 23-module installation, including the less obvious points: excluding an attribute from the recorder doesn't stop the write, and rounding only helps once that attribute is fixed. PyTap's rounding and its removal of per-reading timestamp attributes follow their work.

<br clear="left">

## The protocol

<a href="https://github.com/willglynn"><img src="https://github.com/willglynn.png?size=96" width="64" height="64" alt="Will Glynn" align="left"></a>

The Tigo protocol was reverse-engineered by [taptap](https://github.com/willglynn/taptap) ([Will Glynn](https://github.com/willglynn)). PyTap was inspired by the [taptap fork](https://github.com/litinoveweedle/taptap) and [taptap Home Assistant add-on](https://github.com/litinoveweedle/hassio-addons) by [litinoveweedle](https://github.com/litinoveweedle).

<br clear="left">

<a href="https://github.com/Bobsilvio"><img src="https://github.com/Bobsilvio.png?size=96" width="64" height="64" alt="Bobsilvio" align="left"></a>

PyTap's decoding details (input current scale by hardware ID, the temperature status bit, RSSI 0, 12-byte reports) come from [Bobsilvio](https://github.com/Bobsilvio)'s [protocol notes](https://github.com/Bobsilvio/tigo_server/blob/main/docs/PROTOCOL-NOTES.md) for [tigo_server](https://github.com/Bobsilvio/tigo_server), which work out how the CCA firmware itself decodes the bus and where taptap is incomplete. Their ESP32 project, which grew from [tictactom/tigo_server](https://github.com/tictactom/tigo_server), is also the example in [Future work](how-it-works.md#future-work).

<br clear="left">
