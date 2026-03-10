"""
Virtual Launchpad grid widget for PyQt5.
Displays an 8x8 grid + control buttons, mirroring the physical Launchpad state.
"""

from PyQt5.QtWidgets import QWidget, QGridLayout, QSizePolicy
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QRect, QTimer
from PyQt5.QtGui import QPainter, QColor, QBrush, QPen, QFont, QLinearGradient

# Map Launchpad velocity colors to display RGB
LP_COLOR_MAP = {
    0: (30, 30, 30),        # OFF
    1: (120, 30, 30),       # RED_LOW
    3: (255, 40, 40),       # RED
    16: (30, 120, 30),      # GREEN_LOW
    48: (40, 255, 40),      # GREEN
    17: (120, 100, 20),     # AMBER_LOW
    51: (255, 200, 40),     # AMBER
    50: (240, 240, 40),     # YELLOW
    35: (255, 140, 30),     # ORANGE
    11: (255, 60, 60),      # RED_FLASH
    56: (60, 255, 60),      # GREEN_FLASH
    59: (255, 210, 60),     # AMBER_FLASH
    58: (250, 250, 60),     # YELLOW_FLASH
}


def velocity_to_color(velocity: int) -> QColor:
    """Convert Launchpad velocity value to QColor."""
    rgb = LP_COLOR_MAP.get(velocity, (30, 30, 30))
    return QColor(*rgb)


class PadButton(QWidget):
    """A single pad button on the virtual grid."""

    pressed = pyqtSignal(int, int)
    released = pyqtSignal(int, int)

    def __init__(self, row: int, col: int, parent=None):
        super().__init__(parent)
        self.row = row
        self.col = col
        self._color = QColor(30, 30, 30)
        self._hover = False
        self._pressed = False
        self._label = ""
        self.setMinimumSize(48, 48)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    def set_color(self, color: QColor):
        self._color = color
        self.update()

    def set_velocity_color(self, velocity: int):
        self._color = velocity_to_color(velocity)
        self.update()

    def set_label(self, text: str):
        self._label = text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)

        # Glow effect for active pads
        r, g, b = self._color.red(), self._color.green(), self._color.blue()
        brightness = max(r, g, b)

        if brightness > 60:
            glow = QColor(r, g, b, 40)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(glow))
            glow_rect = rect.adjusted(-3, -3, 3, 3)
            p.drawRoundedRect(glow_rect, 6, 6)

        # Main pad
        if self._pressed:
            pad_color = self._color.lighter(130)
        elif self._hover:
            pad_color = self._color.lighter(115)
        else:
            pad_color = self._color

        # Gradient for depth
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, pad_color.lighter(110))
        gradient.setColorAt(1, pad_color.darker(110))

        p.setPen(QPen(QColor(50, 50, 50), 1))
        p.setBrush(QBrush(gradient))
        p.drawRoundedRect(rect, 5, 5)

        # Label
        if self._label:
            p.setPen(QColor(200, 200, 200) if brightness < 128 else QColor(20, 20, 20))
            font = QFont("JetBrains Mono", 7)
            p.setFont(font)
            p.drawText(rect, Qt.AlignCenter, self._label[:6])

        p.end()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.pressed.emit(self.row, self.col)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = False
            self.released.emit(self.row, self.col)
            self.update()


class ControlButton(QWidget):
    """A circular control button (top row or right column)."""

    pressed = pyqtSignal(str, int)
    released = pyqtSignal(str, int)

    def __init__(self, position: str, index: int, parent=None):
        super().__init__(parent)
        self.position = position  # "top" or "right"
        self.index = index
        self._color = QColor(40, 40, 40)
        self._hover = False
        self._pressed = False
        self._label = ""
        self.setMinimumSize(48, 20) if position == "top" else self.setMinimumSize(20, 48)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    def set_color(self, color: QColor):
        self._color = color
        self.update()

    def set_label(self, text: str):
        self._label = text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Draw circular button
        size = min(self.width(), self.height()) - 6
        x = (self.width() - size) // 2
        y = (self.height() - size) // 2

        color = self._color.lighter(115) if self._hover else self._color
        if self._pressed:
            color = color.lighter(130)

        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.setBrush(QBrush(color))
        p.drawEllipse(x, y, size, size)

        if self._label:
            p.setPen(QColor(180, 180, 180))
            font = QFont("JetBrains Mono", 6)
            p.setFont(font)
            p.drawText(QRect(x, y, size, size), Qt.AlignCenter, self._label[:4])

        p.end()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.pressed.emit(self.position, self.index)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = False
            self.released.emit(self.position, self.index)
            self.update()


class LaunchpadGrid(QWidget):
    """Complete virtual Launchpad grid with control buttons."""

    grid_pressed = pyqtSignal(int, int)
    grid_released = pyqtSignal(int, int)
    control_pressed = pyqtSignal(str, int)
    control_released = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pads = {}  # (row, col) -> PadButton
        self._top_controls = {}  # index -> ControlButton
        self._right_controls = {}  # index -> ControlButton
        self._init_grid()

    def _init_grid(self):
        layout = QGridLayout(self)
        layout.setSpacing(3)
        layout.setContentsMargins(8, 8, 8, 8)

        # Top labels for control buttons
        top_labels = ["◀MODE", "▶MODE", "⏹STOP", "▶PLAY", "⏺REC", "TAP", "──", "──"]

        # Top row control buttons (row 0 in layout, index 0-7)
        for i in range(8):
            btn = ControlButton("top", i)
            btn.set_label(top_labels[i] if i < len(top_labels) else "")
            btn.pressed.connect(self.control_pressed)
            btn.released.connect(self.control_released)
            layout.addWidget(btn, 0, i + 0)
            self._top_controls[i] = btn

        # Spacer column for right controls
        # label column would go here if needed

        # Grid 8x8 (rows 1-8 in layout, cols 0-7)
        for row in range(8):
            for col in range(8):
                pad = PadButton(row, col)
                pad.pressed.connect(self.grid_pressed)
                pad.released.connect(self.grid_released)
                layout.addWidget(pad, row + 1, col)
                self._pads[(row, col)] = pad

            # Right column control button
            btn = ControlButton("right", row)
            btn.pressed.connect(self.control_pressed)
            btn.released.connect(self.control_released)
            layout.addWidget(btn, row + 1, 8)
            self._right_controls[row] = btn

    def set_pad_color(self, row: int, col: int, velocity: int):
        """Set pad color by Launchpad velocity value."""
        pad = self._pads.get((row, col))
        if pad:
            pad.set_velocity_color(velocity)

    def set_pad_label(self, row: int, col: int, label: str):
        pad = self._pads.get((row, col))
        if pad:
            pad.set_label(label)

    def set_control_color(self, position: str, index: int, velocity: int):
        if position == "top":
            btn = self._top_controls.get(index)
        else:
            btn = self._right_controls.get(index)
        if btn:
            btn.set_color(velocity_to_color(velocity))

    def set_control_label(self, position: str, index: int, label: str):
        if position == "top":
            btn = self._top_controls.get(index)
        else:
            btn = self._right_controls.get(index)
        if btn:
            btn.set_label(label)

    def update_from_grid_state(self, grid_state: list):
        """Update all pads from a 2D grid state array."""
        for row in range(min(8, len(grid_state))):
            for col in range(min(8, len(grid_state[row]))):
                self.set_pad_color(row, col, grid_state[row][col])

    def clear_all(self):
        for pad in self._pads.values():
            pad.set_velocity_color(0)
        for btn in self._top_controls.values():
            btn.set_color(QColor(40, 40, 40))
        for btn in self._right_controls.values():
            btn.set_color(QColor(40, 40, 40))
