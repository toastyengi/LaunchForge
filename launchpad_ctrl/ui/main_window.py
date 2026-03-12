"""
Main application window for LaunchPad Controller.
"""

import os
import json
import time
from functools import partial

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QSlider, QSpinBox, QDoubleSpinBox,
    QComboBox, QFileDialog, QGroupBox, QStatusBar, QMenuBar,
    QAction, QMessageBox, QSplitter, QFrame, QTabWidget,
    QScrollArea, QLineEdit, QSizePolicy, QApplication
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QIcon

from launchpad_ctrl.ui.grid_widget import LaunchpadGrid
from launchpad_ctrl.ui.theme import DARK_THEME
from launchpad_ctrl.core import LaunchpadMIDI, LPColor
from launchpad_ctrl.core.audio import AudioEngine, AudioDevice, VirtualAudioRouter
from launchpad_ctrl.modes import ModeManager
from launchpad_ctrl.modes.sequencer import StepSequencerMode
from launchpad_ctrl.modes.soundboard import SoundboardMode, PadConfig
from launchpad_ctrl.modes.recorder import RecorderMode


import sys as _sys

if _sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LaunchPadCtrl")
else:
    CONFIG_DIR = os.path.expanduser("~/.launchpad-ctrl")
PROJECTS_DIR = os.path.join(CONFIG_DIR, "projects")
RECORDINGS_DIR = os.path.join(CONFIG_DIR, "recordings")


class MainWindow(QMainWindow):
    """Main application window."""

    # Thread-safe signals: MIDI callbacks fire from the listener thread,
    # so we marshal them to the main Qt thread via queued signals.
    _sig_midi_grid_press = pyqtSignal(int, int)
    _sig_midi_grid_release = pyqtSignal(int, int)
    _sig_midi_control_press = pyqtSignal(str, int)
    _sig_midi_control_release = pyqtSignal(str, int)
    _sig_ui_update = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LaunchPad Controller")
        self.setMinimumSize(1000, 700)

        # Ensure dirs exist
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(PROJECTS_DIR, exist_ok=True)
        os.makedirs(RECORDINGS_DIR, exist_ok=True)

        # Core systems
        self.midi = LaunchpadMIDI()
        self.audio = AudioEngine()
        self.virtual_router = VirtualAudioRouter()
        self.mode_manager = ModeManager(self.midi, self.audio)

        # Register modes
        self._sequencer = StepSequencerMode(self.midi, self.audio)
        self._soundboard = SoundboardMode(self.midi, self.audio)
        self._recorder = RecorderMode(self.midi, self.audio, RECORDINGS_DIR)

        self.mode_manager.register_mode(self._sequencer)
        self.mode_manager.register_mode(self._soundboard)
        self.mode_manager.register_mode(self._recorder)

        # Set UI callbacks
        self._sequencer.set_ui_callback(self._on_ui_update)
        self._soundboard.set_ui_callback(self._on_ui_update)
        self._recorder.set_ui_callback(self._on_ui_update)
        self.mode_manager.set_mode_change_callback(self._on_mode_changed)

        # Connect thread-safe signals to main-thread handlers
        self._sig_midi_grid_press.connect(self._on_midi_grid_press)
        self._sig_midi_grid_release.connect(self._on_midi_grid_release)
        self._sig_midi_control_press.connect(self._on_midi_control_press)
        self._sig_midi_control_release.connect(self._on_midi_control_release)
        self._sig_ui_update.connect(self._handle_ui_update)

        # Apply theme
        self.setStyleSheet(DARK_THEME)

        # Build UI
        self._build_ui()
        self._build_menu()
        self._setup_midi_callbacks()
        self._setup_timers()

        # Start audio engine
        self.audio.start()

        # Auto-connect MIDI
        self._try_midi_connect()

        # Start with sequencer mode
        self.mode_manager.switch_mode("Sequencer")

    def _build_ui(self):
        """Build the main UI layout."""
        # Create status bar early — combo box callbacks reference it during init
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready")

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(12)

        # Left panel: Grid + transport
        left_panel = QVBoxLayout()

        # Title bar
        title_bar = QHBoxLayout()
        title = QLabel("LAUNCHPAD CTRL")
        title.setObjectName("title_label")
        title_bar.addWidget(title)
        title_bar.addStretch()

        self._midi_status = QLabel("⬤ MIDI: Disconnected")
        self._midi_status.setObjectName("status_label")
        self._midi_status.setStyleSheet("color: #ff4444;")
        title_bar.addWidget(self._midi_status)

        left_panel.addLayout(title_bar)

        # Mode selector buttons
        mode_bar = QHBoxLayout()
        self._mode_buttons = {}
        for name in self.mode_manager.mode_names:
            btn = QPushButton(name.upper())
            btn.setObjectName("mode_btn")
            btn.setCheckable(True)
            btn.clicked.connect(partial(self._on_mode_button, name))
            mode_bar.addWidget(btn)
            self._mode_buttons[name] = btn
        left_panel.addLayout(mode_bar)

        # Launchpad grid
        self._grid = LaunchpadGrid()
        self._grid.grid_pressed.connect(self._on_virtual_grid_press)
        self._grid.grid_released.connect(self._on_virtual_grid_release)
        self._grid.grid_file_dropped.connect(self._on_grid_file_dropped)
        self._grid.control_pressed.connect(self._on_virtual_control_press)
        self._grid.control_released.connect(self._on_virtual_control_release)
        left_panel.addWidget(self._grid, stretch=1)

        # Transport bar
        transport = QHBoxLayout()

        self._play_btn = QPushButton("▶ PLAY")
        self._play_btn.setObjectName("play_btn")
        self._play_btn.clicked.connect(self._on_play)
        transport.addWidget(self._play_btn)

        self._stop_btn = QPushButton("⏹ STOP")
        self._stop_btn.setObjectName("stop_btn")
        self._stop_btn.clicked.connect(self._on_stop)
        transport.addWidget(self._stop_btn)

        self._record_btn = QPushButton("⏺ REC")
        self._record_btn.setObjectName("record_btn")
        self._record_btn.setCheckable(True)
        self._record_btn.clicked.connect(self._on_record_toggle)
        transport.addWidget(self._record_btn)

        transport.addSpacing(16)

        self._panic_btn = QPushButton("⚠ PANIC")
        self._panic_btn.setObjectName("panic_btn")
        self._panic_btn.clicked.connect(self._on_panic)
        transport.addWidget(self._panic_btn)

        left_panel.addLayout(transport)

        main_layout.addLayout(left_panel, stretch=3)

        # Right panel: Controls / settings
        right_panel = QVBoxLayout()

        # Tabs for different control sections
        self._tabs = QTabWidget()

        # Mode controls tab
        self._mode_controls_widget = QWidget()
        self._mode_controls_layout = QVBoxLayout(self._mode_controls_widget)
        self._mode_controls_layout.setAlignment(Qt.AlignTop)
        self._tabs.addTab(self._mode_controls_widget, "Mode")

        # Audio settings tab
        audio_tab = self._build_audio_tab()
        self._tabs.addTab(audio_tab, "Audio")

        # MIDI settings tab
        midi_tab = self._build_midi_tab()
        self._tabs.addTab(midi_tab, "MIDI")

        right_panel.addWidget(self._tabs)

        main_layout.addLayout(right_panel, stretch=2)

    def _build_audio_tab(self) -> QWidget:
        """Build the audio settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)

        # Master volume
        vol_group = QGroupBox("Master Volume")
        vol_layout = QHBoxLayout(vol_group)
        self._master_vol_slider = QSlider(Qt.Horizontal)
        self._master_vol_slider.setRange(0, 100)
        self._master_vol_slider.setValue(80)
        self._master_vol_slider.valueChanged.connect(self._on_master_volume)
        vol_layout.addWidget(self._master_vol_slider)
        self._vol_label = QLabel("80%")
        vol_layout.addWidget(self._vol_label)
        layout.addWidget(vol_group)

        # Output device
        out_group = QGroupBox("Output Device")
        out_layout = QVBoxLayout(out_group)
        self._output_combo = QComboBox()
        self._output_combo.currentIndexChanged.connect(self._on_output_device_changed)
        out_layout.addWidget(self._output_combo)
        layout.addWidget(out_group)

        # Input device
        in_group = QGroupBox("Input Device")
        in_layout = QVBoxLayout(in_group)
        self._input_combo = QComboBox()
        self._input_combo.currentIndexChanged.connect(self._on_input_device_changed)
        in_layout.addWidget(self._input_combo)
        layout.addWidget(in_group)

        # Refresh button
        refresh_btn = QPushButton("Refresh Devices")
        refresh_btn.clicked.connect(self._refresh_audio_devices)
        layout.addWidget(refresh_btn)

        self._refresh_audio_devices()

        # --- Virtual Audio Output (feed audio into Discord / games) ---
        virt_group = QGroupBox("Virtual Audio Output")
        virt_layout = QVBoxLayout(virt_group)

        virt_desc = QLabel(
            "Create a virtual microphone that captures this app's audio "
            "(optionally mixed with your real mic). Select "
            f"\"{VirtualAudioRouter.VIRTUAL_MIC}\" as your input device "
            "in Discord, games, etc."
        )
        virt_desc.setWordWrap(True)
        virt_desc.setStyleSheet("color: #aaa; font-size: 11px;")
        virt_layout.addWidget(virt_desc)

        self._virt_mic_check = QPushButton("Enable Virtual Mic")
        self._virt_mic_check.setCheckable(True)
        self._virt_mic_check.setChecked(False)
        self._virt_mic_check.clicked.connect(self._on_virtual_mic_toggled)
        virt_layout.addWidget(self._virt_mic_check)

        self._virt_mix_real_mic = QPushButton("Mix Real Mic Into Virtual Mic")
        self._virt_mix_real_mic.setCheckable(True)
        self._virt_mix_real_mic.setChecked(True)
        self._virt_mix_real_mic.setToolTip(
            "When enabled, your selected input device (real mic) is mixed "
            "into the virtual mic alongside the app audio."
        )
        virt_layout.addWidget(self._virt_mix_real_mic)

        self._virt_status_label = QLabel("")
        self._virt_status_label.setStyleSheet("color: #888; font-size: 11px;")
        virt_layout.addWidget(self._virt_status_label)

        layout.addWidget(virt_group)

        layout.addStretch()
        return widget

    def _build_midi_tab(self) -> QWidget:
        """Build MIDI settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)

        conn_group = QGroupBox("MIDI Connection")
        conn_layout = QVBoxLayout(conn_group)

        self._midi_input_combo = QComboBox()
        conn_layout.addWidget(QLabel("MIDI Input:"))
        conn_layout.addWidget(self._midi_input_combo)

        self._midi_output_combo = QComboBox()
        conn_layout.addWidget(QLabel("MIDI Output:"))
        conn_layout.addWidget(self._midi_output_combo)

        btn_layout = QHBoxLayout()
        connect_btn = QPushButton("Connect")
        connect_btn.clicked.connect(self._on_midi_connect)
        btn_layout.addWidget(connect_btn)

        disconnect_btn = QPushButton("Disconnect")
        disconnect_btn.clicked.connect(self._on_midi_disconnect)
        btn_layout.addWidget(disconnect_btn)

        refresh_midi_btn = QPushButton("Refresh")
        refresh_midi_btn.clicked.connect(self._refresh_midi_ports)
        btn_layout.addWidget(refresh_midi_btn)

        conn_layout.addLayout(btn_layout)
        layout.addWidget(conn_group)

        self._refresh_midi_ports()

        layout.addStretch()
        return widget

    def _build_menu(self):
        """Build the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        new_action = QAction("New Project", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        save_action = QAction("Save Project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)

        load_action = QAction("Load Project", self)
        load_action.setShortcut("Ctrl+O")
        load_action.triggered.connect(self._load_project)
        file_menu.addAction(load_action)

        file_menu.addSeparator()

        export_action = QAction("Export Config (JSON)", self)
        export_action.triggered.connect(self._export_config)
        file_menu.addAction(export_action)

        import_action = QAction("Import Config (JSON)", self)
        import_action.triggered.connect(self._import_config)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _setup_midi_callbacks(self):
        """Wire MIDI events via Qt signals for thread-safe dispatch.

        MIDI callbacks fire from the listener thread; emitting queued signals
        ensures all UI and mode-manager work runs on the main Qt thread.
        """
        self.midi.set_callbacks(
            on_grid_press=lambda row, col: self._sig_midi_grid_press.emit(row, col),
            on_grid_release=lambda row, col: self._sig_midi_grid_release.emit(row, col),
            on_control_press=lambda pos, idx: self._sig_midi_control_press.emit(pos, idx),
            on_control_release=lambda pos, idx: self._sig_midi_control_release.emit(pos, idx),
        )

    def _setup_timers(self):
        """Setup periodic UI update timer."""
        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._periodic_update)
        self._ui_timer.start(50)  # 20fps UI updates

    # --- MIDI Event Handlers ---

    def _on_midi_grid_press(self, row, col):
        self.mode_manager.on_grid_press(row, col)

    def _on_midi_grid_release(self, row, col):
        self.mode_manager.on_grid_release(row, col)

    def _on_midi_control_press(self, position, index):
        """Handle control button presses - global controls + mode dispatch."""
        if position == "top":
            if index == 0:
                # Prev mode
                self.mode_manager.prev_mode()
                return
            elif index == 1:
                # Next mode
                self.mode_manager.next_mode()
                return
            elif index == 2:
                # Stop all
                self._on_panic()
                return
            elif index == 3:
                # Play/stop toggle
                self._on_play()
                return
            elif index == 4:
                # Record toggle
                self._on_record_toggle()
                return
            elif index == 5:
                # Tap tempo
                if isinstance(self.mode_manager.current_mode, StepSequencerMode):
                    self._sequencer.tap_tempo()
                return

        # Dispatch remaining to mode
        self.mode_manager.on_control_press(position, index)

    def _on_midi_control_release(self, position, index):
        self.mode_manager.on_control_release(position, index)

    # --- Virtual Grid Handlers ---

    def _on_virtual_grid_press(self, row, col):
        self.mode_manager.on_grid_press(row, col)

    def _on_virtual_grid_release(self, row, col):
        self.mode_manager.on_grid_release(row, col)

    def _on_virtual_control_press(self, position, index):
        self._on_midi_control_press(position, index)

    def _on_virtual_control_release(self, position, index):
        self._on_midi_control_release(position, index)

    # --- Transport ---

    def _on_play(self):
        # Always toggle the sequencer so it can play in the background
        self._sequencer.toggle_playback()
        self._play_btn.setText("⏸ PAUSE" if self._sequencer.playing else "▶ PLAY")

    def _on_stop(self):
        # Always stop the sequencer (it may be playing in the background)
        self._sequencer.stop_playback()
        self._play_btn.setText("▶ PLAY")
        self.audio.stop_all()

    def _on_record_toggle(self):
        """Transport bar record button — delegates to recorder mode."""
        from launchpad_ctrl.modes.recorder import RecState

        if isinstance(self.mode_manager.current_mode, RecorderMode):
            # In recorder mode: use the recorder's state machine
            if self._recorder.state == RecState.RECORDING:
                self._recorder.stop_recording()
                self._record_btn.setChecked(False)
                if self._recorder.is_assigning:
                    self._statusbar.showMessage("Recording captured! Click a pad to assign it.")
                else:
                    self._statusbar.showMessage("Recording too short, discarded.")
            elif self._recorder.state == RecState.IDLE:
                self._recorder.start_recording()
                self._record_btn.setChecked(True)
                self._statusbar.showMessage("Recording... press a pad or Stop when done")
            elif self._recorder.state == RecState.ASSIGNING:
                # Discard pending and start a new recording
                self._recorder.start_recording()
                self._record_btn.setChecked(True)
                self._statusbar.showMessage("Previous discarded. Recording...")
            self._rebuild_mode_controls()
        else:
            # Not in recorder mode: basic audio record to buffer
            if self.audio.is_recording:
                self.audio.stop_recording()
                self._record_btn.setChecked(False)
                self._statusbar.showMessage("Recording stopped")
            else:
                self.audio.start_recording()
                self._record_btn.setChecked(True)
                self._statusbar.showMessage("Recording...")

    def _on_panic(self):
        """Stop all sounds immediately."""
        self.audio.stop_all()
        # Always stop sequencer (it may be playing in the background)
        self._sequencer.stop_playback()
        self._play_btn.setText("▶ PLAY")
        if isinstance(self.mode_manager.current_mode, SoundboardMode):
            self._soundboard.stop_all_sounds()
        self._statusbar.showMessage("All sounds stopped")

    # --- Mode Controls ---

    def _on_mode_button(self, name):
        self.mode_manager.switch_mode(name)

    def _on_mode_changed(self, name):
        """Update UI when mode changes."""
        for n, btn in self._mode_buttons.items():
            btn.setChecked(n == name)
        self._rebuild_mode_controls()
        # Keep play button in sync with sequencer state (it may be running in background)
        self._play_btn.setText("⏸ PAUSE" if self._sequencer.playing else "▶ PLAY")
        self._statusbar.showMessage(f"Mode: {name}")
        self._update_grid_display()

    def _rebuild_mode_controls(self):
        """Rebuild the mode-specific control panel."""
        # Clear existing
        while self._mode_controls_layout.count():
            item = self._mode_controls_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        mode = self.mode_manager.current_mode

        if isinstance(mode, StepSequencerMode):
            self._build_sequencer_controls()
        elif isinstance(mode, SoundboardMode):
            self._build_soundboard_controls()
        elif isinstance(mode, RecorderMode):
            self._build_recorder_controls()

    def _build_sequencer_controls(self):
        """Build sequencer-specific controls."""
        layout = self._mode_controls_layout

        # Mode title
        title = QLabel("STEP SEQUENCER")
        title.setObjectName("mode_label")
        layout.addWidget(title)

        # BPM control
        bpm_group = QGroupBox("Tempo")
        bpm_layout = QHBoxLayout(bpm_group)

        self._bpm_label = QLabel(f"{self._sequencer.bpm:.0f}")
        self._bpm_label.setObjectName("bpm_label")
        bpm_layout.addWidget(self._bpm_label)

        bpm_layout.addWidget(QLabel("BPM"))

        bpm_spin = QDoubleSpinBox()
        bpm_spin.setRange(20, 300)
        bpm_spin.setValue(self._sequencer.bpm)
        bpm_spin.setSingleStep(1)
        bpm_spin.valueChanged.connect(self._on_bpm_changed)
        self._bpm_spin = bpm_spin
        bpm_layout.addWidget(bpm_spin)

        tap_btn = QPushButton("TAP")
        tap_btn.clicked.connect(self._sequencer.tap_tempo)
        bpm_layout.addWidget(tap_btn)

        layout.addWidget(bpm_group)

        # Row sample assignment
        samples_group = QGroupBox("Row Samples")
        samples_layout = QVBoxLayout(samples_group)

        for row in range(8):
            row_layout = QHBoxLayout()
            row_layout.addWidget(QLabel(f"R{row + 1}:"))

            sample_path = self._sequencer.get_sample(row)
            label = QLabel(os.path.basename(sample_path) if sample_path else "Empty")
            label.setStyleSheet("color: #888;" if not sample_path else "color: #aaffaa;")
            row_layout.addWidget(label, stretch=1)

            load_btn = QPushButton("Load")
            load_btn.setMaximumWidth(60)
            load_btn.clicked.connect(partial(self._load_sequencer_sample, row))
            row_layout.addWidget(load_btn)

            vol_slider = QSlider(Qt.Horizontal)
            vol_slider.setRange(0, 100)
            vol_slider.setValue(int(self._sequencer._row_volumes.get(row, 0.8) * 100))
            vol_slider.setMaximumWidth(80)
            vol_slider.valueChanged.connect(partial(self._on_row_volume, row))
            row_layout.addWidget(vol_slider)

            samples_layout.addLayout(row_layout)

        layout.addWidget(samples_group)

        # Clear grid button
        clear_btn = QPushButton("Clear Grid")
        clear_btn.clicked.connect(self._sequencer.clear_grid)
        layout.addWidget(clear_btn)

        layout.addStretch()

    def _build_soundboard_controls(self):
        """Build soundboard-specific controls."""
        layout = self._mode_controls_layout

        title = QLabel("SOUNDBOARD")
        title.setObjectName("mode_label")
        layout.addWidget(title)

        info = QLabel("Click a pad on the grid, then assign a sound below.")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Pad assignment
        assign_group = QGroupBox("Pad Assignment")
        assign_layout = QVBoxLayout(assign_group)

        self._sb_selected_label = QLabel("No pad selected")
        assign_layout.addWidget(self._sb_selected_label)

        load_btn = QPushButton("Load Sound File")
        load_btn.clicked.connect(self._load_soundboard_sample)
        assign_layout.addWidget(load_btn)

        # Color selector
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("Color:"))
        self._sb_color_combo = QComboBox()
        for name in LPColor.names():
            if name != "off":
                self._sb_color_combo.addItem(name)
        self._sb_color_combo.currentTextChanged.connect(self._on_sb_color_changed)
        color_layout.addWidget(self._sb_color_combo)
        assign_layout.addLayout(color_layout)

        # Volume for pad
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("Volume:"))
        self._sb_vol_slider = QSlider(Qt.Horizontal)
        self._sb_vol_slider.setRange(0, 100)
        self._sb_vol_slider.setValue(80)
        self._sb_vol_slider.valueChanged.connect(self._on_sb_volume_changed)
        vol_layout.addWidget(self._sb_vol_slider)
        assign_layout.addLayout(vol_layout)

        remove_btn = QPushButton("Remove Sound")
        remove_btn.clicked.connect(self._remove_soundboard_pad)
        assign_layout.addWidget(remove_btn)

        layout.addWidget(assign_group)

        # Bank info
        bank_group = QGroupBox("Banks")
        bank_layout = QHBoxLayout(bank_group)
        self._bank_label = QLabel(f"Bank {self._soundboard.current_bank + 1}/{self._soundboard.num_banks}")
        bank_layout.addWidget(self._bank_label)

        add_bank_btn = QPushButton("+ Bank")
        add_bank_btn.clicked.connect(self._add_soundboard_bank)
        bank_layout.addWidget(add_bank_btn)

        layout.addWidget(bank_group)

        # Stop all
        stop_all_btn = QPushButton("⚠ Stop All Sounds")
        stop_all_btn.setObjectName("panic_btn")
        stop_all_btn.clicked.connect(self._soundboard.stop_all_sounds)
        layout.addWidget(stop_all_btn)

        layout.addStretch()

    def _build_recorder_controls(self):
        """Build recorder-specific controls. UI adapts to recorder state."""
        layout = self._mode_controls_layout
        from launchpad_ctrl.modes.recorder import RecState

        title = QLabel("RECORDER")
        title.setObjectName("mode_label")
        layout.addWidget(title)

        # State-aware status banner
        state = self._recorder.state
        if state == RecState.RECORDING:
            dur = self._recorder.recording_duration
            status_text = f"🔴 RECORDING ({dur:.1f}s)\nPress a pad to stop & assign, or press Stop"
            status_color = "#ff4444"
            status_bg = "#1a0808"
        elif state == RecState.ASSIGNING:
            status_text = "🟢 READY TO ASSIGN\nPress any pad on the grid to place the recording"
            status_color = "#44ff44"
            status_bg = "#081a08"
        else:
            status_text = "⏸ IDLE\nPress Record to start capturing audio"
            status_color = "#aaaaaa"
            status_bg = "#151515"

        self._rec_status = QLabel(status_text)
        self._rec_status.setWordWrap(True)
        self._rec_status.setStyleSheet(
            f"color: {status_color}; font-size: 12px; font-weight: bold; "
            f"padding: 10px; border: 1px solid #333; border-radius: 4px; "
            f"background: {status_bg};"
        )
        layout.addWidget(self._rec_status)

        if state == RecState.ASSIGNING and self._recorder.has_pending_recording:
            trim_group = QGroupBox("Trim Pending Recording")
            trim_layout = QVBoxLayout(trim_group)

            duration = self._recorder.pending_duration
            self._trim_info_label = QLabel(
                f"Original: {duration:.2f}s • Selected: {self._recorder.trim_duration:.2f}s"
            )
            self._trim_info_label.setStyleSheet("color: #aaa; font-size: 10px;")
            trim_layout.addWidget(self._trim_info_label)

            start_row = QHBoxLayout()
            start_row.addWidget(QLabel("Start"))
            self._trim_start_slider = QSlider(Qt.Horizontal)
            self._trim_start_slider.setRange(0, int(duration * 1000))
            self._trim_start_slider.setValue(int(self._recorder.trim_start_sec * 1000))
            self._trim_start_slider.valueChanged.connect(self._on_trim_start_changed)
            self._trim_start_slider.sliderReleased.connect(self._preview_trimmed_pending)
            self._trim_start_slider.setFixedHeight(28)
            self._trim_start_slider.setStyleSheet(
                "QSlider::groove:horizontal {height: 8px; background: #2f2f2f; border-radius: 4px;}"
                "QSlider::handle:horizontal {background: #44ff44; border: 1px solid #173; width: 20px; margin: -8px 0; border-radius: 10px;}"
            )
            start_row.addWidget(self._trim_start_slider)
            self._trim_start_spin = QDoubleSpinBox()
            self._trim_start_spin.setDecimals(2)
            self._trim_start_spin.setRange(0.0, duration)
            self._trim_start_spin.setSingleStep(0.01)
            self._trim_start_spin.setValue(self._recorder.trim_start_sec)
            self._trim_start_spin.valueChanged.connect(self._on_trim_start_spin_changed)
            self._trim_start_spin.setSuffix(" s")
            self._trim_start_spin.setMinimumWidth(80)
            start_row.addWidget(self._trim_start_spin)
            trim_layout.addLayout(start_row)

            end_row = QHBoxLayout()
            end_row.addWidget(QLabel("End"))
            self._trim_end_slider = QSlider(Qt.Horizontal)
            self._trim_end_slider.setRange(0, int(duration * 1000))
            self._trim_end_slider.setValue(int(self._recorder.trim_end_sec * 1000))
            self._trim_end_slider.valueChanged.connect(self._on_trim_end_changed)
            self._trim_end_slider.sliderReleased.connect(self._preview_trimmed_pending)
            self._trim_end_slider.setFixedHeight(28)
            self._trim_end_slider.setStyleSheet(
                "QSlider::groove:horizontal {height: 8px; background: #2f2f2f; border-radius: 4px;}"
                "QSlider::handle:horizontal {background: #44ff44; border: 1px solid #173; width: 20px; margin: -8px 0; border-radius: 10px;}"
            )
            end_row.addWidget(self._trim_end_slider)
            self._trim_end_spin = QDoubleSpinBox()
            self._trim_end_spin.setDecimals(2)
            self._trim_end_spin.setRange(0.0, duration)
            self._trim_end_spin.setSingleStep(0.01)
            self._trim_end_spin.setValue(self._recorder.trim_end_sec)
            self._trim_end_spin.valueChanged.connect(self._on_trim_end_spin_changed)
            self._trim_end_spin.setSuffix(" s")
            self._trim_end_spin.setMinimumWidth(80)
            end_row.addWidget(self._trim_end_spin)
            trim_layout.addLayout(end_row)

            preview_row = QHBoxLayout()
            preview_trim_btn = QPushButton("▶ Preview Selected")
            preview_trim_btn.clicked.connect(self._preview_trimmed_pending)
            preview_row.addWidget(preview_trim_btn)
            preview_full_btn = QPushButton("▶ Preview Full")
            preview_full_btn.clicked.connect(self._preview_full_pending)
            preview_row.addWidget(preview_full_btn)
            trim_layout.addLayout(preview_row)

            layout.addWidget(trim_group)

        # Contextual instructions
        if state == RecState.IDLE:
            info = QLabel(
                "How to record:\n"
                "1. Press ⏺ Record below\n"
                "2. Speak or play your sound\n"
                "3. Press any pad on the grid to stop & assign\n"
                "   — or press Stop first, then pick a pad\n\n"
                "Tap an amber pad to preview its recording."
            )
            info.setWordWrap(True)
            info.setStyleSheet("color: #666; font-size: 10px;")
            layout.addWidget(info)

        # Action buttons — change based on state
        rec_group = QGroupBox("Controls")
        rec_layout = QVBoxLayout(rec_group)

        if state == RecState.RECORDING:
            stop_btn = QPushButton("⏹ Stop Recording")
            stop_btn.setObjectName("stop_btn")
            stop_btn.clicked.connect(self._on_recorder_stop)
            rec_layout.addWidget(stop_btn)

        elif state == RecState.ASSIGNING:
            assign_label = QLabel("👆 Click a pad on the grid above!")
            assign_label.setStyleSheet("color: #44ff44; font-size: 12px; font-weight: bold;")
            assign_label.setAlignment(Qt.AlignCenter)
            rec_layout.addWidget(assign_label)

            discard_btn = QPushButton("✕ Discard Recording")
            discard_btn.setStyleSheet("color: #ff8888;")
            discard_btn.clicked.connect(self._on_recorder_discard)
            rec_layout.addWidget(discard_btn)

            re_record_btn = QPushButton("⏺ Record Again")
            re_record_btn.setObjectName("record_btn")
            re_record_btn.clicked.connect(self._on_recorder_record)
            rec_layout.addWidget(re_record_btn)

        else:  # IDLE
            rec_btn = QPushButton("⏺ Start Recording")
            rec_btn.setObjectName("record_btn")
            rec_btn.clicked.connect(self._on_recorder_record)
            rec_layout.addWidget(rec_btn)

        layout.addWidget(rec_group)

        # Import from file (only in IDLE)
        if state == RecState.IDLE:
            import_group = QGroupBox("Import File")
            import_layout = QVBoxLayout(import_group)

            sel = self._recorder.selected_pad
            if sel:
                import_info = QLabel(f"Will import to pad [{sel[0]},{sel[1]}]")
                import_info.setStyleSheet("color: #aaa; font-size: 10px;")
            else:
                import_info = QLabel("Select a pad first, then import a file.")
                import_info.setStyleSheet("color: #666; font-size: 10px;")
            import_layout.addWidget(import_info)

            import_btn = QPushButton("📁 Import Audio File")
            import_btn.clicked.connect(self._import_to_recorder_pad)
            import_btn.setEnabled(sel is not None)
            import_layout.addWidget(import_btn)

            layout.addWidget(import_group)

        # Selected pad details
        if self._recorder.selected_pad and state == RecState.IDLE:
            r, c = self._recorder.selected_pad
            pad_filepath = self._recorder.get_recording_path(r, c)
            if pad_filepath:
                pad_group = QGroupBox(f"Pad [{r},{c}]")
                pad_layout = QVBoxLayout(pad_group)

                fname_label = QLabel(os.path.basename(pad_filepath))
                fname_label.setStyleSheet("color: #aaffaa;")
                pad_layout.addWidget(fname_label)

                btn_row = QHBoxLayout()
                preview_btn = QPushButton("▶ Preview")
                # Use _ to absorb the bool that clicked.connect sends
                preview_btn.clicked.connect(lambda _, fp=pad_filepath: self.audio.play_sound(fp))
                btn_row.addWidget(preview_btn)

                del_btn = QPushButton("🗑 Delete")
                del_btn.setStyleSheet("color: #ff8888;")
                del_btn.clicked.connect(lambda _, rr=r, cc=c: (
                    self._recorder.delete_pad(rr, cc),
                    self._rebuild_mode_controls()
                ))
                btn_row.addWidget(del_btn)
                pad_layout.addLayout(btn_row)

                # Send to Soundboard button
                sb_btn = QPushButton("📤 Send to Soundboard")
                sb_btn.setStyleSheet("color: #aaaaff;")
                sb_btn.clicked.connect(lambda _, fp=pad_filepath, rr=r, cc=c: self._send_recording_to_soundboard(rr, cc, fp))
                pad_layout.addWidget(sb_btn)

                layout.addWidget(pad_group)

        # Recordings summary
        num_recs = len(self._recorder._recordings)
        recs_group = QGroupBox(f"All Recordings ({num_recs})")
        recs_layout = QVBoxLayout(recs_group)

        if self._recorder._recordings:
            for (row, col), filepath in self._recorder._recordings.items():
                lbl = QLabel(f"  [{row},{col}] {os.path.basename(filepath)}")
                lbl.setStyleSheet("color: #aaffaa; font-size: 10px;")
                recs_layout.addWidget(lbl)
        else:
            lbl = QLabel("  No recordings yet")
            lbl.setStyleSheet("color: #555; font-size: 10px;")
            recs_layout.addWidget(lbl)

        layout.addWidget(recs_group)

        if num_recs > 0:
            clear_btn = QPushButton("🗑 Clear All Recordings")
            clear_btn.setStyleSheet("color: #ff6666;")
            clear_btn.clicked.connect(lambda _: (
                self._recorder.clear_all(),
                self._rebuild_mode_controls()
            ))
            layout.addWidget(clear_btn)

        layout.addStretch()

    # --- Audio Device Handling ---

    def _refresh_audio_devices(self):
        # Force re-enumeration so newly created virtual devices appear.
        AudioDevice.refresh()

        # Block signals while populating to avoid overriding the system
        # default output device before the audio stream is started.
        self._output_combo.blockSignals(True)
        self._input_combo.blockSignals(True)

        self._output_combo.clear()
        self._input_combo.clear()

        outputs = AudioDevice.list_output_devices()
        for dev in outputs:
            info = AudioDevice.resolve_device_info(dev)
            self._output_combo.addItem(dev['name'], info)

        inputs = AudioDevice.list_input_devices()
        for dev in inputs:
            info = AudioDevice.resolve_device_info(dev)
            self._input_combo.addItem(dev['name'], info)

        # Select the current default device in the combo without triggering
        # a change.  Match by sounddevice index when available.
        defaults = AudioDevice.get_default_devices()
        if defaults[1] is not None:
            for i in range(self._output_combo.count()):
                sd_idx, _pa = self._output_combo.itemData(i)
                if sd_idx == defaults[1]:
                    self._output_combo.setCurrentIndex(i)
                    break
        if defaults[0] is not None:
            for i in range(self._input_combo.count()):
                sd_idx, _pa = self._input_combo.itemData(i)
                if sd_idx == defaults[0]:
                    self._input_combo.setCurrentIndex(i)
                    break

        self._output_combo.blockSignals(False)
        self._input_combo.blockSignals(False)

    def _on_output_device_changed(self, index):
        if index >= 0:
            info = self._output_combo.itemData(index)
            if info is not None:
                sd_idx, pa_name = info
                AudioDevice.set_output_device(sd_idx, pa_name)
                # Restart the audio stream so it uses the newly selected device
                self.audio.restart()
                self._statusbar.showMessage(f"Output: {self._output_combo.currentText()}")

    def _on_input_device_changed(self, index):
        if index >= 0:
            info = self._input_combo.itemData(index)
            if info is not None:
                sd_idx, pa_name = info
                AudioDevice.set_input_device(sd_idx, pa_name)
                self._statusbar.showMessage(f"Input: {self._input_combo.currentText()}")

    def _on_master_volume(self, value):
        self.audio.master_volume = value / 100.0
        self._vol_label.setText(f"{value}%")

    # --- Virtual Audio Output ---

    def _on_virtual_mic_toggled(self, checked):
        if checked:
            # Determine the real mic source (from Input Device combo)
            mic_source = None
            if self._virt_mix_real_mic.isChecked():
                mic_source = AudioDevice._selected_input_pa
                if not mic_source:
                    idx = self._input_combo.currentIndex()
                    if idx >= 0:
                        info = self._input_combo.itemData(idx)
                        if info:
                            _, mic_source = info

            # Determine the current output sink for local monitoring
            monitor_sink = AudioDevice._selected_output_pa
            if not monitor_sink:
                idx = self._output_combo.currentIndex()
                if idx >= 0:
                    info = self._output_combo.itemData(idx)
                    if info:
                        _, monitor_sink = info

            ok = self.virtual_router.enable(
                mic_source=mic_source,
                monitor_sink=monitor_sink,
            )
            if ok:
                # Refresh devices so the new sinks/sources appear in combos
                self._refresh_audio_devices()

                # Auto-select SoundboardSink as output so the app routes there
                for i in range(self._output_combo.count()):
                    info = self._output_combo.itemData(i)
                    if info and info[1] == VirtualAudioRouter.SOUNDBOARD_SINK:
                        self._output_combo.setCurrentIndex(i)
                        break

                self._virt_mic_check.setText("Disable Virtual Mic")
                self._virt_mic_check.setStyleSheet(
                    "background-color: #2a5a2a; border: 1px solid #4a8a4a;"
                )
                self._virt_status_label.setText(
                    f"Active — select \"{VirtualAudioRouter.VIRTUAL_MIC}\" "
                    f"in Discord / your game"
                )
                self._virt_status_label.setStyleSheet(
                    "color: #6c6; font-size: 11px;"
                )
                self._statusbar.showMessage("Virtual mic enabled")
            else:
                self._virt_mic_check.setChecked(False)
                self._virt_status_label.setText(
                    "Failed — is PulseAudio / PipeWire running?"
                )
                self._virt_status_label.setStyleSheet(
                    "color: #c66; font-size: 11px;"
                )
        else:
            self.virtual_router.disable()
            self._virt_mic_check.setText("Enable Virtual Mic")
            self._virt_mic_check.setStyleSheet("")
            self._virt_status_label.setText("")
            self._refresh_audio_devices()
            self._statusbar.showMessage("Virtual mic disabled")

    # --- MIDI Connection ---

    def _refresh_midi_ports(self):
        self._midi_input_combo.clear()
        self._midi_output_combo.clear()
        ports = LaunchpadMIDI.list_midi_ports()
        for name in ports["inputs"]:
            self._midi_input_combo.addItem(name)
        for name in ports["outputs"]:
            self._midi_output_combo.addItem(name)

    def _try_midi_connect(self):
        """Try to auto-connect to Launchpad."""
        if self.midi.connect():
            self._midi_status.setText("⬤ MIDI: Connected")
            self._midi_status.setStyleSheet("color: #44ff44;")
            self._statusbar.showMessage("Launchpad connected!")
        else:
            self._midi_status.setText("⬤ MIDI: Disconnected")
            self._midi_status.setStyleSheet("color: #ff4444;")

    def _on_midi_connect(self):
        inp = self._midi_input_combo.currentText()
        out = self._midi_output_combo.currentText()
        if self.midi.connect(inp, out):
            self._midi_status.setText("⬤ MIDI: Connected")
            self._midi_status.setStyleSheet("color: #44ff44;")
            self._statusbar.showMessage("MIDI connected!")
        else:
            QMessageBox.warning(self, "MIDI Error", "Could not connect to the selected MIDI device.")

    def _on_midi_disconnect(self):
        self.midi.disconnect()
        self._midi_status.setText("⬤ MIDI: Disconnected")
        self._midi_status.setStyleSheet("color: #ff4444;")

    # --- Sequencer Controls ---

    def _on_bpm_changed(self, value):
        self._sequencer.bpm = value
        self._bpm_label.setText(f"{value:.0f}")

    def _on_row_volume(self, row, value):
        self._sequencer.set_row_volume(row, value / 100.0)

    def _load_sequencer_sample(self, row):
        filepath, _ = QFileDialog.getOpenFileName(
            self, f"Load Sample for Row {row + 1}",
            "", "Audio Files (*.wav *.ogg *.mp3);;All Files (*)"
        )
        if filepath:
            self._sequencer.set_sample(row, filepath)
            self._rebuild_mode_controls()
            self._statusbar.showMessage(f"Loaded {os.path.basename(filepath)} to row {row + 1}")

    # --- Soundboard Controls ---

    # Track which pad is selected for assignment in the soundboard UI
    _sb_selected_pad = None

    def _load_soundboard_sample(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Load Sound",
            "", "Audio Files (*.wav *.ogg *.mp3);;All Files (*)"
        )
        if filepath and self._sb_selected_pad:
            row, col = self._sb_selected_pad
            color = self._sb_color_combo.currentText() or "green"
            volume = self._sb_vol_slider.value() / 100.0
            config = PadConfig(filepath=filepath, color=color, volume=volume,
                             label=os.path.basename(filepath)[:6])
            self._soundboard.set_pad(row, col, config)
            self._statusbar.showMessage(f"Assigned {os.path.basename(filepath)} to pad [{row},{col}]")
            self._update_grid_display()

    def _on_sb_color_changed(self, color_name):
        if self._sb_selected_pad:
            pad = self._soundboard.get_pad(*self._sb_selected_pad)
            if pad:
                pad.color = color_name
                self._soundboard.refresh_leds()
                self._update_grid_display()

    def _on_sb_volume_changed(self, value):
        if self._sb_selected_pad:
            pad = self._soundboard.get_pad(*self._sb_selected_pad)
            if pad:
                pad.volume = value / 100.0

    def _remove_soundboard_pad(self):
        if self._sb_selected_pad:
            self._soundboard.remove_pad(*self._sb_selected_pad)
            self._sb_selected_pad = None
            self._sb_selected_label.setText("No pad selected")
            self._update_grid_display()

    def _add_soundboard_bank(self):
        self._soundboard.add_bank()
        self._bank_label.setText(
            f"Bank {self._soundboard.current_bank + 1}/{self._soundboard.num_banks}"
        )

    # --- Drag and Drop ---

    def _on_grid_file_dropped(self, row: int, col: int, filepath: str):
        """Handle a sound file dropped onto a grid pad."""
        mode = self.mode_manager.current_mode

        if isinstance(mode, SoundboardMode):
            color = "green"
            volume = 0.8
            # Use current UI settings if a pad was already selected
            if hasattr(self, "_sb_color_combo"):
                color = self._sb_color_combo.currentText() or "green"
            if hasattr(self, "_sb_vol_slider"):
                volume = self._sb_vol_slider.value() / 100.0
            config = PadConfig(
                filepath=filepath, color=color, volume=volume,
                label=os.path.basename(filepath)[:6],
            )
            self._soundboard.set_pad(row, col, config)
            # Update selection to the dropped pad
            self._sb_selected_pad = (row, col)
            self._sb_selected_label.setText(
                f"Pad [{row},{col}]: {os.path.basename(filepath)}"
            )
            self._statusbar.showMessage(
                f"Dropped {os.path.basename(filepath)} onto pad [{row},{col}]"
            )
            self._update_grid_display()

        elif isinstance(mode, StepSequencerMode):
            self._sequencer.set_sample(row, filepath)
            self._rebuild_mode_controls()
            self._statusbar.showMessage(
                f"Dropped {os.path.basename(filepath)} onto sequencer row {row + 1}"
            )

    # --- Recorder Controls ---

    def _on_recorder_record(self):
        """Start recording. If in ASSIGNING state, discards pending first."""
        self._recorder.start_recording()
        self._rebuild_mode_controls()
        self._statusbar.showMessage("Recording... press a pad or Stop when done")

    def _on_recorder_stop(self):
        """Stop recording. Enters ASSIGNING state."""
        self._recorder.stop_recording()
        self._rebuild_mode_controls()
        if self._recorder.is_assigning:
            self._statusbar.showMessage("Recording captured! Now click a pad to assign it.")
        else:
            self._statusbar.showMessage("Recording too short, discarded.")

    def _on_recorder_discard(self):
        """Discard pending recording."""
        self._recorder.discard_pending()
        self._rebuild_mode_controls()
        self._statusbar.showMessage("Recording discarded.")

    def _on_trim_start_changed(self, value: int):
        if not self._recorder.has_pending_recording:
            return
        start_sec = value / 1000.0
        self._recorder.set_trim(start_sec, self._recorder.trim_end_sec)
        self._sync_trim_controls()

    def _on_trim_end_changed(self, value: int):
        if not self._recorder.has_pending_recording:
            return
        end_sec = value / 1000.0
        self._recorder.set_trim(self._recorder.trim_start_sec, end_sec)
        self._sync_trim_controls()

    def _on_trim_start_spin_changed(self, value: float):
        if not self._recorder.has_pending_recording:
            return
        self._recorder.set_trim(value, self._recorder.trim_end_sec)
        self._sync_trim_controls()

    def _on_trim_end_spin_changed(self, value: float):
        if not self._recorder.has_pending_recording:
            return
        self._recorder.set_trim(self._recorder.trim_start_sec, value)
        self._sync_trim_controls()

    def _sync_trim_controls(self):
        if not self._recorder.has_pending_recording:
            return
        blockers = []
        controls = [
            getattr(self, "_trim_start_slider", None),
            getattr(self, "_trim_end_slider", None),
            getattr(self, "_trim_start_spin", None),
            getattr(self, "_trim_end_spin", None),
        ]
        for ctrl in controls:
            if ctrl is not None:
                blockers.append(ctrl.blockSignals(True))

        if getattr(self, "_trim_start_slider", None) is not None:
            self._trim_start_slider.setValue(int(self._recorder.trim_start_sec * 1000))
        if getattr(self, "_trim_end_slider", None) is not None:
            self._trim_end_slider.setValue(int(self._recorder.trim_end_sec * 1000))
        if getattr(self, "_trim_start_spin", None) is not None:
            self._trim_start_spin.setValue(self._recorder.trim_start_sec)
        if getattr(self, "_trim_end_spin", None) is not None:
            self._trim_end_spin.setValue(self._recorder.trim_end_sec)
        if getattr(self, "_trim_info_label", None) is not None:
            self._trim_info_label.setText(
                f"Original: {self._recorder.pending_duration:.2f}s • Selected: {self._recorder.trim_duration:.2f}s"
            )

        idx = 0
        for ctrl in controls:
            if ctrl is not None:
                ctrl.blockSignals(blockers[idx])
                idx += 1

    def _preview_trimmed_pending(self):
        if not self._recorder.has_pending_recording:
            return
        self.audio.stop_all()
        played = self._recorder.preview_pending(trimmed=True)
        if played is None:
            self._statusbar.showMessage("Selected trim is too short to preview.")

    def _preview_full_pending(self):
        if not self._recorder.has_pending_recording:
            return
        self.audio.stop_all()
        self._recorder.preview_pending(trimmed=False)

    def _import_to_recorder_pad(self):
        """Import an audio file directly to the currently selected pad."""
        sel = self._recorder.selected_pad
        if not sel:
            self._statusbar.showMessage("Select a pad first by clicking on the grid.")
            return

        filepath, _ = QFileDialog.getOpenFileName(
            self, "Import Sound File",
            "", "Audio Files (*.wav *.ogg *.mp3);;All Files (*)"
        )
        if filepath:
            self._recorder.assign_file_to_pad(sel[0], sel[1], filepath)
            # Also auto-send to soundboard
            self._send_recording_to_soundboard(sel[0], sel[1], filepath, quiet=True)
            self._rebuild_mode_controls()
            self._statusbar.showMessage(f"Imported {os.path.basename(filepath)} to pad [{sel[0]},{sel[1]}] (also added to Soundboard)")

    def _send_recording_to_soundboard(self, row: int, col: int, filepath: str, quiet: bool = False):
        """Send a recording to the Soundboard on the same pad position."""
        config = PadConfig(
            filepath=filepath,
            color="amber",
            volume=0.8,
            label=os.path.basename(filepath)[:6],
        )
        self._soundboard.set_pad(row, col, config)
        if not quiet:
            self._statusbar.showMessage(
                f"Sent {os.path.basename(filepath)} to Soundboard pad [{row},{col}]"
            )

    # --- Project Management ---

    def _new_project(self):
        reply = QMessageBox.question(
            self, "New Project",
            "Create a new project? Unsaved changes will be lost.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._sequencer.clear_grid()
            self._sequencer._row_samples.clear()
            self._soundboard._banks = [{}]
            self._soundboard._current_bank = 0
            self._recorder._recordings.clear()
            if self.mode_manager.current_mode:
                self.mode_manager.current_mode.refresh_leds()
            self._rebuild_mode_controls()
            self._statusbar.showMessage("New project created")

    def _save_project(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Project",
            PROJECTS_DIR, "LaunchPad Project (*.lpproj);;JSON (*.json)"
        )
        if filepath:
            config = self.mode_manager.get_project_config()
            config["_meta"] = {
                "version": "1.0",
                "app": "LaunchPad Controller",
            }
            with open(filepath, "w") as f:
                json.dump(config, f, indent=2)
            self._statusbar.showMessage(f"Project saved: {os.path.basename(filepath)}")

    def _load_project(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Load Project",
            PROJECTS_DIR, "LaunchPad Project (*.lpproj);;JSON (*.json);;All Files (*)"
        )
        if filepath:
            try:
                with open(filepath, "r") as f:
                    config = json.load(f)
                config.pop("_meta", None)
                self.mode_manager.load_project_config(config)
                self._rebuild_mode_controls()
                self._update_grid_display()
                self._statusbar.showMessage(f"Project loaded: {os.path.basename(filepath)}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load project:\n{e}")

    def _export_config(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export Config",
            "", "JSON Files (*.json)"
        )
        if filepath:
            config = self.mode_manager.get_project_config()
            with open(filepath, "w") as f:
                json.dump(config, f, indent=2)
            self._statusbar.showMessage("Config exported")

    def _import_config(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Import Config",
            "", "JSON Files (*.json);;All Files (*)"
        )
        if filepath:
            try:
                with open(filepath, "r") as f:
                    config = json.load(f)
                config.pop("_meta", None)
                self.mode_manager.load_project_config(config)
                self._rebuild_mode_controls()
                self._update_grid_display()
                self._statusbar.showMessage("Config imported")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to import config:\n{e}")

    # --- Periodic Updates ---

    def _periodic_update(self):
        """Called by timer for regular UI updates."""
        mode = self.mode_manager.current_mode
        if mode:
            mode.tick(0.05)
            self._update_grid_display()

        # Update sequencer BPM display if it exists
        if isinstance(mode, StepSequencerMode) and hasattr(self, '_bpm_label'):
            self._bpm_label.setText(f"{self._sequencer.bpm:.0f}")
            if hasattr(self, '_bpm_spin'):
                self._bpm_spin.blockSignals(True)
                self._bpm_spin.setValue(self._sequencer.bpm)
                self._bpm_spin.blockSignals(False)

    def _on_ui_update(self):
        """Called by modes when they want to trigger a UI refresh.

        This may be called from any thread (MIDI listener, sequencer playback,
        or the main thread), so we emit a queued signal to ensure all widget
        work happens on the main Qt thread.
        """
        self._sig_ui_update.emit()

    def _handle_ui_update(self):
        """Process a UI refresh request on the main Qt thread."""
        self._update_grid_display()
        # Rebuild recorder controls when state changes (recording started/stopped/assigned)
        if isinstance(self.mode_manager.current_mode, RecorderMode):
            self._rebuild_mode_controls()

    def _update_grid_display(self):
        """Sync the virtual grid with the current mode state."""
        mode = self.mode_manager.current_mode
        if mode:
            grid_state = mode.get_grid_state()
            self._grid.update_from_grid_state(grid_state)

        # Handle soundboard pad selection tracking
        if isinstance(mode, SoundboardMode):
            # The soundboard grid press also means "select this pad for UI assignment"
            pass

    # Override the grid press handler for soundboard selection
    def _on_virtual_grid_press(self, row, col):
        mode = self.mode_manager.current_mode

        # Track selected pad for soundboard UI
        if isinstance(mode, SoundboardMode):
            self._sb_selected_pad = (row, col)
            pad = self._soundboard.get_pad(row, col)
            if pad:
                self._sb_selected_label.setText(
                    f"Pad [{row},{col}]: {os.path.basename(pad.filepath)}"
                )
                # Update UI controls to match this pad's settings
                idx = self._sb_color_combo.findText(pad.color)
                if idx >= 0:
                    self._sb_color_combo.setCurrentIndex(idx)
                self._sb_vol_slider.setValue(int(pad.volume * 100))
            else:
                self._sb_selected_label.setText(f"Pad [{row},{col}]: Empty")

        # Dispatch the press to the mode (recorder handles its own state machine)
        # Check recorder state BEFORE the press to know if an assignment will happen
        from launchpad_ctrl.modes.recorder import RecState
        was_assigning = isinstance(mode, RecorderMode) and (
            self._recorder.state in (RecState.RECORDING, RecState.ASSIGNING)
        )

        self.mode_manager.on_grid_press(row, col)

        # After press: if recorder just assigned a recording, auto-send to soundboard
        if isinstance(mode, RecorderMode):
            if was_assigning and self._recorder.state == RecState.IDLE:
                # An assignment just happened — find the file that was saved
                filepath = self._recorder.get_recording_path(row, col)
                if filepath:
                    self._send_recording_to_soundboard(row, col, filepath, quiet=True)
                    self._statusbar.showMessage(
                        f"Recording assigned to pad [{row},{col}] and added to Soundboard"
                    )
            self._rebuild_mode_controls()
            self._record_btn.setChecked(self._recorder.is_recording)

    def closeEvent(self, event):
        """Clean up on window close."""
        # Stop sequencer thread (may be running in the background)
        self._sequencer.stop_playback()
        self.midi.clear_all()
        self.midi.disconnect()
        self.virtual_router.disable()
        self.audio.stop()
        event.accept()
