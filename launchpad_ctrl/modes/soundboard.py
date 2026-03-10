"""
Soundboard Mode - Each pad plays a mapped sound file.
Retriggerable (spam-friendly), with configurable colors per pad.
Supports multiple banks/pages via right column buttons.
"""

import os
from typing import Dict, List, Optional, Set
from launchpad_ctrl.modes import BaseMode
from launchpad_ctrl.core import LPColor


class PadConfig:
    """Configuration for a single pad."""

    def __init__(self, filepath: str = "", color: str = "green",
                 volume: float = 0.8, label: str = ""):
        self.filepath = filepath
        self.color = color
        self.volume = volume
        self.label = label

    def to_dict(self) -> dict:
        return {
            "filepath": self.filepath,
            "color": self.color,
            "volume": self.volume,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PadConfig":
        return cls(
            filepath=d.get("filepath", ""),
            color=d.get("color", "green"),
            volume=d.get("volume", 0.8),
            label=d.get("label", ""),
        )


class SoundboardMode(BaseMode):
    NAME = "Soundboard"
    DESCRIPTION = "Trigger sounds with configurable pads"
    COLOR = "amber"

    def __init__(self, midi, audio):
        super().__init__(midi, audio)
        # Multiple banks (pages), each is 8x8 grid of PadConfig
        self._banks: List[Dict[tuple, PadConfig]] = [{}]
        self._current_bank = 0
        self._active_playbacks: Dict[int, int] = {}  # instance_id -> (row, col) hash
        self._playing_pads: Set[tuple] = set()

    @property
    def current_bank(self):
        return self._current_bank

    @property
    def num_banks(self):
        return len(self._banks)

    def add_bank(self):
        """Add a new empty bank."""
        self._banks.append({})

    def switch_bank(self, index: int):
        """Switch to a different bank."""
        if 0 <= index < len(self._banks):
            self._current_bank = index
            self.refresh_leds()
            self._update_right_column()
            self.notify_ui()

    def set_pad(self, row: int, col: int, config: PadConfig, bank: Optional[int] = None):
        """Configure a pad."""
        b = bank if bank is not None else self._current_bank
        if 0 <= b < len(self._banks):
            self._banks[b][(row, col)] = config
            if b == self._current_bank and self._active:
                self._update_led(row, col)

    def get_pad(self, row: int, col: int, bank: Optional[int] = None) -> Optional[PadConfig]:
        b = bank if bank is not None else self._current_bank
        if 0 <= b < len(self._banks):
            return self._banks[b].get((row, col))
        return None

    def remove_pad(self, row: int, col: int, bank: Optional[int] = None):
        b = bank if bank is not None else self._current_bank
        if 0 <= b < len(self._banks):
            self._banks[b].pop((row, col), None)
            if b == self._current_bank and self._active:
                self._update_led(row, col)

    def on_grid_press(self, row: int, col: int):
        pad = self.get_pad(row, col)
        if pad and pad.filepath and os.path.exists(pad.filepath):
            # Retrigger: start a new playback instance
            instance_id = self.audio.play_sound(pad.filepath, volume=pad.volume)
            if instance_id is not None:
                self._active_playbacks[instance_id] = (row, col)
                self._playing_pads.add((row, col))
                self._update_led(row, col)
                self.notify_ui()

    def on_grid_release(self, row: int, col: int):
        pass  # No action on release for retrigger mode

    def on_control_press(self, position: str, index: int):
        if position == "right":
            # Bank switching
            if index < len(self._banks):
                self.switch_bank(index)
            elif index == len(self._banks):
                # Create new bank if pressing one past the last
                self.add_bank()
                self.switch_bank(index)

    def stop_all_sounds(self):
        """Stop all playing sounds (panic)."""
        self.audio.stop_all()
        self._active_playbacks.clear()
        self._playing_pads.clear()
        self.refresh_leds()
        self.notify_ui()

    def _update_led(self, row: int, col: int):
        pad = self.get_pad(row, col)
        if pad and pad.filepath:
            if (row, col) in self._playing_pads:
                # Bright version when playing
                color = LPColor.get(pad.color) or LPColor.GREEN
            else:
                color = LPColor.get(pad.color) or LPColor.GREEN_LOW
        else:
            color = LPColor.OFF

        self._grid_state[row][col] = color
        self.midi.set_led(row, col, color)

    def _update_right_column(self):
        """Show bank indicators on right column."""
        for i in range(8):
            if i < len(self._banks):
                color = LPColor.AMBER if i == self._current_bank else LPColor.AMBER_LOW
            else:
                color = LPColor.OFF
            self.midi.set_control_led("right", i, color)

    def refresh_leds(self):
        for row in range(8):
            for col in range(8):
                self._update_led(row, col)
        self._update_right_column()

    def activate(self):
        super().activate()
        self._update_right_column()

    def tick(self, dt: float):
        """Check for finished playbacks and update LED state."""
        dead = []
        for iid in list(self._active_playbacks.keys()):
            # Check if the playback instance is still in the audio engine
            with self.audio._lock:
                if iid not in self.audio._playback_instances:
                    dead.append(iid)

        if dead:
            for iid in dead:
                pos = self._active_playbacks.pop(iid, None)
                if pos:
                    self._playing_pads.discard(pos)
                    if self._active:
                        self._update_led(pos[0], pos[1])
            self.notify_ui()

    # --- Config ---

    def get_config(self) -> dict:
        banks = []
        for bank in self._banks:
            bank_data = {}
            for (row, col), pad in bank.items():
                bank_data[f"{row},{col}"] = pad.to_dict()
            banks.append(bank_data)
        return {
            "banks": banks,
            "current_bank": self._current_bank,
        }

    def load_config(self, config: dict):
        if "banks" in config:
            self._banks = []
            for bank_data in config["banks"]:
                bank = {}
                for key, pad_dict in bank_data.items():
                    row, col = map(int, key.split(","))
                    bank[(row, col)] = PadConfig.from_dict(pad_dict)
                self._banks.append(bank)
        if not self._banks:
            self._banks = [{}]
        if "current_bank" in config:
            self._current_bank = min(config["current_bank"], len(self._banks) - 1)
        if self._active:
            self.refresh_leds()
