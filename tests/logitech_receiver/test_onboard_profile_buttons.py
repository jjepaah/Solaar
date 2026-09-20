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

"""Unit tests for settings_templates.OnboardProfileButtons's profile/sector
selection logic.

These deliberately avoid the fake_hidpp transcript machinery used elsewhere
in this file's siblings: `_active_sector`/`_active_profile` are pure
selection logic over already-decoded objects, so plain stand-ins for the
device/setting/profile collaborators are simpler and just as faithful.
Round-tripping actual bytes through `hidpp20.OnboardProfiles.from_device` /
`.write()` is exercised by the CLI's own tests, not duplicated here.
"""

from __future__ import annotations

from types import SimpleNamespace

from logitech_receiver import settings_templates


def _make_profile(sector: int, buttons: list):
    return SimpleNamespace(sector=sector, buttons=buttons)


def _make_profiles(*profiles):
    button_count = len(profiles[0].buttons) if profiles else 0
    return SimpleNamespace(buttons=button_count, profiles={i + 1: p for i, p in enumerate(profiles)})


def _make_onboard_profile_buttons(active_sector_value):
    """Build an OnboardProfileButtons instance without going through
    Setting.build(), which would need a real device/feature handshake."""
    setting = settings_templates.OnboardProfileButtons.__new__(settings_templates.OnboardProfileButtons)
    onboard_profiles_setting = SimpleNamespace(name="onboard_profiles", _value=active_sector_value)
    setting._device = SimpleNamespace(settings=[onboard_profiles_setting], online=True, persister=None)
    setting._value = None
    return setting


def test_active_sector_defaults_to_1_when_onboard_profiles_disabled():
    # OnboardProfiles setting reports 0 ("Disabled") when the device is in host mode.
    setting = _make_onboard_profile_buttons(active_sector_value=0)
    assert setting._active_sector() == 1


def test_active_sector_defaults_to_1_when_onboard_profiles_setting_missing():
    setting = _make_onboard_profile_buttons(active_sector_value=0)
    setting._device.settings = []  # device has no onboard_profiles setting at all
    assert setting._active_sector() == 1


def test_active_sector_follows_the_active_profile():
    setting = _make_onboard_profile_buttons(active_sector_value=2)
    assert setting._active_sector() == 2


def test_active_profile_matches_by_sector_not_by_dict_key():
    # profiles.profiles is keyed by a running index (1, 2, 3, ...) from the
    # header scan order, which is NOT guaranteed to equal the sector number
    # a firmware reports -- selection must match on `.sector`, not the key.
    profile_a = _make_profile(sector=5, buttons=["A"])
    profile_b = _make_profile(sector=2, buttons=["B"])
    profiles = _make_profiles(profile_a, profile_b)  # dict keys become 1, 2 regardless of .sector

    setting = _make_onboard_profile_buttons(active_sector_value=5)
    assert setting._active_profile(profiles) is profile_a

    setting = _make_onboard_profile_buttons(active_sector_value=2)
    assert setting._active_profile(profiles) is profile_b


def test_active_profile_falls_back_to_first_when_sector_not_found():
    profile_a = _make_profile(sector=1, buttons=["A"])
    profiles = _make_profiles(profile_a)

    # Onboard Profiles setting points at a sector this device doesn't have
    # (shouldn't normally happen, but must not raise) -- fall back rather
    # than leaving the editor with nothing to show.
    setting = _make_onboard_profile_buttons(active_sector_value=9)
    assert setting._active_profile(profiles) is profile_a


def test_read_populates_value_from_active_profile_buttons():
    profile = _make_profile(sector=1, buttons=["left", "right", "middle"])
    profiles = _make_profiles(profile)
    setting = _make_onboard_profile_buttons(active_sector_value=1)
    setting._device.online = True

    import unittest.mock as mock

    with mock.patch("logitech_receiver.hidpp20.OnboardProfiles.from_device", return_value=profiles):
        value = setting.read(cached=False)

    assert value == {0: "left", 1: "right", 2: "middle"}
    assert setting._profiles is profiles


def test_write_key_value_patches_only_the_target_button_and_writes_back():
    profile = _make_profile(sector=1, buttons=["left", "right", "middle"])
    profiles = _make_profiles(profile)
    profiles.write = lambda device: 1  # stand-in for the real sector write

    setting = _make_onboard_profile_buttons(active_sector_value=1)
    setting._value = {0: "left", 1: "right", 2: "middle"}
    setting._profiles = profiles

    result = setting.write_key_value(1, "NEW-RIGHT")

    assert result == "NEW-RIGHT"
    assert profile.buttons[1] == "NEW-RIGHT"
    assert profile.buttons[0] == "left"  # untouched
    assert setting._value[1] == "NEW-RIGHT"
