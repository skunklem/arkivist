from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap, QPen, QColor, QIcon

def make_checkbox_plus_icon(
    size: int = 16,
    box_color: str = "#000000",      # black
    plus_color: str = "#32d12d",     # green
    box_width: float = 1.6,
    plus_width: float = 2.2,
    radius: float = 2.5
) -> QIcon:
    """
    Draws an empty checkbox (rounded rect) with a small '+' overlapping the
    bottom-right edge of the box. Anti-aliased and crisp at small sizes.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # --- Checkbox outline ---
    pen_box = QPen(QColor(box_color))
    pen_box.setWidthF(box_width)
    painter.setPen(pen_box)

    # box rect (leave breathing room for stroke and plus)
    inset = box_width + 1.0
    box_left  = inset + (size * 0.05)  # nudge right so plus can overlap at bottom-right
    box_top   = inset
    box_size  = size - inset*2
    box_size  = min(box_size, size - box_left - inset)  # safe width after nudging
    painter.drawRoundedRect(box_left, box_top, box_size, box_size, radius, radius)

    # --- Plus (overlapping bottom-left corner) ---
    pen_plus = QPen(QColor(plus_color))
    pen_plus.setWidthF(plus_width)
    pen_plus.setCapStyle(Qt.RoundCap)
    painter.setPen(pen_plus)

    # Place plus center near the box's bottom-left corner
    cx = box_left - size * 0.02   # a hair left of the corner
    cy = box_top + box_size + size * 0.02  # a hair below the corner
    arm = size * 0.22

    # clamp so strokes stay inside pixmap
    cx = max(arm + 1, min(size - arm - 1, cx))
    cy = max(arm + 1, min(size - arm - 1, cy))

    painter.drawLine(int(cx - arm), int(cy),         int(cx + arm), int(cy))
    painter.drawLine(int(cx),       int(cy - arm),   int(cx),       int(cy + arm))

    painter.end()
    return QIcon(pm)
