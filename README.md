# Novation Launchpad Soundboard, Sequencer and Recorder

A modular MIDI controller application for the **Novation Launchpad Mini Mk2** on Arch Linux. Features a PyQt5 dark-themed GUI with a virtual grid that mirrors your physical Launchpad, multiple operational modes, mic recording, and full audio device selection.

![Python](https://img.shields.io/badge/Python-3.8+-blue) ![Arch Linux](https://img.shields.io/badge/Arch-Linux-1793D1) ![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

### 🎵 Step Sequencer
- 8x8 grid sequencer with per-row sample assignment
- Configurable BPM with tap tempo support (on Launchpad or GUI)
- Per-row volume control and mute/unmute via right column buttons
- Visual playhead scanning left-to-right (Teenage Engineering PO-style)
- Save/load patterns per project

### 🔊 Soundboard
- Assign any WAV/OGG/MP3 file to any pad
- **Retriggerable** — spam-press pads freely, each press starts a new playback
- Configurable LED colors per pad
- Per-pad volume control
- Multiple banks (pages) — switch via right column buttons
- Global "Stop All" panic button

### 🎙️ Recorder
- Record directly from your microphone
- Press a pad to assign the recording to it
- Import existing audio files to pads
- Preview recordings by pressing assigned pads

### 🎛️ Global Controls (Top Row Buttons)
| Button | Function |
|--------|----------|
| 1 (◀) | Previous mode |
| 2 (▶) | Next mode |
| 3 (⏹) | Stop all / Panic |
| 4 (▶) | Play / Pause (sequencer) |
| 5 (⏺) | Record toggle |
| 6 | Tap tempo |
| 7-8 | Reserved for future use |

### 💾 Project System
- Save and load complete project states
- GUI-managed with JSON export/import for backup and sharing
- All mode configurations preserved per project

---

## Installation

### Arch Linux (Quick Install)

```bash
git clone <your-repo-url> launchpad-controller
cd launchpad-controller
chmod +x install.sh
./install.sh
```

The installer handles everything: system packages, Python deps, udev rules, audio group, and desktop shortcut.

### Windows

**Prerequisites:** Install [Python 3.8+](https://www.python.org/downloads/) (check "Add Python to PATH" during install). For MP3 support, install [ffmpeg](https://ffmpeg.org/download.html) and add it to your PATH.

```
cd launchpad-controller
install.bat
```

Or manually:
```
pip install PyQt5 mido python-rtmidi sounddevice soundfile pydub numpy
pip install -e .
launchpad-ctrl
```

The Launchpad Mini Mk2 should work out of the box on Windows via its standard USB MIDI driver. If it doesn't show up, install the [Novation USB driver](https://customer.novationmusic.com/en/support/downloads).

### macOS

The Python code works on macOS too. Install deps with pip, and you may need `brew install portaudio ffmpeg` for audio support.

### Manual Install (Any Platform)

```bash
# System deps
sudo pacman -S python python-pip python-pyqt5 python-numpy alsa-utils rtmidi ffmpeg portaudio

# Python deps
pip install --user mido python-rtmidi sounddevice soundfile pydub numpy

# Install the app
pip install --user -e .
```

### Post-Install

1. Plug in your Launchpad Mini Mk2 via USB
2. If this is your first install, **log out and back in** (for audio group)
3. Run: `launchpad-ctrl` or `python -m launchpad_ctrl`

---

## Usage

### Running

```bash
# From anywhere
launchpad-ctrl

# Or from the project directory
python -m launchpad_ctrl
```

### Workflow

1. **Connect** your Launchpad — auto-detected on startup, or use the MIDI tab to manually connect
2. **Select a mode** — click the mode buttons at the top or use the Launchpad's top-row buttons
3. **Sequencer**: Load samples per row, toggle cells, hit play
4. **Soundboard**: Select a pad in the GUI, assign a sound file and color
5. **Recorder**: Hit record, capture audio, press a pad to assign it
6. **Save your project** via File → Save Project

### Audio Device Selection

Use the **Audio** tab in the right panel to:
- Select your output device (speakers, headphones, audio interface)
- Select your input device (microphone, line-in)
- Adjust master volume

---

## File Structure

```
launchpad-controller/
├── install.sh                  # Arch Linux installer
├── setup.py                    # Python package setup
├── README.md
└── launchpad_ctrl/
    ├── __init__.py
    ├── __main__.py             # Entry point
    ├── core/
    │   ├── __init__.py         # MIDI engine (LaunchpadMIDI, LPColor)
    │   └── audio.py            # Audio engine (playback, recording, devices)
    ├── modes/
    │   ├── __init__.py         # BaseMode, ModeManager
    │   ├── sequencer.py        # Step Sequencer mode
    │   ├── soundboard.py       # Soundboard mode
    │   └── recorder.py         # Recorder mode
    └── ui/
        ├── __init__.py
        ├── theme.py            # Dark theme stylesheet
        ├── grid_widget.py      # Virtual Launchpad grid widget
        └── main_window.py      # Main application window
```

---

## Adding Custom Modes

Create a new Python file in `launchpad_ctrl/modes/` and extend `BaseMode`:

```python
from launchpad_ctrl.modes import BaseMode
from launchpad_ctrl.core import LPColor

class MyCustomMode(BaseMode):
    NAME = "MyMode"
    DESCRIPTION = "Description of my mode"
    COLOR = "amber"

    def on_grid_press(self, row, col):
        # Handle button press
        pass

    def on_control_press(self, position, index):
        # Handle control button
        pass

    def refresh_leds(self):
        # Update the LED grid
        pass
```

Then register it in `main_window.py`:

```python
from launchpad_ctrl.modes.my_mode import MyCustomMode
my_mode = MyCustomMode(self.midi, self.audio)
self.mode_manager.register_mode(my_mode)
```

---

## Config

- **Config directory**: `~/.launchpad-ctrl/`
- **Projects**: `~/.launchpad-ctrl/projects/`
- **Recordings**: `~/.launchpad-ctrl/recordings/`

Projects are saved as `.lpproj` files (JSON format) and can be freely shared.

---

## Troubleshooting

**Launchpad not detected:**
- Check `aconnect -l` to see if the MIDI device appears
- Try unplugging and replugging the USB cable
- Ensure udev rules are loaded: `sudo udevadm control --reload-rules`

**No audio output:**
- Check the Audio tab for correct output device
- Ensure you're in the `audio` group: `groups` should show `audio`
- Test with: `speaker-test -c 2`

**Permission denied on MIDI:**
- Run `sudo udevadm trigger` after installing udev rules
- Log out and back in after being added to the `audio` group

---

## License

MIT License — do whatever you want with it.
