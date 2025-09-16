from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QPainter, QPixmap, QPen, QColor, QPainterPath

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

def make_lock_icon(app, locked: bool, size: int = 16) -> QIcon:
    """Programmatically draw a crisp lock icon (open/closed) that adapts to theme."""
    dpr = app.devicePixelRatioF() if hasattr(app, "devicePixelRatioF") else 1.0
    px  = int(size * dpr)
    pm  = QPixmap(px, px)
    pm.fill(Qt.transparent)

    fg = app.palette().windowText().color()  # single color used for both states

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    penw = max(1.2 * dpr, 1.0)
    pen  = QPen(fg, penw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)

    # Body
    body = QRectF(px * 0.22, px * 0.46, px * 0.56, px * 0.40)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(body, px * 0.08, px * 0.08)

    # # Keyhole (simple vertical line)
    # kx = body.center().x()
    # ky1 = body.center().y() - px * 0.05
    # ky2 = body.center().y() + px * 0.10
    # p.drawLine(kx, ky1, kx, ky2)

    # # Keyhole
    # p.setBrush(fg)
    # keyhole = QPainterPath()
    # keyhole.addEllipse(QRectF(body.center().x()-px*0.03, body.center().y()-px*0.06, px*0.06, px*0.06))
    # keyhole.moveTo(body.center().x(), body.center().y())
    # keyhole.lineTo(body.center().x(), body.center().y()+px*0.10)
    # p.drawPath(keyhole)

    # Keyhole
    kx = body.center().x()
    ky = body.center().y() + px*0.02
    p.drawEllipse(QRectF(kx - px*0.03, ky - px*0.03, px*0.06, px*0.06))
    p.drawLine(kx, ky, kx, ky + px*0.08)

    # Shackle
    sh = QPainterPath()
    r = px * 0.22  # shackle radius
    cx = body.center().x()
    arc_rect = QRectF(cx - r, body.top() - r*1.2, 2*r, 2*r)  # sits above body

    if locked:
        # Closed arc from left top to right top
        sh.moveTo(cx - r, body.top())
        sh.arcTo(arc_rect, 180, -180)  # arc from left→right over the top
        sh.lineTo(cx + r, body.top())
    else:
        # Open arc: leave a gap on one side
        sh.moveTo(cx - r, body.top())
        sh.arcTo(arc_rect, 180, -120)  # arc but stop early

    p.drawPath(sh)
    p.end()

    if dpr != 1.0:
        pm.setDevicePixelRatio(dpr)
    return QIcon(pm)
