# Installation

[← PyTap README](../README.md)

PyTap isn't in the default HACS or Marketplace list, so add it as a custom repository. How you do that depends on your Home Assistant version: Home Assistant 2026.11 replaced HACS with the built-in **Marketplace**, and existing HACS installs move over automatically when you update.

## Home Assistant 2026.11 and later (Marketplace)

1. Go to **Settings → Marketplace**. The first time, accept the warning about community projects.
2. Select **Add from link** (top right), enter `DoubleDoc/pytap` and select **Add**. This needs a GitHub connection; follow the prompt to sign in with a code on GitHub.
3. Open **PyTap** under **Custom repositories** and select **Install**.
4. Restart Home Assistant if a repair asks you to, then add the integration as described under [Configuration](configuration.md).

Updates appear in the Marketplace and as an update entity. Restart Home Assistant after updating; a repair offers to do it for you.

## Home Assistant 2024.4 to 2026.10 (HACS, PyTap 1.4.x only)

PyTap 1.5 and later need Home Assistant 2026.11 (for serial connections). On older versions, HACS offers PyTap 1.4.x.

1. Open **HACS**, select the **three dots** menu (top right) and choose **Custom repositories**.
2. Add `https://github.com/DoubleDoc/pytap` with type **Integration**.
3. Search for **PyTap**, open it and select **Download**.
4. Restart Home Assistant, then add the integration as described under [Configuration](configuration.md).

Updates appear in HACS and as an update entity; restart Home Assistant after updating.

## Manual

1. Copy the `custom_components/pytap` folder from this repository into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant, then add the integration as described under [Configuration](configuration.md).

## Upgrading from 1.3.x

Version 1.4.0 renames devices (see [Device naming](entities.md#device-naming)): optimizers change from `Tigo TS4 <name>` to `Tigo <string> <name>`, and `Tigo Installation` becomes `Tigo Total`. Entity IDs don't change, so automations and dashboards keep working. If your module names include the string or inverter (for example `Master-2-P01`), use **Edit a module** to shorten them to the position (`P01`), and set an inverter name if you have more than one gateway.
## Removing the integration

1. Go to **Settings → Devices & Services → PyTap**, open each entry's menu (⋮) and select **Delete**.
2. Remove PyTap in the Marketplace or HACS (or delete `config/custom_components/pytap`), then restart Home Assistant.
