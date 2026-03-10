#!/usr/bin/env python3
"""
LaunchPad Controller - Main entry point.
A modular MIDI controller for Novation Launchpad Mini Mk2.
"""

import sys
import os
import warnings


def main():
    # Suppress pydub's noisy ffmpeg warning (MP3 still won't work without it,
    # but WAV/OGG are fine and the warning scares people)
    warnings.filterwarnings("ignore", message="Couldn't find ffmpeg")

    # Ensure we can find the package
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if app_dir not in sys.path:
        sys.path.insert(0, os.path.dirname(app_dir))

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont

    from launchpad_ctrl.ui.main_window import MainWindow

    # High DPI support
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("LaunchPad Controller")
    app.setOrganizationName("launchpad-ctrl")

    # Set default font
    font = QFont("JetBrains Mono", 10)
    font.setStyleHint(QFont.Monospace)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
