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

"""Translate a captured GDK key event into a Solaar onboard-profile Button.

Onboard profiles store button assignments as USB HID usage codes
(``logitech_receiver.special_keys.USB_HID_KEYCODES``), the same table used
for HID++ diversion rules. GDK key events carry an X11 keysym (``keyval``)
instead, so a widget that lets someone "press a key to assign it" needs a
keyval -> HID usage translation. There is no existing table for the
specific subset of keys HID++ onboard profiles can express, so this module
hand-maps the keys people actually rebind: letters, digits, the function
row including F13-F24, the numpad (both NumLock-on and NumLock-off keyvals,
since we can't assume the live NumLock state while capturing), navigation
and editing keys, and the standard modifiers.

Anything not in the resulting table (dead keys, IME composition keys, media
keys not in the consumer-key table, ...) is reported as unsupported by
``capture()`` rather than silently mapped to something wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # NOQA: E402
from logitech_receiver import hidpp20  # NOQA: E402
from logitech_receiver import special_keys  # NOQA: E402

from solaar.i18n import _  # NOQA: E402

# GDK keyval -> USB HID usage code (logitech_receiver.special_keys.USB_HID_KEYCODES).
_KEYVAL_TO_HID: dict[int, int] = {}

_HID_NAME_TO_CODE: dict[str, int] = {str(k): int(k) for k in special_keys.USB_HID_KEYCODES}


def _map_name(gdk_attr: str, hid_name: str) -> None:
    keyval = getattr(Gdk, gdk_attr, None)
    hid_code = _HID_NAME_TO_CODE.get(hid_name)
    if keyval is not None and hid_code is not None:
        _KEYVAL_TO_HID[keyval] = hid_code


for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _map_name(f"KEY_{_letter.lower()}", _letter)

# The top-row digit keys: HID usage codes 0x1E-0x27 (1,2,...,9,0). Only 0x1E/0x1F
# are given string names ("1"/"2") in special_keys.py; the rest resolve by raw code.
_DIGIT_HID_CODE = {
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
}
for _digit, _code in _DIGIT_HID_CODE.items():
    _keyval = getattr(Gdk, f"KEY_{_digit}", None)
    if _keyval is not None:
        _KEYVAL_TO_HID[_keyval] = _code

for _n in range(1, 25):  # F1..F24
    _map_name(f"KEY_F{_n}", f"F{_n}")

_NAMED_KEYS = {
    "KEY_Return": "ENTER",
    "KEY_Escape": "ESC",
    "KEY_BackSpace": "BACKSPACE",
    "KEY_Tab": "TAB",
    "KEY_space": "SPACE",
    "KEY_Caps_Lock": "CAPSLOCK",
    "KEY_Insert": "INSERT",
    "KEY_Delete": "DELETE",
    "KEY_Home": "HOME",
    "KEY_End": "END",
    "KEY_Page_Up": "PAGEUP",
    "KEY_Page_Down": "PAGEDOWN",
    "KEY_Up": "UP",
    "KEY_Down": "DOWN",
    "KEY_Left": "LEFT",
    "KEY_Right": "RIGHT",
    "KEY_Num_Lock": "NUMLOCK",
    "KEY_Scroll_Lock": "SCROLLLOCK",
    "KEY_Pause": "PAUSE",
    "KEY_Menu": "COMPOSE",
    "KEY_KP_Divide": "KPSLASH",
    "KEY_KP_Multiply": "KPASTERISK",
    "KEY_KP_Subtract": "KPMINUS",
    "KEY_KP_Add": "KPPLUS",
    "KEY_KP_Enter": "KPENTER",
    "KEY_KP_Decimal": "KPDOT",
    "KEY_KP_Equal": "KPEQUAL",
}
for _attr, _hid_name in _NAMED_KEYS.items():
    _map_name(_attr, _hid_name)

# Numpad digits: GDK reports a "KP_1".."KP_9"/"KP_0" keyval with NumLock on, and a
# navigation-key keyval (KP_End, KP_Down, ...) with NumLock off for the same physical
# key. Map both spellings to the same HID keypad code so capture works either way.
_KP_DIGIT_ALIASES = {
    "0": ("KP_0", "KP_Insert"),
    "1": ("KP_1", "KP_End"),
    "2": ("KP_2", "KP_Down"),
    "3": ("KP_3", "KP_Page_Down"),
    "4": ("KP_4", "KP_Left"),
    "5": ("KP_5", "KP_Begin"),
    "6": ("KP_6", "KP_Right"),
    "7": ("KP_7", "KP_Home"),
    "8": ("KP_8", "KP_Up"),
    "9": ("KP_9", "KP_Page_Up"),
}
_KP_HID_NAME = {d: f"KP{d}" for d in _KP_DIGIT_ALIASES}
for _digit, _attrs in _KP_DIGIT_ALIASES.items():
    for _attr in _attrs:
        _map_name(f"KEY_{_attr}", _KP_HID_NAME[_digit])

# GDK modifier bits -> the modifier byte Button/hidpp20 expects
# (Ctrl=0x01, Shift=0x02, Alt=0x04, Meta/Super=0x08 -- see special_keys.modifiers).
_MODIFIER_BITS = (
    (Gdk.ModifierType.CONTROL_MASK, 0x01),
    (Gdk.ModifierType.SHIFT_MASK, 0x02),
    (Gdk.ModifierType.MOD1_MASK, 0x04),  # Alt
    (Gdk.ModifierType.SUPER_MASK, 0x08),  # Meta / Super / Windows key
)


@dataclass(frozen=True)
class CapturedKey:
    """A resolved key capture, ready to become an onboard-profile Button."""

    hid_code: int
    modifiers: int
    display_name: str


def capture(keyval: int, state) -> CapturedKey | None:
    """Resolve a GDK (keyval, modifier state) pair to a CapturedKey.

    Returns None if the key isn't one onboard profiles can express (see
    module docstring) -- the caller should ask for a different key rather
    than silently discarding the press.
    """
    hid_code = _KEYVAL_TO_HID.get(keyval)
    if hid_code is None:
        return None
    modifiers = 0
    for bit, value in _MODIFIER_BITS:
        if state & bit:
            modifiers |= value
    key_name = Gdk.keyval_name(keyval) or str(hid_code)
    return CapturedKey(hid_code=hid_code, modifiers=modifiers, display_name=_format_display(key_name, modifiers))


def _format_display(key_name: str, modifiers: int) -> str:
    prefix = special_keys.modifiers.get(modifiers, "")
    return f"{prefix}{key_name}"


def to_button(captured: CapturedKey) -> hidpp20.Button:
    """Build the onboard-profile Button entry for a captured key."""
    return hidpp20.Button(
        behavior=int(hidpp20.ButtonBehavior.SEND),
        type=int(hidpp20.ButtonMappingType.MODIFIER_AND_KEY),
        modifiers=captured.modifiers,
        value=captured.hid_code,
    )


def unassigned_button() -> hidpp20.Button:
    """The device's native "no action" marker (behavior 15, raw 0xFFFFFFFF)."""
    return hidpp20.Button(behavior=15, bytes=b"\xff\xff\xff\xff")


def describe(button: hidpp20.Button | None) -> str:
    """Human-readable summary of a button's current assignment, for its row label."""
    behavior = getattr(button, "behavior", None) if button is not None else None
    if behavior is None or behavior == 15:
        return _("(unassigned)")
    is_key_send = behavior == int(hidpp20.ButtonBehavior.SEND)
    is_key_send = is_key_send and getattr(button, "type", None) == int(hidpp20.ButtonMappingType.MODIFIER_AND_KEY)
    if is_key_send:
        hid_code = getattr(button, "value", None)
        if hid_code in special_keys.USB_HID_KEYCODES:
            key_name = str(special_keys.USB_HID_KEYCODES[hid_code])
        else:
            key_name = str(hid_code)
        return _format_display(key_name, getattr(button, "modifiers", 0) or 0)
    # Mouse buttons, consumer keys, and device functions are out of scope for
    # this editor (v1) -- show that something is set without offering to edit it.
    return _("(set via CLI -- not a plain key mapping)")
