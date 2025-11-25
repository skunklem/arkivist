import re
from PySide6.QtWidgets import QWidget, QPlainTextEdit, QSizePolicy, QTableWidget
from PySide6.QtCore import Signal, Qt, QUrl, QUrlQuery

class DropPane(QWidget):
    fileDropped = Signal(list)  # list[str]
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            self.fileDropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

class PlainNoTab(QPlainTextEdit):
    """QPlainTextEdit that uses Tab/Shift+Tab to move focus instead of inserting tabs."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)   # ensure it can take focus
        self.setUndoRedoEnabled(True)
        self.setReadOnly(False)

    def keyPressEvent(self, event):
        k = event.key()
        m = event.modifiers()
        if k == Qt.Key_Tab and not m:
            # advance focus at the dialog/window level
            win = self.window()
            if win:
                win.focusNextPrevChild(True)
            return
        elif k == Qt.Key_Backtab:
            win = self.window()
            if win:
                win.focusNextPrevChild(False)
            return
        super().keyPressEvent(event)

def auto_grow_plaintext(edit: QPlainTextEdit, min_lines=3, max_lines=24):
    # make the editor fixed-height so the dialog scrolls, not the editor
    # edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def _recalc():
        # make the doc wrap to the viewport width for correct height
        doc = edit.document()
        doc.setTextWidth(edit.viewport().width() - 4)
        h = int(doc.size().height()) + edit.frameWidth()*2 + 6
        fm = edit.fontMetrics()
        min_h = fm.lineSpacing()*min_lines + 8
        max_h = fm.lineSpacing()*max_lines + 8
        edit.setFixedHeight(max(min_h, min(h, max_h)))

    edit.textChanged.connect(_recalc)

    # Also update when the editor resizes (dialog or splitter moves)
    old_resize = edit.resizeEvent
    def _resize(ev):
        if old_resize:
            old_resize(ev)
        _recalc()
    edit.resizeEvent = _resize

    edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    _recalc()


def fit_table_height_to_rows(table: QTableWidget, min_rows=1):
    header = table.horizontalHeader().height()
    print("rows:",table.rowCount(), min_rows)
    rows = max(table.rowCount(), min_rows) + 1
    rowh = table.sizeHintForRow(0) if table.rowCount() else table.fontMetrics().height()+8
    h = header + rows*rowh + 6
    table.setMinimumHeight(h)
    table.setMaximumHeight(h)


def chapter_display_label(index_zero_based: int, title: str) -> str:
    # tweak to your policy (Prologue, Parts, “Ch. N — Title”, etc.)
    n = index_zero_based + 1
    return f"{n}. {title}" if title else f"Chapter {n}"

def parse_internal_url(qurl: QUrl):
    s = qurl.scheme()
    if s == "world":
        # world://item/123 or world://123
        m = re.search(r'^world://(?:item/)?(\d+)', qurl.toString())
        if m: return {"kind":"world","id": int(m.group(1))}
        return None
    if s == "suggest":
        # suggest://quick/123  or suggest://ai/456
        m = re.search(r'^suggest://([^/]+)/(\d+)', qurl.toString())
        if m: return {"kind":"suggest","source": m.group(1), "id": int(m.group(2))}
        return None
    return None


_MD_ESC = re.compile(r"\\([\\`\*\[\]\(\)\{\}\.\!\?\,\;\:\#\+\-\_])")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_CODEBLOCK = re.compile(r"(?s)```.*?```")
_MD_INLINECODE = re.compile(r"`[^`]+`")
_MD_FMT = re.compile(r"(\*{1,3}|_{1,3}|~~|^>+|\#{1,6})")

def scrub_markdown_for_ner(md: str) -> str:
    t = md or ""
    t = _MD_CODEBLOCK.sub(" ", t)
    t = _MD_INLINECODE.sub(" ", t)
    t = _MD_LINK.sub(r"\1", t)         # keep the link text
    t = _MD_ESC.sub(r"\1", t)          # unescape \. \! \?
    t = _MD_FMT.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

_POSSESSIVE = re.compile(r"(?i)\b(.+?)'s\b")

def normalize_possessive(surface: str) -> tuple[str, bool]:
    m = _POSSESSIVE.fullmatch(surface.strip())
    if m:
        print("normalize_possessive:", surface, m, m.group(1), True)
        return m.group(1), True
    print("normalize_possessive:", surface, m, "No match", False)
    return surface, False
