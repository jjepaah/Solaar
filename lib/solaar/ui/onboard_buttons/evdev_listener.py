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

"""Raw-input listener backing the "Identify Buttons" feature.

GTK's key-press-event is only delivered for keys that reach *this*
application's focused window, and that delivery is not guaranteed: a
desktop environment's global keyboard shortcuts (KDE's "Global Shortcuts",
GNOME Shell's, a plain window manager's own grabs, ...) intercept a
keypress before ordinary focused-window input delivery ever sees it. In
practice this showed up exactly the way it would be expected to: one probe
key (out of the F13-F24 range "Identify Buttons" assigns -- see keymap's
module docstring) worked, and most of the others silently vanished --
one of them turned out to be bound to a system-settings shortcut instead
of ever reaching the dialog.

Reading the kernel's raw evdev event stream instead sidesteps that
failure mode: every process that opens a ``/dev/input/eventN`` node gets
its own independent read of the events arriving there. A compositor
consuming one of those events for its own global shortcut does that
*above* the evdev layer (it just chooses not to act on it as ordinary text
input) -- it does not remove the event from the device's queue before any
other reader, including this one, also sees it. This module never
*injects* input (Solaar's existing evdev use, in
``logitech_receiver.diversion``, is output-only via ``evdev.uinput.UInput``
for its own "rules" feature) and never exclusively grabs a device
(``EVIOCGRAB``) -- it only listens, alongside whatever else is already
reading the same device.

This is a best-effort fallback path, not a guarantee: it needs
python-evdev installed (an existing but Linux-only, optional Solaar
dependency -- see ``setup.py``) and read access to the relevant
``/dev/input/eventN`` nodes (typically already granted to the graphical
session's user via logind/udev ACLs on a normal desktop, but not
universally). ``ProbeListener.start()`` returns False if either isn't
available, and the dialog falls back to plain key-press-event capture in
that case.
"""

from __future__ import annotations

import logging
import selectors
import threading

from typing import Callable

_log = logging.getLogger(__name__)

try:
    import evdev
except ImportError:  # pragma: no cover - evdev is Linux-only; see setup.py
    evdev = None


class ProbeListener:
    """Watches every readable keyboard-capable evdev device for key-down
    events matching a set of probe keycodes, reporting matches through a
    callback until stopped.

    Runs its own background thread, since evdev reads block; the callback
    therefore fires off the GTK main thread and must marshal itself back
    (e.g. via ``GLib.idle_add``) before touching any GTK widget -- the
    caller's job, not this class's, since this module has no GTK
    dependency at all.
    """

    def __init__(self, evdev_code_to_index: dict[int, int], on_match: Callable[[int], None]) -> None:
        self._code_to_index = dict(evdev_code_to_index)
        self._on_match = on_match
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._devices: list = []

    @staticmethod
    def is_available() -> bool:
        return evdev is not None

    def start(self) -> bool:
        """Try to start listening. Returns False (leaving nothing running)
        if evdev isn't installed, no keyboard-capable device could be
        opened, or none of them report any of the requested keycodes --
        the caller should fall back to key-press-event capture in that
        case rather than assume this path is always usable."""
        if evdev is None or not self._code_to_index:
            return False
        candidates = []
        try:
            for path in evdev.list_devices():
                try:
                    device = evdev.InputDevice(path)
                except OSError:
                    continue
                capabilities = device.capabilities().get(evdev.ecodes.EV_KEY, [])
                if any(code in capabilities for code in self._code_to_index):
                    candidates.append(device)
                else:
                    device.close()
        except OSError as e:
            _log.warning("identify: could not enumerate input devices for raw probing: %s", e)
            for device in candidates:
                device.close()
            return False
        if not candidates:
            return False
        self._devices = candidates
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="solaar-identify-probe", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        selector = selectors.DefaultSelector()
        for device in self._devices:
            try:
                selector.register(device.fd, selectors.EVENT_READ, device)
            except (OSError, ValueError):
                pass
        try:
            while not self._stop_event.is_set():
                for key, _mask in selector.select(timeout=0.2):
                    device = key.data
                    try:
                        for event in device.read():
                            if event.type != evdev.ecodes.EV_KEY or event.value != 1:  # key-down transitions only
                                continue
                            index = self._code_to_index.get(event.code)
                            if index is not None:
                                self._on_match(index)
                    except (OSError, BlockingIOError):
                        continue
        except Exception:  # pragma: no cover - defensive: a probe thread must never crash silently
            _log.exception("identify: raw-input probe listener stopped unexpectedly")
        finally:
            selector.close()
            for device in self._devices:
                try:
                    device.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._devices = []
