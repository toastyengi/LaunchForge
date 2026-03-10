"""
Core MIDI engine for Launchpad Mini Mk2 communication.
Handles bidirectional MIDI: button input and LED output.

Launchpad Mini Mk2 Layout:
- Top row (control): CC messages 104-111
- Grid 8x8: Note messages, mapped as (row * 16 + col) for rows 0-7, cols 0-7
- Right column (control): Notes at col=8 -> (row * 16 + 8)
"""

import threading
import time
from typing import Callable, Optional, Dict, Tuple

try:
    import mido
    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False


# Launchpad Mini Mk2 color velocity values (standard mapping)
class LPColor:
    """Launchpad Mini Mk2 color definitions using velocity values."""
    OFF = 0
    # Low brightness
    RED_LOW = 1
    RED = 3
    AMBER_LOW = 17
    AMBER = 51
    YELLOW = 50
    GREEN_LOW = 16
    GREEN = 48
    # Medium brightness
    ORANGE = 35
    # Flashing (OR with 0x08 for flash)

    # Full color palette mapping for easier use
    PALETTE = {
        "off": 0,
        "red": 3,
        "red_low": 1,
        "green": 48,
        "green_low": 16,
        "amber": 51,
        "amber_low": 17,
        "yellow": 50,
        "orange": 35,
        "red_flash": 11,
        "green_flash": 56,
        "amber_flash": 59,
        "yellow_flash": 58,
    }

    @classmethod
    def get(cls, name: str) -> int:
        return cls.PALETTE.get(name.lower(), 0)

    @classmethod
    def names(cls) -> list:
        return list(cls.PALETTE.keys())


class LaunchpadMIDI:
    """Handles MIDI communication with Launchpad Mini Mk2."""

    DEVICE_NAMES = ["Launchpad Mini", "Launchpad Mini MK2", "Launchpad Mini MIDI"]

    def __init__(self):
        self._input_port = None
        self._output_port = None
        self._listener_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

        # Callbacks
        self._on_grid_press: Optional[Callable] = None
        self._on_grid_release: Optional[Callable] = None
        self._on_control_press: Optional[Callable] = None
        self._on_control_release: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @staticmethod
    def list_midi_ports() -> Dict[str, list]:
        """List available MIDI input and output ports."""
        if not MIDO_AVAILABLE:
            return {"inputs": [], "outputs": []}
        return {
            "inputs": mido.get_input_names(),
            "outputs": mido.get_output_names(),
        }

    def find_launchpad_ports(self) -> Tuple[Optional[str], Optional[str]]:
        """Auto-detect Launchpad MIDI ports."""
        ports = self.list_midi_ports()
        input_port = None
        output_port = None

        for name in ports["inputs"]:
            for dev_name in self.DEVICE_NAMES:
                if dev_name.lower() in name.lower():
                    input_port = name
                    break

        for name in ports["outputs"]:
            for dev_name in self.DEVICE_NAMES:
                if dev_name.lower() in name.lower():
                    output_port = name
                    break

        return input_port, output_port

    def connect(self, input_name: Optional[str] = None, output_name: Optional[str] = None) -> bool:
        """Connect to the Launchpad. Auto-detects if names not provided."""
        if not MIDO_AVAILABLE:
            print("[MIDI] mido not available - running in simulation mode")
            self._connected = False
            return False

        if input_name is None or output_name is None:
            auto_in, auto_out = self.find_launchpad_ports()
            input_name = input_name or auto_in
            output_name = output_name or auto_out

        if not input_name or not output_name:
            print("[MIDI] Launchpad not found")
            self._connected = False
            return False

        try:
            self._input_port = mido.open_input(input_name)
            self._output_port = mido.open_output(output_name)
            self._connected = True
            self._start_listener()
            print(f"[MIDI] Connected: {input_name} / {output_name}")
            return True
        except Exception as e:
            print(f"[MIDI] Connection error: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Disconnect from the Launchpad."""
        self._running = False
        if self._listener_thread:
            self._listener_thread.join(timeout=2)
        if self._input_port:
            self._input_port.close()
            self._input_port = None
        if self._output_port:
            self._output_port.close()
            self._output_port = None
        self._connected = False
        print("[MIDI] Disconnected")

    def set_callbacks(
        self,
        on_grid_press: Optional[Callable] = None,
        on_grid_release: Optional[Callable] = None,
        on_control_press: Optional[Callable] = None,
        on_control_release: Optional[Callable] = None,
    ):
        """Set callback functions for button events."""
        if on_grid_press:
            self._on_grid_press = on_grid_press
        if on_grid_release:
            self._on_grid_release = on_grid_release
        if on_control_press:
            self._on_control_press = on_control_press
        if on_control_release:
            self._on_control_release = on_control_release

    def _start_listener(self):
        """Start the MIDI input listener thread."""
        self._running = True
        self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listener_thread.start()

    def _listen_loop(self):
        """Main MIDI listener loop."""
        while self._running and self._input_port:
            try:
                msg = self._input_port.receive(block=False)
                if msg is None:
                    time.sleep(0.001)
                    continue
                self._process_message(msg)
            except Exception:
                time.sleep(0.01)

    def _process_message(self, msg):
        """Process incoming MIDI message and dispatch to callbacks."""
        if msg.type == "control_change":
            # Top row control buttons: CC 104-111
            if 104 <= msg.control <= 111:
                button_index = msg.control - 104
                if msg.value > 0 and self._on_control_press:
                    self._on_control_press("top", button_index)
                elif msg.value == 0 and self._on_control_release:
                    self._on_control_release("top", button_index)

        elif msg.type in ("note_on", "note_off"):
            note = msg.note
            row = note // 16
            col = note % 16

            if 0 <= row <= 7:
                if col == 8:
                    # Right column control buttons
                    if msg.type == "note_on" and msg.velocity > 0:
                        if self._on_control_press:
                            self._on_control_press("right", row)
                    else:
                        if self._on_control_release:
                            self._on_control_release("right", row)
                elif 0 <= col <= 7:
                    # Grid buttons
                    if msg.type == "note_on" and msg.velocity > 0:
                        if self._on_grid_press:
                            self._on_grid_press(row, col)
                    else:
                        if self._on_grid_release:
                            self._on_grid_release(row, col)

    def set_led(self, row: int, col: int, color: int):
        """Set a single grid LED color."""
        if not self._connected or not self._output_port:
            return
        note = row * 16 + col
        msg = mido.Message("note_on", note=note, velocity=color, channel=0)
        try:
            self._output_port.send(msg)
        except Exception as e:
            print(f"[MIDI] LED set error: {e}")

    def set_control_led(self, position: str, index: int, color: int):
        """Set a control button LED (top row or right column)."""
        if not self._connected or not self._output_port:
            return
        try:
            if position == "top":
                msg = mido.Message("control_change", control=104 + index, value=color, channel=0)
            else:
                note = index * 16 + 8
                msg = mido.Message("note_on", note=note, velocity=color, channel=0)
            self._output_port.send(msg)
        except Exception as e:
            print(f"[MIDI] Control LED error: {e}")

    def clear_all(self):
        """Turn off all LEDs."""
        if not self._connected or not self._output_port:
            return
        # Clear grid
        for row in range(8):
            for col in range(8):
                self.set_led(row, col, LPColor.OFF)
        # Clear controls
        for i in range(8):
            self.set_control_led("top", i, LPColor.OFF)
            self.set_control_led("right", i, LPColor.OFF)

    def set_grid(self, grid: list):
        """Set entire 8x8 grid from a 2D list of color values."""
        for row in range(min(8, len(grid))):
            for col in range(min(8, len(grid[row]))):
                self.set_led(row, col, grid[row][col])

    def reset(self):
        """Reset the Launchpad (send reset SysEx)."""
        if not self._connected or not self._output_port:
            return
        try:
            # Launchpad Mini Mk2 reset via CC
            msg = mido.Message("control_change", control=0, value=0, channel=0)
            self._output_port.send(msg)
        except Exception:
            pass
