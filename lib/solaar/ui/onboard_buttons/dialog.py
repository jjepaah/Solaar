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

"""Per-device dialog for assigning keyboard keys to onboard-profile buttons.

One dialog instance is kept per device key, same pattern as
``solaar.ui.perkey.dialog`` -- see that module for why a firmware unit-id
is preferred as the key.

v1 is a plain list (one row per button slot), not the spatial canvas the
per-key RGB editor uses: onboard profiles don't have a per-device physical
layout defined anywhere in Solaar yet (the per-key layouts describe LED
zones, not button positions), and a numbered list gets someone productive
today. A future version could grow a visual layout the same way the RGB
editor did, reusing ``solaar.ui.perkey.layout``.
"""

from __future__ import annotations

import logging

from enum import Enum
from typing import Hashable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk  # NOQA: E402
from gi.repository import Gtk  # NOQA: E402

from solaar.i18n import _  # NOQA: E402

from . import keymap  # NOQA: E402

logger = logging.getLogger(__name__)

_MODIFIER_KEYVALS = (
    Gdk.KEY_Control_L,
    Gdk.KEY_Control_R,
    Gdk.KEY_Shift_L,
    Gdk.KEY_Shift_R,
    Gdk.KEY_Alt_L,
    Gdk.KEY_Alt_R,
    Gdk.KEY_Super_L,
    Gdk.KEY_Super_R,
)


class GtkSignal(Enum):
    DELETE_EVENT = "delete-event"
    CLICKED = "clicked"
    KEY_PRESS_EVENT = "key-press-event"


_dialogs: dict[Hashable, "OnboardButtonsDialog"] = {}


class _ButtonRow(Gtk.Box):
    """One row: slot label, current-assignment summary, capture + clear buttons."""

    def __init__(self, index: int, button, on_capture, on_clear) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._index = index
        self.set_border_width(4)

        label = Gtk.Label(label=_("Button {index}").format(index=index + 1))
        label.set_xalign(0.0)
        label.set_size_request(90, -1)
        self.pack_start(label, False, False, 0)

        self._current = Gtk.Label(label=keymap.describe(button))
        self._current.set_xalign(0.0)
        self.pack_start(self._current, True, True, 0)

        capture_btn = Gtk.Button(label=_("Capture key…"))
        capture_btn.connect(GtkSignal.CLICKED.value, lambda _b: on_capture(self._index, self))
        self.pack_end(capture_btn, False, False, 0)

        clear_btn = Gtk.Button(label=_("Clear"))
        clear_btn.connect(GtkSignal.CLICKED.value, lambda _b: on_clear(self._index, self))
        self.pack_end(clear_btn, False, False, 0)

    def set_description(self, text: str) -> None:
        self._current.set_text(text)


class OnboardButtonsDialog:
    def __init__(self, key: Hashable) -> None:
        self._key = key
        self._window: Gtk.Window | None = None
        self._listbox: Gtk.ListBox | None = None
        self._capture_overlay: Gtk.Label | None = None
        self._setting = None
        self._sbox = None
        self._rows: dict[int, _ButtonRow] = {}
        self._capturing_index: int | None = None

    def _on_delete(self, _w, _e) -> bool:
        self._destroy()
        _dialogs.pop(self._key, None)
        return True

    def _destroy(self) -> None:
        if self._window is not None:
            self._window.destroy()
        self._window = None
        self._listbox = None
        self._capture_overlay = None
        self._setting = None
        self._sbox = None
        self._rows = {}
        self._capturing_index = None

    def present(self, setting, sbox) -> None:
        if self._window is not None and self._setting is setting:
            self._reload()
            self._window.present()
            return
        self._destroy()
        self._setting = setting
        self._sbox = sbox
        device = getattr(setting, "_device", None)
        title = getattr(device, "name", None) or getattr(device, "codename", None) or ""

        self._window = Gtk.Window()
        self._window.set_title(_("Onboard Profile Buttons") + " — " + title)
        self._window.set_default_size(440, 340)
        self._window.connect(GtkSignal.DELETE_EVENT.value, self._on_delete)
        # Capture the raw key event at the window level rather than an entry
        # per row -- lets Escape cancel a capture cleanly and keeps a bare
        # modifier press (Ctrl, Shift, ...) from resolving before the real
        # key it's meant to combine with arrives.
        self._window.connect(GtkSignal.KEY_PRESS_EVENT.value, self._on_key_press)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(8)
        self._window.add(outer)

        info = Gtk.Label(
            label=_(
                "Click “Capture key”, then press the key you want that button to send. "
                "Mouse-button, media-key, and device-function assignments (DPI cycle, profile switch, "
                "G-Shift, ...) aren’t editable here — use ‘solaar profiles’ on the command line for those."
            )
        )
        info.set_line_wrap(True)
        info.set_xalign(0.0)
        outer.pack_start(info, False, False, 0)

        self._capture_overlay = Gtk.Label(label="")
        self._capture_overlay.set_xalign(0.0)
        outer.pack_start(self._capture_overlay, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self._listbox)
        outer.pack_start(scroller, True, True, 0)

        self._reload()
        outer.show_all()
        self._window.present()

    def _reload(self) -> None:
        if self._setting is None or self._listbox is None:
            return
        value = self._setting._value if self._setting._value else self._setting.read(cached=False)
        for child in list(self._listbox.get_children()):
            self._listbox.remove(child)
        self._rows = {}
        if not value:
            return
        for index in sorted(value):
            row = _ButtonRow(index, value[index], self._start_capture, self._clear)
            self._rows[index] = row
            self._listbox.add(row)
        self._listbox.show_all()

    def _start_capture(self, index: int, row: _ButtonRow) -> None:
        self._capturing_index = index
        row.set_description(_("Press a key… (Esc to cancel)"))
        if self._capture_overlay is not None:
            self._capture_overlay.set_text(_("Listening for a key press for Button {index}…").format(index=index + 1))

    def _clear(self, index: int, row: _ButtonRow) -> None:
        button = keymap.unassigned_button()
        row.set_description(keymap.describe(button))
        self._write(index, button)

    def _on_key_press(self, _widget, event) -> bool:
        if self._capturing_index is None:
            return False
        index = self._capturing_index
        row = self._rows.get(index)

        if event.keyval == Gdk.KEY_Escape:
            self._cancel_capture(index, row)
            return True
        if event.keyval in _MODIFIER_KEYVALS:
            return True  # wait for the real key this modifier is combined with

        captured = keymap.capture(event.keyval, event.state)
        self._capturing_index = None
        if self._capture_overlay is not None:
            self._capture_overlay.set_text("")
        if captured is None:
            if row is not None:
                row.set_description(_("That key can’t be assigned here — try another"))
            return True

        button = keymap.to_button(captured)
        if row is not None:
            row.set_description(keymap.describe(button))
        self._write(index, button)
        return True

    def _cancel_capture(self, index: int, row: _ButtonRow | None) -> None:
        self._capturing_index = None
        if self._capture_overlay is not None:
            self._capture_overlay.set_text("")
        if row is not None and self._setting is not None and self._setting._value:
            row.set_description(keymap.describe(self._setting._value.get(index)))

    def _write(self, index: int, button) -> None:
        if self._setting is None:
            return
        # Lazy import: config_panel imports settings/UI machinery that would
        # otherwise create a circular import with this package at load time.
        from solaar.ui.config_panel import _write_async

        _write_async(self._setting, button, self._sbox, key=index)


def get_dialog(key: Hashable) -> OnboardButtonsDialog:
    """Return the dialog for `key`, creating one if none is open."""
    d = _dialogs.get(key)
    if d is None:
        d = OnboardButtonsDialog(key)
        _dialogs[key] = d
    return d
