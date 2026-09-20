## Copyright (C) 2026  Solaar Contributors https://pwr-solaar.github.io/Solaar/
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License along
## with this program; if not, write to the Free Software Foundation, Inc.,
## 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""Unit tests for evdev_listener.ProbeListener, the raw-input reader behind
"Identify Buttons" on systems where a desktop keyboard shortcut would
otherwise intercept an F13-F24 probe key before it reaches the dialog as a
GTK key-press-event (see keymap's and evdev_listener's module docstrings).

These use the real `evdev` module (an existing Solaar dependency on
Linux) for its ecodes constants and exception types, but stand in fake
InputDevice objects backed by an os.pipe() so the selector/read loop is
exercised for real without needing an actual input device node or a
running compositor to race against.
"""

from __future__ import annotations

import collections
import os
import threading
import time

import pytest

evdev = pytest.importorskip("evdev")

from solaar.ui.onboard_buttons import evdev_listener  # noqa: E402
from solaar.ui.onboard_buttons.evdev_listener import ProbeListener  # noqa: E402

_FakeEvent = collections.namedtuple("_FakeEvent", ["type", "code", "value"])


class _FakeInputDevice:
    """Stands in for evdev.InputDevice: a real pipe fd (so selector.select()
    behaves exactly as it would against a real /dev/input/eventN node), with
    hand-fed EV_KEY events instead of a real kernel-driven event queue."""

    def __init__(self, path: str, capable_codes: list[int]) -> None:
        self.path = path
        self.fd, self._write_fd = os.pipe()
        self._capable_codes = capable_codes
        self._pending: list[_FakeEvent] = []
        self.closed = False

    def capabilities(self):
        return {evdev.ecodes.EV_KEY: self._capable_codes}

    def push_key_down(self, code: int) -> None:
        self._pending.append(_FakeEvent(evdev.ecodes.EV_KEY, code, 1))
        os.write(self._write_fd, b"x")

    def read(self):
        os.read(self.fd, 1)
        events, self._pending = self._pending, []
        return events

    def close(self) -> None:
        self.closed = True
        os.close(self.fd)
        os.close(self._write_fd)


@pytest.fixture
def fake_devices(monkeypatch):
    """Patches evdev.list_devices()/InputDevice() to return fake devices
    instead of enumerating real ones, and cleans up afterwards regardless
    of whether the test closed them all itself."""
    devices: list[_FakeInputDevice] = []

    def make(path: str, capable_codes: list[int]) -> _FakeInputDevice:
        device = _FakeInputDevice(path, capable_codes)
        devices.append(device)
        return device

    monkeypatch.setattr(evdev, "list_devices", lambda: [d.path for d in devices])
    by_path = {}
    monkeypatch.setattr(evdev, "InputDevice", lambda path: by_path[path])

    def register(device: _FakeInputDevice) -> _FakeInputDevice:
        by_path[device.path] = device
        return device

    make.register = register  # type: ignore[attr-defined]
    yield make
    for device in devices:
        if not device.closed:
            device.close()


def test_start_opens_only_devices_that_advertise_a_probe_keycode(fake_devices):
    keyboard = fake_devices.register(fake_devices("/dev/input/event0", [evdev.ecodes.KEY_A, evdev.ecodes.KEY_B]))
    mouse_probe_device = fake_devices.register(fake_devices("/dev/input/event1", [183, 184, 185]))

    listener = ProbeListener({183: 0}, on_match=lambda index: None)
    try:
        assert listener.start() is True
        # The device with none of our probe codes must be closed immediately
        # rather than left open and selected on for no reason.
        assert keyboard.closed is True
        assert mouse_probe_device.closed is False
    finally:
        listener.stop()


def test_start_returns_false_when_no_device_advertises_a_probe_keycode(fake_devices):
    fake_devices.register(fake_devices("/dev/input/event0", [evdev.ecodes.KEY_A]))

    listener = ProbeListener({183: 0}, on_match=lambda index: None)
    assert listener.start() is False


def test_start_returns_false_when_evdev_is_unavailable(monkeypatch):
    monkeypatch.setattr(evdev_listener, "evdev", None)
    assert ProbeListener.is_available() is False
    assert ProbeListener({183: 0}, on_match=lambda index: None).start() is False


def test_matches_key_down_events_regardless_of_which_device_reports_them(fake_devices):
    # This is the scenario the feature exists for: a desktop-level global
    # shortcut can swallow a probe key before it ever becomes a GTK
    # key-press-event, but the raw kernel event still arrives here exactly
    # the same as any other key-down -- there's nothing for a compositor's
    # shortcut handling to intercept at this layer.
    device = fake_devices.register(fake_devices("/dev/input/event0", [183, 184, 185]))

    matches: list[int] = []
    lock = threading.Lock()

    def on_match(index: int) -> None:
        with lock:
            matches.append(index)

    listener = ProbeListener({183: 10, 184: 11, 185: 12}, on_match=on_match)
    try:
        assert listener.start() is True
        device.push_key_down(183)
        device.push_key_down(185)
        _wait_until(lambda: len(matches) == 2)
        with lock:
            assert matches == [10, 12]
    finally:
        listener.stop()


def test_ignores_key_up_events_and_unrelated_keycodes(fake_devices):
    device = fake_devices.register(fake_devices("/dev/input/event0", [183]))

    matches: list[int] = []
    listener = ProbeListener({183: 0}, on_match=matches.append)
    try:
        assert listener.start() is True
        # A key-up (value=0) for a probe code, and a key-down for something
        # else entirely, must not be reported as a match.
        device._pending.append(_FakeEvent(evdev.ecodes.EV_KEY, 183, 0))
        os.write(device._write_fd, b"x")
        device.push_key_down(evdev.ecodes.KEY_A)
        time.sleep(0.3)
        assert matches == []
    finally:
        listener.stop()


def test_stop_closes_devices_and_joins_the_background_thread(fake_devices):
    device = fake_devices.register(fake_devices("/dev/input/event0", [183]))

    listener = ProbeListener({183: 0}, on_match=lambda index: None)
    assert listener.start() is True
    listener.stop()

    assert device.closed is True
    assert listener._thread is None


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "condition was never met within the timeout"
