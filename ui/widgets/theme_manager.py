# theme_manager.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor, QFont
from PySide6.QtWidgets import QApplication

@dataclass(frozen=True)
class Theme:
    name: str
    is_dark: bool
    # core tokens
    window: str
    base: str
    alt_base: str
    text: str
    disabled_text: str
    highlight: str
    highlighted_text: str
    button: str
    button_text: str
    link: str
    # accents
    accent_ok: str
    accent_warn: str
    accent_err: str
    # chrome
    tooltip_bg: str
    tooltip_fg: str
    menu_bg: str
    menu_fg: str
    menu_sel_bg: str
    menu_sel_fg: str

LIGHT = Theme(
    name="Light",
    is_dark=False,
    window="#F6F7F9", base="#FFFFFF", alt_base="#F2F3F5",
    text="#1E1F22", disabled_text="#9AA0A6",
    highlight="#1E88E5", highlighted_text="#FFFFFF",
    button="#FFFFFF", button_text="#1E1F22",
    link="#1565C0",
    accent_ok="#2e7d32", accent_warn="#EF6C00", accent_err="#C62828",
    tooltip_bg="#2B2F33", tooltip_fg="#FFFFFF",
    menu_bg="#FFFFFF", menu_fg="#1E1F22",
    menu_sel_bg="#E3F2FD", menu_sel_fg="#1E1F22"
)

DARK = Theme(
    name="Dark",
    is_dark=True,
    window="#17191C", base="#1E2125", alt_base="#24272B",
    text="#ECEFF1", disabled_text="#7C8896",
    highlight="#64B5F6", highlighted_text="#0B0C0E",
    button="#2A2E34", button_text="#ECEFF1",
    link="#90CAF9",
    accent_ok="#7CB342", accent_warn="#FFA726", accent_err="#EF5350",
    tooltip_bg="#2A2E34", tooltip_fg="#ECEFF1",
    menu_bg="#24272B", menu_fg="#ECEFF1",
    menu_sel_bg="#3A3F46", menu_sel_fg="#ECEFF1"
)

HIGH_CONTRAST = Theme(
    name="High Contrast",
    is_dark=True,
    window="#000000", base="#0A0A0A", alt_base="#111111",
    text="#FFFFFF", disabled_text="#8A8A8A",
    highlight="#00D7FF", highlighted_text="#000000",
    button="#0F0F0F", button_text="#FFFFFF",
    link="#7FDBFF",
    accent_ok="#00FF88", accent_warn="#FFD400", accent_err="#FF3860",
    tooltip_bg="#202020", tooltip_fg="#FFFFFF",
    menu_bg="#0F0F0F", menu_fg="#FFFFFF",
    menu_sel_bg="#2A2A2A", menu_sel_fg="#FFFFFF"
)

FLUENT = Theme(  # a subtle Fluent/Win11-ish palette
    name="Fluent",
    is_dark=True,
    window="#151719", base="#1B1D20", alt_base="#202328",
    text="#E8EAED", disabled_text="#89939E",
    highlight="#3B82F6", highlighted_text="#FFFFFF",
    button="#202328", button_text="#E8EAED",
    link="#93C5FD",
    accent_ok="#22C55E", accent_warn="#F59E0B", accent_err="#EF4444",
    tooltip_bg="#2A2D31", tooltip_fg="#E8EAED",
    menu_bg="#202328", menu_fg="#E8EAED",
    menu_sel_bg="#2B3340", menu_sel_fg="#E8EAED"
)

THEMES = [LIGHT, DARK, HIGH_CONTRAST, FLUENT]

QSS_TEMPLATE = """
/* ---- Global ---- */
* {{ outline: 0; }}
QToolTip {{
  background: {tooltip_bg}; color: {tooltip_fg}; border: 1px solid {highlight}; padding: {pad_y}px {pad_x}px; border-radius: {radius}px;
}}
QWidget {{
  background-color: {base}; color: {text};
}}
QMainWindow, QDialog, QMenuBar, QMenu, QToolBar {{
  background-color: {window}; color: {menu_fg};
}}
QMenu {{
  background: {menu_bg}; color: {menu_fg}; border: 1px solid {alt_base};
}}
QMenu::item:selected {{ background: {menu_sel_bg}; color: {menu_sel_fg}; }}
QToolBar {{ border: 0; spacing: {pad_x}px; background: {window}; }}
QStatusBar {{ background: {window}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QTreeView, QTableView, QListView {{
  background: {base}; color: {text}; border: 1px solid {alt_base}; border-radius: {radius}px; selection-background-color: {highlight}; selection-color: {highlighted_text};
}}
QPlainTextEdit, QTextEdit {{ padding: {pad_y}px; }}

QPushButton {{
  background: {button}; color: {button_text}; border: 1px solid {alt_base}; border-radius: {pill_radius}px; padding: {pad_y}px {pad_x}px;
}}
QPushButton:hover {{ border-color: {highlight}; }}
QPushButton:pressed {{ background: {alt_base}; }}

QScrollBar:vertical {{
  background: transparent; width: {scrollbar_w}px; margin: {pad_y}px;
}}
QScrollBar::handle:vertical {{
  background: {alt_base}; min-height: {scrollbar_handle_min}px; border-radius: {scrollbar_radius}px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

QCheckBox::indicator, QRadioButton::indicator {{
  width: {check}px; height: {check}px;
}}
QCheckBox::indicator:checked {{
  image: none; background: {highlight}; border: 1px solid {highlight}; border-radius: {check_radius}px;
}}
QCheckBox::indicator:unchecked {{
  image: none; background: transparent; border: 1px solid {alt_base}; border-radius: {check_radius}px;
}}

/* ---- Status pill (uses objectName 'StatusPill') ---- */
#StatusPill {{
  background: transparent;
  color: {highlight};
  border: 1px solid {highlight};
  border-radius: 999px;
  padding: {pill_pad_y}px {pill_pad_x}px;
  font-weight: 600;
}}
/* semantic helpers */
.StatusPill--ok    {{ color: {accent_ok};    border-color: {accent_ok};    }}
.StatusPill--warn  {{ color: {accent_warn};  border-color: {accent_warn};  }}
.StatusPill--err   {{ color: {accent_err};   border-color: {accent_err};   }}

/* ---- Trees & selection polish ---- */
QTreeView::item:hover {{ background: rgba(127,127,127,0.08); }}
QTreeView::item:selected:active {{ background: {highlight}; color: {highlighted_text}; }}
"""

class ThemeManager:
    def __init__(self):
        self._themes = THEMES
        self._idx = 0
        self._base_pt = 10.0
        self._scale = 1.0  # 1.0 = 100%

    @property
    def current(self) -> Theme:
        return self._themes[self._idx]

    def set_index(self, idx: int) -> None:
        self._idx = idx % len(self._themes)

    def next(self) -> Theme:
        self._idx = (self._idx + 1) % len(self._themes)
        return self.current

    def set_base_pt(self, pt: float) -> None:
        self._base_pt = max(6.0, min(24.0, float(pt)))  # clamp

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.75, min(1.75, float(scale)))  # clamp ~75–175%

    def zoom_in(self, step: float = 0.1) -> None:
        self.set_scale(self._scale + step)

    def zoom_out(self, step: float = 0.1) -> None:
        self.set_scale(self._scale - step)

    def zoom_reset(self) -> None:
        self._scale = 1.0

    def apply(self, app: QApplication, *, font_family: str = "Inter", base_pt: float | None = None) -> None:
        theme = self.current
        if base_pt is not None:
            self.set_base_pt(base_pt)

        app.setStyle("Fusion")

        # ---- Palette ----
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(theme.window))
        pal.setColor(QPalette.WindowText, QColor(theme.text))
        pal.setColor(QPalette.Base, QColor(theme.base))
        pal.setColor(QPalette.AlternateBase, QColor(theme.alt_base))
        pal.setColor(QPalette.Text, QColor(theme.text))
        pal.setColor(QPalette.Button, QColor(theme.button))
        pal.setColor(QPalette.ButtonText, QColor(theme.button_text))
        pal.setColor(QPalette.ToolTipBase, QColor(theme.tooltip_bg))
        pal.setColor(QPalette.ToolTipText, QColor(theme.tooltip_fg))
        pal.setColor(QPalette.Link, QColor(theme.link))
        pal.setColor(QPalette.Highlight, QColor(theme.highlight))
        pal.setColor(QPalette.HighlightedText, QColor(theme.highlighted_text))
        pal.setColor(QPalette.Disabled, QPalette.Text, QColor(theme.disabled_text))
        pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(theme.disabled_text))
        pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(theme.disabled_text))
        app.setPalette(pal)

        # ---- Font baseline ----
        f = app.font() if app.font() else QFont()
        if font_family:
            f.setFamily(font_family)
        f.setPointSizeF(self._base_pt)
        app.setFont(f)

        # ---- Sizing tokens derived from scale ----
        s = self._scale
        nums: Dict[str, int] = {
            "pad_x": int(round(10 * s)),
            "pad_y": int(round(6 * s)),
            "radius": int(round(8 * s)),
            "pill_radius": int(round(10 * s)),
            "pill_pad_x": int(round(8 * s)),
            "pill_pad_y": int(round(2 * s)),
            "scrollbar_w": int(round(12 * s)),
            "scrollbar_handle_min": int(round(24 * s)),
            "scrollbar_radius": int(round(6 * s)),
            "check": int(round(16 * s)),
            "check_radius": int(round(4 * s)),
        }

        qss = QSS_TEMPLATE.format(
            tooltip_bg=theme.tooltip_bg, tooltip_fg=theme.tooltip_fg,
            base=theme.base, text=theme.text, window=theme.window, alt_base=theme.alt_base,
            highlight=theme.highlight, highlighted_text=theme.highlighted_text,
            button=theme.button, button_text=theme.button_text,
            menu_bg=theme.menu_bg, menu_fg=theme.menu_fg,
            menu_sel_bg=theme.menu_sel_bg, menu_sel_fg=theme.menu_sel_fg,
            accent_ok=theme.accent_ok, accent_warn=theme.accent_warn, accent_err=theme.accent_err,
            **nums
        )
        app.setStyleSheet(qss)

# Singleton-ish helper
theme_manager = ThemeManager()
