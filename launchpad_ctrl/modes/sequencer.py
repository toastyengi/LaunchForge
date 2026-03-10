"""
Step Sequencer Mode - Teenage Engineering PO-style grid sequencer.
Each row is a configurable track with its own sample.
Scanner moves left to right, triggering active cells.
"""

import time
import threading
from typing import Optional, Dict
from launchpad_ctrl.modes import BaseMode
from launchpad_ctrl.core import LPColor


class StepSequencerMode(BaseMode):
    NAME = "Sequencer"
    DESCRIPTION = "Step sequencer with per-row samples"
    COLOR = "green"

    # Colors
    COLOR_INACTIVE = LPColor.OFF
    COLOR_ACTIVE = LPColor.GREEN
    COLOR_PLAYHEAD = LPColor.RED
    COLOR_ACTIVE_PLAYHEAD = LPColor.AMBER
    COLOR_MUTED_ACTIVE = LPColor.GREEN_LOW
    COLOR_MUTED_PLAYHEAD = LPColor.RED_LOW

    def __init__(self, midi, audio):
        super().__init__(midi, audio)
        # 8 rows x 8 steps
        self._grid = [[False] * 8 for _ in range(8)]
        self._bpm = 120.0
        self._playing = False
        self._playhead = 0  # current column (0-7)
        self._muted_rows = [False] * 8
        self._row_samples: Dict[int, str] = {}  # row -> filepath
        self._row_volumes: Dict[int, float] = {i: 0.8 for i in range(8)}
        self._row_colors: Dict[int, int] = {i: LPColor.GREEN for i in range(8)}

        self._tick_thread: Optional[threading.Thread] = None
        self._tick_running = False
        self._last_tick_time = 0

        # Tap tempo
        self._tap_times = []

    @property
    def bpm(self):
        return self._bpm

    @bpm.setter
    def bpm(self, value):
        self._bpm = max(20, min(300, value))

    @property
    def playing(self):
        return self._playing

    @property
    def playhead(self):
        return self._playhead

    def activate(self):
        super().activate()
        self._update_right_column()

    def deactivate(self):
        self.stop_playback()
        super().deactivate()

    def cleanup(self):
        self.stop_playback()

    def set_sample(self, row: int, filepath: str):
        """Assign a sample to a row."""
        if 0 <= row < 8:
            self._row_samples[row] = filepath

    def get_sample(self, row: int) -> Optional[str]:
        return self._row_samples.get(row)

    def set_row_volume(self, row: int, volume: float):
        if 0 <= row < 8:
            self._row_volumes[row] = max(0.0, min(1.0, volume))

    def toggle_mute(self, row: int):
        if 0 <= row < 8:
            self._muted_rows[row] = not self._muted_rows[row]
            self._update_right_column()
            self.refresh_leds()

    def on_grid_press(self, row: int, col: int):
        if 0 <= row < 8 and 0 <= col < 8:
            self._grid[row][col] = not self._grid[row][col]
            self._update_led(row, col)
            self.notify_ui()

    def on_control_press(self, position: str, index: int):
        if position == "right":
            # Right column: mute/unmute rows
            self.toggle_mute(index)
            self.notify_ui()

    def _update_led(self, row: int, col: int):
        """Update a single LED based on grid state and playhead."""
        is_active = self._grid[row][col]
        is_playhead = (col == self._playhead and self._playing)
        is_muted = self._muted_rows[row]

        if is_muted:
            if is_playhead and is_active:
                color = self.COLOR_MUTED_PLAYHEAD
            elif is_playhead:
                color = self.COLOR_MUTED_PLAYHEAD
            elif is_active:
                color = self.COLOR_MUTED_ACTIVE
            else:
                color = self.COLOR_INACTIVE
        else:
            if is_playhead and is_active:
                color = self.COLOR_ACTIVE_PLAYHEAD
            elif is_playhead:
                color = self.COLOR_PLAYHEAD
            elif is_active:
                color = self._row_colors.get(row, self.COLOR_ACTIVE)
            else:
                color = self.COLOR_INACTIVE

        self._grid_state[row][col] = color
        self.midi.set_led(row, col, color)

    def _update_right_column(self):
        """Update right column LEDs to show mute state."""
        for row in range(8):
            color = LPColor.RED_LOW if self._muted_rows[row] else LPColor.GREEN
            if row in self._row_samples:
                if self._muted_rows[row]:
                    color = LPColor.RED_LOW
                else:
                    color = LPColor.GREEN
            else:
                color = LPColor.AMBER_LOW if not self._muted_rows[row] else LPColor.OFF
            self.midi.set_control_led("right", row, color)

    def refresh_leds(self):
        for row in range(8):
            for col in range(8):
                self._update_led(row, col)
        self._update_right_column()

    # --- Playback ---

    def start_playback(self):
        if self._playing:
            return
        self._playing = True
        self._playhead = 0
        self._tick_running = True
        self._last_tick_time = time.time()
        self._tick_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._tick_thread.start()
        self.notify_ui()

    def stop_playback(self):
        self._playing = False
        self._tick_running = False
        if self._tick_thread:
            self._tick_thread.join(timeout=2)
            self._tick_thread = None
        self._playhead = 0
        self.refresh_leds()
        self.notify_ui()

    def toggle_playback(self):
        if self._playing:
            self.stop_playback()
        else:
            self.start_playback()

    def _playback_loop(self):
        """Main sequencer loop running in a separate thread."""
        while self._tick_running:
            step_duration = 60.0 / self._bpm / 2  # 8th notes

            # Trigger sounds for the current column
            self._trigger_column(self._playhead)
            self.refresh_leds()
            self.notify_ui()

            # Wait for next step
            next_time = self._last_tick_time + step_duration
            now = time.time()
            sleep_time = next_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)

            self._last_tick_time = time.time()

            # Advance playhead
            self._playhead = (self._playhead + 1) % 8

    def _trigger_column(self, col: int):
        """Trigger all active sounds in a column."""
        for row in range(8):
            if self._grid[row][col] and not self._muted_rows[row]:
                filepath = self._row_samples.get(row)
                if filepath:
                    self.audio.play_sound(filepath, volume=self._row_volumes[row])

    def tap_tempo(self):
        """Record a tap for tap-tempo."""
        now = time.time()
        self._tap_times.append(now)
        # Keep last 4 taps
        if len(self._tap_times) > 4:
            self._tap_times = self._tap_times[-4:]
        if len(self._tap_times) >= 2:
            intervals = [self._tap_times[i+1] - self._tap_times[i]
                        for i in range(len(self._tap_times) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval > 0:
                self.bpm = 60.0 / avg_interval
                self.notify_ui()

    def clear_grid(self):
        """Clear all active cells."""
        self._grid = [[False] * 8 for _ in range(8)]
        self.refresh_leds()
        self.notify_ui()

    # --- Config ---

    def get_config(self) -> dict:
        return {
            "grid": [row[:] for row in self._grid],
            "bpm": self._bpm,
            "muted_rows": self._muted_rows[:],
            "row_samples": dict(self._row_samples),
            "row_volumes": dict(self._row_volumes),
        }

    def load_config(self, config: dict):
        if "grid" in config:
            self._grid = config["grid"]
        if "bpm" in config:
            self._bpm = config["bpm"]
        if "muted_rows" in config:
            self._muted_rows = config["muted_rows"]
        if "row_samples" in config:
            self._row_samples = {int(k): v for k, v in config["row_samples"].items()}
        if "row_volumes" in config:
            self._row_volumes = {int(k): v for k, v in config["row_volumes"].items()}
        if self._active:
            self.refresh_leds()
