from PySide6 import QtWidgets, QtCore

from ui.widgets.outline.page import OutlineWorkspace

class OutlineWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Outline")
        self.setWindowModality(QtCore.Qt.NonModal)
        self.setWindowFlag(QtCore.Qt.Window, True)
        self.workspace = None
        self._first_open_done = False
        self.resize(1024, 700)

    def adopt_workspace(self, workspace):
        """Mount an existing OutlineWorkspace into this window."""
        if self.workspace is workspace:
            return
        self.workspace = workspace
        self.workspace.setParent(self)               # reparent
        self.setCentralWidget(self.workspace)

    def load_project(self, db, project_id: int, book_id: int):
        # only if you *didn't* adopt; when shared, main already loaded it:
        if not self.workspace:
            self.adopt_workspace(OutlineWorkspace())
        self.workspace.load_from_db(db, project_id, book_id)

    def focus_chapter_id(self, chap_id: int):
        # Resolve row from id (don’t rely on previous selection)
        r = self.workspace.row_for_chapter_id(chap_id)
        if r < 0:
            return
        # Defer until layout is ready
        QtCore.QTimer.singleShot(0, lambda: (
            self.workspace.focus_chapter_row(r, give_focus=True),
            self.raise_(), self.activateWindow()
        ))

