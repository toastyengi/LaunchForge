"""
Mode system - base class and mode manager.
Each mode is a self-contained plugin that controls the grid behavior.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from launchpad_ctrl.core import LaunchpadMIDI, LPColor
    from launchpad_ctrl.core.audio import AudioEngine


class BaseMode(ABC):
    """Abstract base class for all Launchpad modes."""

    NAME = "Base"
    DESCRIPTION = "Base mode"
    COLOR = "off"  # Color for the mode indicator LED

    def __init__(self, midi: "LaunchpadMIDI", audio: "AudioEngine"):
        self.midi = midi
        self.audio = audio
        self._active = False
        self._grid_state = [[0] * 8 for _ in range(8)]
        self._ui_update_callback = None

    @property
    def active(self):
        return self._active

    def set_ui_callback(self, callback):
        """Set callback to notify UI of state changes."""
        self._ui_update_callback = callback

    def notify_ui(self):
        """Notify the UI that state has changed."""
        if self._ui_update_callback:
            self._ui_update_callback()

    def activate(self):
        """Called when this mode becomes active."""
        self._active = True
        self.refresh_leds()

    def deactivate(self):
        """Called when switching away from this mode."""
        self._active = False
        self.cleanup()

    @abstractmethod
    def on_grid_press(self, row: int, col: int):
        """Handle grid button press."""
        pass

    def on_grid_release(self, row: int, col: int):
        """Handle grid button release. Override if needed."""
        pass

    @abstractmethod
    def on_control_press(self, position: str, index: int):
        """Handle control button press (top/right)."""
        pass

    def on_control_release(self, position: str, index: int):
        """Handle control button release. Override if needed."""
        pass

    @abstractmethod
    def refresh_leds(self):
        """Refresh all LEDs to match current state."""
        pass

    def cleanup(self):
        """Cleanup when deactivating. Override if needed."""
        pass

    def tick(self, dt: float):
        """Called periodically for time-based updates (e.g., sequencer). Override if needed."""
        pass

    def get_grid_state(self) -> list:
        """Return the current grid state as a 2D list of color values."""
        return [row[:] for row in self._grid_state]

    def get_config(self) -> dict:
        """Return serializable config for saving projects."""
        return {}

    def load_config(self, config: dict):
        """Load config from a saved project."""
        pass

    def get_ui_controls(self) -> list:
        """Return list of UI controls this mode wants displayed.
        Each control: {"type": "slider"|"button"|"label"|"file", "name": str, ...}
        """
        return []


class ModeManager:
    """Manages mode switching and dispatching."""

    def __init__(self, midi: "LaunchpadMIDI", audio: "AudioEngine"):
        self.midi = midi
        self.audio = audio
        self._modes: Dict[str, BaseMode] = {}
        self._mode_order: List[str] = []
        self._current_mode: Optional[str] = None
        self._on_mode_change = None

    def register_mode(self, mode: BaseMode):
        """Register a new mode."""
        name = mode.NAME
        self._modes[name] = mode
        if name not in self._mode_order:
            self._mode_order.append(name)

    def set_mode_change_callback(self, callback):
        self._on_mode_change = callback

    @property
    def current_mode(self) -> Optional[BaseMode]:
        if self._current_mode:
            return self._modes.get(self._current_mode)
        return None

    @property
    def current_mode_name(self) -> Optional[str]:
        return self._current_mode

    @property
    def mode_names(self) -> List[str]:
        return self._mode_order[:]

    def switch_mode(self, name: str):
        """Switch to a named mode."""
        if name not in self._modes:
            print(f"[ModeManager] Unknown mode: {name}")
            return

        if self._current_mode:
            self._modes[self._current_mode].deactivate()

        self._current_mode = name
        self._modes[name].activate()
        print(f"[ModeManager] Switched to: {name}")

        if self._on_mode_change:
            self._on_mode_change(name)

    def next_mode(self):
        """Cycle to the next mode."""
        if not self._mode_order:
            return
        if self._current_mode is None:
            self.switch_mode(self._mode_order[0])
        else:
            idx = self._mode_order.index(self._current_mode)
            next_idx = (idx + 1) % len(self._mode_order)
            self.switch_mode(self._mode_order[next_idx])

    def prev_mode(self):
        """Cycle to the previous mode."""
        if not self._mode_order:
            return
        if self._current_mode is None:
            self.switch_mode(self._mode_order[-1])
        else:
            idx = self._mode_order.index(self._current_mode)
            prev_idx = (idx - 1) % len(self._mode_order)
            self.switch_mode(self._mode_order[prev_idx])

    def on_grid_press(self, row: int, col: int):
        if self.current_mode:
            self.current_mode.on_grid_press(row, col)

    def on_grid_release(self, row: int, col: int):
        if self.current_mode:
            self.current_mode.on_grid_release(row, col)

    def on_control_press(self, position: str, index: int):
        if self.current_mode:
            self.current_mode.on_control_press(position, index)

    def on_control_release(self, position: str, index: int):
        if self.current_mode:
            self.current_mode.on_control_release(position, index)

    def tick(self, dt: float):
        if self.current_mode:
            self.current_mode.tick(dt)

    def get_project_config(self) -> dict:
        """Get complete project config from all modes."""
        config = {}
        for name, mode in self._modes.items():
            config[name] = mode.get_config()
        return config

    def load_project_config(self, config: dict):
        """Load project config into all modes."""
        for name, mode_config in config.items():
            if name in self._modes:
                self._modes[name].load_config(mode_config)
