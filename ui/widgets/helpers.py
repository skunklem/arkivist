from PySide6.QtWidgets import QWidget, QPlainTextEdit
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