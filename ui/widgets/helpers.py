from PySide6.QtWidgets import QWidget, QPlainTextEdit, QSizePolicy, QTableWidget
from PySide6.QtCore import Signal, Qt

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
