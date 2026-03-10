"""
Recorder Mode - Record audio from microphone and assign to pads.

State machine:
  IDLE       -> Press Record -> RECORDING
  RECORDING  -> Press Stop   -> ASSIGNING (pending recording, pick a pad)
  RECORDING  -> Press a pad  -> stops + assigns directly to that pad -> IDLE
  ASSIGNING  -> Press a pad  -> assigns pending recording to pad -> IDLE
  ASSIGNING  -> Press Record -> discards pending, starts new RECORDING
  ASSIGNING  -> Press Discard -> discards pending -> IDLE

The saved .wav files are also usable from Soundboard mode.
"""

import os
import sys
import time
import numpy as np
from enum import Enum, auto
from typing import Optional
from launchpad_ctrl.modes import BaseMode
from launchpad_ctrl.core import LPColor


class RecState(Enum):
    IDLE = auto()
    RECORDING = auto()
    ASSIGNING = auto()


class RecorderMode(BaseMode):
    NAME = "Recorder"
    DESCRIPTION = "Record audio and assign to pads"
    COLOR = "red"

    COLOR_EMPTY = LPColor.OFF
    COLOR_RECORDED = LPColor.AMBER
    COLOR_RECORDING = LPColor.RED
    COLOR_PLAYING = LPColor.GREEN
    COLOR_SELECTED = LPColor.YELLOW
    COLOR_ASSIGN_TARGET = LPColor.GREEN_LOW

    def __init__(self, midi, audio, samples_dir: str = ""):
        super().__init__(midi, audio)
        if samples_dir:
            self._samples_dir = samples_dir
        elif sys.platform == "win32":
            self._samples_dir = os.path.join(
                os.environ.get("APPDATA", os.path.expanduser("~")), "LaunchPadCtrl", "recordings"
            )
        else:
            self._samples_dir = os.path.expanduser("~/.launchpad-ctrl/recordings")
        os.makedirs(self._samples_dir, exist_ok=True)

        self._recordings: dict = {}  # (row, col) -> filepath
        self._state = RecState.IDLE
        self._selected_pad: Optional[tuple] = None
        self._record_start_time = 0.0
        self._pending_recording: Optional[np.ndarray] = None
        self._trim_start_sec = 0.0
        self._trim_end_sec = 0.0
        self._last_blink = False

    @property
    def samples_dir(self):
        return self._samples_dir

    @property
    def state(self):
        return self._state

    @property
    def is_recording(self):
        return self._state == RecState.RECORDING

    @property
    def is_assigning(self):
        return self._state == RecState.ASSIGNING

    @property
    def selected_pad(self):
        return self._selected_pad

    @property
    def recording_duration(self) -> float:
        if self._state == RecState.RECORDING and self._record_start_time > 0:
            return time.time() - self._record_start_time
        return 0.0

    @property
    def has_pending_recording(self):
        return self._pending_recording is not None

    @property
    def pending_duration(self) -> float:
        if self._pending_recording is None:
            return 0.0
        return len(self._pending_recording) / float(self.audio.samplerate)

    @property
    def trim_start_sec(self) -> float:
        return self._trim_start_sec

    @property
    def trim_end_sec(self) -> float:
        return self._trim_end_sec

    @property
    def trim_duration(self) -> float:
        return max(0.0, self._trim_end_sec - self._trim_start_sec)

    # ---- Actions ----

    def start_recording(self):
        self._pending_recording = None
        self._state = RecState.RECORDING
        self._record_start_time = time.time()
        self.audio.start_recording()
        self.refresh_leds()
        self.notify_ui()

    def stop_recording(self):
        if self._state != RecState.RECORDING:
            return
        data = self.audio.stop_recording()
        if data is not None and len(data) > 100:
            self._pending_recording = data
            self._trim_start_sec = 0.0
            self._trim_end_sec = self.pending_duration
            self._state = RecState.ASSIGNING
        else:
            self._pending_recording = None
            self._trim_start_sec = 0.0
            self._trim_end_sec = 0.0
            self._state = RecState.IDLE
        self.refresh_leds()
        self.notify_ui()

    def assign_to_pad(self, row: int, col: int) -> bool:
        if self._pending_recording is None:
            return False
        trimmed = self.get_trimmed_pending_recording()
        if trimmed is None or len(trimmed) <= 100:
            print("[Recorder] Trimmed recording is too short")
            return False
        filename = f"rec_{row}_{col}_{int(time.time())}.wav"
        filepath = os.path.join(self._samples_dir, filename)
        success = self.audio.save_recording(trimmed, filepath)
        if success and os.path.exists(filepath):
            self._recordings[(row, col)] = filepath
            self._selected_pad = (row, col)
            self._pending_recording = None
            self._trim_start_sec = 0.0
            self._trim_end_sec = 0.0
            self._state = RecState.IDLE
            self.refresh_leds()
            self.notify_ui()
            return True
        else:
            print(f"[Recorder] Failed to save recording to {filepath}")
            return False

    def discard_pending(self):
        self._pending_recording = None
        self._trim_start_sec = 0.0
        self._trim_end_sec = 0.0
        self._state = RecState.IDLE
        self.refresh_leds()
        self.notify_ui()

    def set_trim(self, start_sec: float, end_sec: float):
        if self._pending_recording is None:
            return
        duration = self.pending_duration
        start = max(0.0, min(start_sec, duration))
        end = max(0.0, min(end_sec, duration))
        if end < start:
            start, end = end, start
        self._trim_start_sec = start
        self._trim_end_sec = end

    def get_trimmed_pending_recording(self) -> Optional[np.ndarray]:
        if self._pending_recording is None:
            return None
        duration = self.pending_duration
        if duration <= 0:
            return None
        start_idx = int(self._trim_start_sec * self.audio.samplerate)
        end_idx = int(self._trim_end_sec * self.audio.samplerate)
        start_idx = max(0, min(start_idx, len(self._pending_recording)))
        end_idx = max(0, min(end_idx, len(self._pending_recording)))
        if end_idx <= start_idx:
            return None
        return self._pending_recording[start_idx:end_idx]

    def assign_file_to_pad(self, row: int, col: int, filepath: str):
        if os.path.exists(filepath):
            self._recordings[(row, col)] = filepath
            self.refresh_leds()
            self.notify_ui()

    def delete_pad(self, row: int, col: int):
        if (row, col) in self._recordings:
            filepath = self._recordings.pop((row, col))
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except OSError:
                pass
            if self._selected_pad == (row, col):
                self._selected_pad = None
            self.refresh_leds()
            self.notify_ui()

    def clear_all(self):
        self._recordings.clear()
        self._selected_pad = None
        self.refresh_leds()
        self.notify_ui()

    def get_recording_path(self, row: int, col: int) -> Optional[str]:
        return self._recordings.get((row, col))

    # ---- Grid Events ----

    def on_grid_press(self, row: int, col: int):
        if self._state == RecState.RECORDING:
            # Stop + assign directly to this pad
            data = self.audio.stop_recording()
            if data is not None and len(data) > 100:
                self._pending_recording = data
                self._trim_start_sec = 0.0
                self._trim_end_sec = self.pending_duration
                self._state = RecState.ASSIGNING
                self.assign_to_pad(row, col)
            else:
                self._state = RecState.IDLE
                self._pending_recording = None
                self._trim_start_sec = 0.0
                self._trim_end_sec = 0.0

        elif self._state == RecState.ASSIGNING:
            self.assign_to_pad(row, col)

        elif self._state == RecState.IDLE:
            if (row, col) in self._recordings:
                self._selected_pad = (row, col)
                filepath = self._recordings[(row, col)]
                self.audio.play_sound(filepath)
            else:
                self._selected_pad = (row, col)

        self.refresh_leds()
        self.notify_ui()

    def on_grid_release(self, row: int, col: int):
        pass

    def on_control_press(self, position: str, index: int):
        if position == "right":
            if index == 0:
                if self._state == RecState.RECORDING:
                    self.stop_recording()
                elif self._state == RecState.ASSIGNING:
                    self.discard_pending()
                    self.start_recording()
                else:
                    self.start_recording()
            elif index == 1:
                if self._selected_pad and self._selected_pad in self._recordings:
                    self.delete_pad(*self._selected_pad)
            elif index == 2:
                if self._state == RecState.ASSIGNING:
                    self.discard_pending()
            elif index == 7:
                self.clear_all()

    def on_control_release(self, position: str, index: int):
        pass

    # ---- LED Display ----

    def tick(self, dt: float):
        if self._state in (RecState.RECORDING, RecState.ASSIGNING):
            new_blink = int(time.time() * 3) % 2 == 0
            if new_blink != self._last_blink:
                self._last_blink = new_blink
                self.refresh_leds()

    def _update_led(self, row: int, col: int):
        if self._state == RecState.RECORDING:
            if (row, col) in self._recordings:
                color = LPColor.AMBER_LOW if not self._last_blink else self.COLOR_RECORDING
            elif self._last_blink:
                color = self.COLOR_RECORDING
            else:
                color = LPColor.RED_LOW

        elif self._state == RecState.ASSIGNING:
            if (row, col) in self._recordings:
                color = LPColor.AMBER
            elif self._last_blink:
                color = self.COLOR_ASSIGN_TARGET
            else:
                color = LPColor.OFF

        else:
            if (row, col) == self._selected_pad:
                color = self.COLOR_SELECTED
            elif (row, col) in self._recordings:
                color = self.COLOR_RECORDED
            else:
                color = self.COLOR_EMPTY

        self._grid_state[row][col] = color
        self.midi.set_led(row, col, color)

    def _update_right_column(self):
        if self._state == RecState.RECORDING:
            rec_color = LPColor.RED
        elif self._state == RecState.ASSIGNING:
            rec_color = LPColor.AMBER
        else:
            rec_color = LPColor.RED_LOW

        self.midi.set_control_led("right", 0, rec_color)

        del_color = LPColor.AMBER if (self._selected_pad and self._selected_pad in self._recordings) else LPColor.OFF
        self.midi.set_control_led("right", 1, del_color)

        discard_color = LPColor.RED_LOW if self._state == RecState.ASSIGNING else LPColor.OFF
        self.midi.set_control_led("right", 2, discard_color)

        for i in range(3, 7):
            self.midi.set_control_led("right", i, LPColor.OFF)

        self.midi.set_control_led("right", 7, LPColor.AMBER_LOW)

    def refresh_leds(self):
        for row in range(8):
            for col in range(8):
                self._update_led(row, col)
        self._update_right_column()

    def activate(self):
        super().activate()

    def deactivate(self):
        if self._state == RecState.RECORDING:
            self.stop_recording()
        super().deactivate()

    # ---- Config ----

    def get_config(self) -> dict:
        recs = {}
        for (row, col), filepath in self._recordings.items():
            recs[f"{row},{col}"] = filepath
        return {"recordings": recs}

    def load_config(self, config: dict):
        if "recordings" in config:
            self._recordings = {}
            for key, filepath in config["recordings"].items():
                row, col = map(int, key.split(","))
                if os.path.exists(filepath):
                    self._recordings[(row, col)] = filepath
        if self._active:
            self.refresh_leds()
