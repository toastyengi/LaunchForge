"""
Dark theme stylesheet for the LaunchPad Controller UI.
Industrial-meets-music-production aesthetic.
"""

DARK_THEME = """
QMainWindow {
    background-color: #0d0d0d;
    color: #e0e0e0;
}

QWidget {
    background-color: #0d0d0d;
    color: #e0e0e0;
    font-family: "JetBrains Mono", "Fira Code", "Monospace";
    font-size: 11px;
}

QLabel {
    color: #b0b0b0;
    font-size: 11px;
    background: transparent;
}

QLabel#title_label {
    color: #ff5555;
    font-size: 18px;
    font-weight: bold;
    letter-spacing: 2px;
}

QLabel#mode_label {
    color: #55ff55;
    font-size: 14px;
    font-weight: bold;
}

QLabel#status_label {
    color: #888888;
    font-size: 10px;
}

QLabel#bpm_label {
    color: #ffaa00;
    font-size: 24px;
    font-weight: bold;
}

QPushButton {
    background-color: #1a1a1a;
    color: #d0d0d0;
    border: 1px solid #333333;
    border-radius: 4px;
    padding: 8px 16px;
    font-size: 11px;
    min-height: 28px;
}

QPushButton:hover {
    background-color: #252525;
    border-color: #555555;
}

QPushButton:pressed {
    background-color: #333333;
    border-color: #666666;
}

QPushButton:disabled {
    background-color: #111111;
    color: #444444;
    border-color: #222222;
}

QPushButton#mode_btn {
    background-color: #1a1a2e;
    border-color: #333355;
    font-weight: bold;
}

QPushButton#mode_btn:checked, QPushButton#mode_btn:hover {
    background-color: #2a2a4e;
    border-color: #5555aa;
    color: #aaaaff;
}

QPushButton#play_btn {
    background-color: #1a2e1a;
    border-color: #335533;
    color: #55ff55;
    font-weight: bold;
    font-size: 13px;
}

QPushButton#play_btn:hover {
    background-color: #2a4e2a;
}

QPushButton#stop_btn {
    background-color: #2e1a1a;
    border-color: #553333;
    color: #ff5555;
    font-weight: bold;
    font-size: 13px;
}

QPushButton#stop_btn:hover {
    background-color: #4e2a2a;
}

QPushButton#record_btn {
    background-color: #2e1a1a;
    border-color: #553333;
    color: #ff4444;
    font-weight: bold;
}

QPushButton#record_btn:checked {
    background-color: #551111;
    border-color: #ff3333;
    color: #ff3333;
}

QPushButton#panic_btn {
    background-color: #3e1010;
    border-color: #882222;
    color: #ff2222;
    font-weight: bold;
    font-size: 12px;
}

QPushButton#panic_btn:hover {
    background-color: #551515;
}

QSlider::groove:horizontal {
    border: 1px solid #333;
    height: 6px;
    background: #1a1a1a;
    margin: 0;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #555555;
    border: 1px solid #666666;
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #777777;
}

QSlider::sub-page:horizontal {
    background: #335533;
    border-radius: 3px;
}

QSpinBox, QDoubleSpinBox {
    background-color: #1a1a1a;
    color: #ffaa00;
    border: 1px solid #333333;
    border-radius: 3px;
    padding: 4px;
    font-size: 14px;
    font-weight: bold;
}

QComboBox {
    background-color: #1a1a1a;
    color: #d0d0d0;
    border: 1px solid #333333;
    border-radius: 3px;
    padding: 4px 8px;
    min-height: 24px;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #1a1a1a;
    color: #d0d0d0;
    border: 1px solid #333333;
    selection-background-color: #333355;
}

QGroupBox {
    border: 1px solid #2a2a2a;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: bold;
    color: #888888;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #888888;
}

QTabWidget::pane {
    border: 1px solid #2a2a2a;
    background-color: #0d0d0d;
}

QTabBar::tab {
    background-color: #1a1a1a;
    color: #888888;
    border: 1px solid #2a2a2a;
    padding: 8px 16px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

QTabBar::tab:selected {
    background-color: #0d0d0d;
    color: #e0e0e0;
    border-bottom-color: #0d0d0d;
}

QTabBar::tab:hover:!selected {
    background-color: #252525;
}

QScrollBar:vertical {
    background: #111111;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #333333;
    border-radius: 4px;
    min-height: 20px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QFileDialog {
    background-color: #1a1a1a;
    color: #e0e0e0;
}

QLineEdit {
    background-color: #1a1a1a;
    color: #d0d0d0;
    border: 1px solid #333333;
    border-radius: 3px;
    padding: 4px 8px;
}

QMenuBar {
    background-color: #111111;
    color: #b0b0b0;
    border-bottom: 1px solid #222222;
}

QMenuBar::item:selected {
    background-color: #252525;
}

QMenu {
    background-color: #1a1a1a;
    color: #d0d0d0;
    border: 1px solid #333333;
}

QMenu::item:selected {
    background-color: #333355;
}

QStatusBar {
    background-color: #0a0a0a;
    color: #666666;
    border-top: 1px solid #1a1a1a;
}
"""
