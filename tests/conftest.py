"""Fixtures for PyTap tests."""


import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture(autouse=True)
def cancel_coordinator_timers():
    """Cancel timers left by coordinators that tests create directly.

    Tests often drive a coordinator's handlers without setting up and
    unloading an entry, which leaves its 10-second save timer (and sometimes
    the midnight reset) pending. Home Assistant's test plugin fails a test
    that ends with a lingering timer, so cancel them at teardown.
    """
    from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator

    created = []
    original_init = PyTapDataUpdateCoordinator.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    PyTapDataUpdateCoordinator.__init__ = tracking_init
    try:
        yield
    finally:
        PyTapDataUpdateCoordinator.__init__ = original_init
        for coordinator in created:
            for attr in ("_save_task", "_midnight_reset_unsub"):
                handle = getattr(coordinator, attr, None)
                if handle is None:
                    continue
                if hasattr(handle, "cancel"):
                    handle.cancel()  # a loop timer (pending save)
                elif callable(handle):
                    handle()  # an HA listener's remove callback (midnight reset)
            # A save request queued from the listener thread may still run
            # after this; with a save "already pending" it starts no timer.
            coordinator._save_task = _NoTimer()
            coordinator._midnight_reset_unsub = None


class _NoTimer:
    """Stands in for a pending save timer after a test ends."""

    def cancel(self) -> None:
        """Nothing to cancel."""
