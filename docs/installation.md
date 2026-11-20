# Installation

[← PyTap README](../README.md)

PyTap needs **Home Assistant 2026.11 or later**. It installs from Home Assistant's built-in **Marketplace** as a custom repository, since it isn't in the Marketplace's default list.

## Marketplace

1. Go to **Settings → Marketplace**. The first time, accept the warning about community projects.
2. Select **Add from link** (top right), enter `DoubleDoc/pytap` and select **Add**. This needs a GitHub connection; follow the prompt to sign in with a code on GitHub.
3. Open **PyTap** under **Custom repositories** and select **Install**.
4. Restart Home Assistant if a repair asks you to, then add the integration as described under [Configuration](configuration.md).

Updates appear in the Marketplace and as an update entity. Restart Home Assistant after updating; a repair offers to do it for you.

## Manual

1. Copy the `custom_components/pytap` folder from this repository into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant, then add the integration as described under [Configuration](configuration.md).

## Upgrading from 1.3.x

Version 1.4.0 renames devices (see [Device naming](entities.md#device-naming)): optimizers change from `Tigo TS4 <name>` to `Tigo <string> <name>`, and `Tigo Installation` becomes `Tigo Total`. Entity IDs don't change, so automations and dashboards keep working. If your module names include the string or inverter (for example `Master-2-P01`), use **Edit a module** to shorten them to the position (`P01`), and set an inverter name if you have more than one gateway.

## Removing the integration

1. Go to **Settings → Devices & Services → PyTap**, open each entry's menu (⋮) and select **Delete**.
2. Remove PyTap in the Marketplace (or delete `config/custom_components/pytap` if you installed it manually), then restart Home Assistant.
