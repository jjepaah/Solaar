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
    ROW_ACTIVATED = "row-activated"


_dialogs: dict[Hashable, "OnboardButtonsDialog"] = {}


class _ButtonRow(Gtk.Box):
    """One row: slot label, current-assignment summary, capture/choose/clear buttons."""

    def __init__(self, index: int, button, on_capture, on_choose, on_clear) -> None:
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

        choose_btn = Gtk.Button(label=_("Choose from list…"))
        choose_btn.set_tooltip_text(_("Pick a key by name -- for keys this keyboard can't send (e.g. a numpad key)"))
        choose_btn.connect(GtkSignal.CLICKED.value, lambda _b: on_choose(self._index, self))
        self.pack_end(choose_btn, False, False, 0)

        capture_btn = Gtk.Button(label=_("Capture key…"))
        capture_btn.connect(GtkSignal.CLICKED.value, lambda _b: on_capture(self._index, self))
        self.pack_end(capture_btn, False, False, 0)

        clear_btn = Gtk.Button(label=_("Clear"))
        clear_btn.connect(GtkSignal.CLICKED.value, lambda _b: on_clear(self._index, self))
        self.pack_end(clear_btn, False, False, 0)

    def set_description(self, text: str) -> None:
        self._current.set_text(text)


class _KeyPickerDialog(Gtk.Dialog):
    """Modal "choose a key by name" dialog: the counterpart to capture-by-keypress.

    Capture can only ever offer keys the *current* keyboard can physically
    send. A numpad key on a keyboard with no numpad block, for example, can
    never arrive as a GDK key event no matter how complete the capture table
    is -- the only way in is picking it by name, with modifiers chosen
    separately via checkboxes instead of read off the live event state.
    """

    # Row height GTK gives a plain (non-headers) Gtk.TreeView, in pixels --
    # used to size the scroller to a fixed number of visible rows rather
    # than letting it size to fit every row (which is what made the old
    # Gtk.ComboBoxText popup span the whole monitor for a ~100-entry list).
    _ROW_HEIGHT_PX = 26
    _VISIBLE_ROWS = 9

    def __init__(self, parent: Gtk.Window, current_hid_code: int | None, current_modifiers: int) -> None:
        super().__init__(title=_("Choose a Key"), transient_for=parent, modal=True)
        self.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("OK"), Gtk.ResponseType.OK)
        self.set_default_size(280, 360)

        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(8)

        # A plain ComboBoxText's popup has no built-in max-height, so with
        # ~100 entries it opened spanning the full screen. A Gtk.TreeView
        # embedded directly in the dialog (in a height-capped, scrollable
        # window) behaves like an ordinary bounded dropdown list instead.
        self._store = Gtk.ListStore(str, int)  # display name, HID code
        selected_iter = None
        for name, hid_code in keymap.available_keys():
            row_iter = self._store.append([name, hid_code])
            if current_hid_code is not None and hid_code == current_hid_code:
                selected_iter = row_iter

        self._view = Gtk.TreeView(model=self._store)
        self._view.set_headers_visible(False)
        column = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
        self._view.append_column(column)
        self._view.connect(GtkSignal.ROW_ACTIVATED.value, self._on_row_activated)

        self._selection = self._view.get_selection()
        self._selection.set_mode(Gtk.SelectionMode.BROWSE)
        if selected_iter is None:
            selected_iter = self._store.get_iter_first()
        if selected_iter is not None:
            self._selection.select_iter(selected_iter)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(self._ROW_HEIGHT_PX * self._VISIBLE_ROWS)
        scroller.set_max_content_height(self._ROW_HEIGHT_PX * self._VISIBLE_ROWS)
        scroller.add(self._view)
        box.pack_start(scroller, True, True, 0)

        mod_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._ctrl = Gtk.CheckButton(label=_("Ctrl"))
        self._shift = Gtk.CheckButton(label=_("Shift"))
        self._alt = Gtk.CheckButton(label=_("Alt"))
        self._meta = Gtk.CheckButton(label=_("Meta"))
        self._ctrl.set_active(bool(current_modifiers & 0x01))
        self._shift.set_active(bool(current_modifiers & 0x02))
        self._alt.set_active(bool(current_modifiers & 0x04))
        self._meta.set_active(bool(current_modifiers & 0x08))
        for check in (self._ctrl, self._shift, self._alt, self._meta):
            mod_box.pack_start(check, False, False, 0)
        box.pack_start(mod_box, False, False, 0)

        self.show_all()
        if selected_iter is not None:
            path = self._store.get_path(selected_iter)
            # Center the current selection in the scroller instead of always
            # opening on row 0 -- most useful when re-editing an existing key.
            self._view.scroll_to_cell(path, None, True, 0.5, 0.0)

    def _on_row_activated(self, _view, _path, _column) -> None:
        # Double-clicking a row is the same as picking it and pressing OK.
        self.response(Gtk.ResponseType.OK)

    def result(self) -> tuple[int, int] | None:
        """The chosen (hid_code, modifiers), or None if nothing is selected."""
        _model, row_iter = self._selection.get_selected()
        if row_iter is None:
            return None
        hid_code = self._store.get_value(row_iter, 1)
        modifiers = 0
        if self._ctrl.get_active():
            modifiers |= 0x01
        if self._shift.get_active():
            modifiers |= 0x02
        if self._alt.get_active():
            modifiers |= 0x04
        if self._meta.get_active():
            modifiers |= 0x08
        return int(hid_code), modifiers


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
            row = _ButtonRow(index, value[index], self._start_capture, self._start_choose, self._clear)
            self._rows[index] = row
            self._listbox.add(row)
        self._listbox.show_all()

    def _start_capture(self, index: int, row: _ButtonRow) -> None:
        self._capturing_index = index
        row.set_description(_("Press a key… (Esc to cancel)"))
        if self._capture_overlay is not None:
            self._capture_overlay.set_text(_("Listening for a key press for Button {index}…").format(index=index + 1))

    def _start_choose(self, index: int, row: _ButtonRow) -> None:
        current_hid_code, current_modifiers = self._current_key_and_modifiers(index)
        picker = _KeyPickerDialog(self._window, current_hid_code, current_modifiers)
        try:
            response = picker.run()
            if response == Gtk.ResponseType.OK:
                result = picker.result()
                if result is not None:
                    hid_code, modifiers = result
                    button = keymap.manual_button(hid_code, modifiers)
                    row.set_description(keymap.describe(button))
                    self._write(index, button)
        finally:
            picker.destroy()

    def _current_key_and_modifiers(self, index: int) -> tuple[int | None, int]:
        """The (hid_code, modifiers) a button slot currently holds, if it's a
        plain key mapping -- used to pre-select the picker on an existing
        assignment rather than always opening on the first entry."""
        value = self._setting._value if self._setting is not None else None
        button = value.get(index) if value else None
        return keymap.key_and_modifiers(button)

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
