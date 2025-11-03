from collections import defaultdict
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QIcon, QIcon
from PySide6.QtWidgets import (
    QWidget, QSplitter, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QLineEdit, QLabel, QPushButton, QFileDialog, QMessageBox,
    QComboBox, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, 
    QInputDialog, QMenu, QFormLayout, QHBoxLayout,
)
from ui.widgets.helpers import PlainNoTab, chapter_display_label

class ProjectManagerDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Project Manager")
        self.resize(760, 480)

        # Left: header + New + Double-click hint + list
        self.leftHeader = QWidget()
        lh = QHBoxLayout(self.leftHeader); lh.setContentsMargins(0,0,0,0)
        self.leftTitle = QLabel("<b>Projects</b>")
        self.btnNew = QPushButton("New")
        self.btnNew.setIcon(QIcon.fromTheme("list-add") or QIcon.fromTheme("document-new"))
        lh.addWidget(self.leftTitle); lh.addStretch(1); lh.addWidget(self.btnNew)

        self.leftHint = QLabel("Double-click a project to open")
        # self.leftHint.setStyleSheet("color: palette(mid); font-size: 11px;") # too dark in dark mode
        # self.leftHint.setStyleSheet("color: palette(buttonText); font-size: 11px;") # too light in light mode
        # self.leftHint.setStyleSheet("""
        #     color: palette(text);
        #     font-size: 11px;
        #     opacity: 0.7;
        # """) # too light in light mode and turns chapter tree box dark (or one or the other if changing opacity)
        self.leftHint.setStyleSheet("""
            color: #888;
            font-size: 11px;
        """) # works ok in both modes

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self._load_projects()

        leftWrap = QWidget(); lv = QVBoxLayout(leftWrap); lv.setContentsMargins(6,6,6,6); lv.setSpacing(6)
        lv.addWidget(self.leftHeader)
        lv.addWidget(self.leftHint)
        lv.addWidget(self.list, 1)

        # Right: editable fields
        self.nameEdit = QLineEdit()
        self.importEdit = QLineEdit()
        self.btnBrowseImport = QPushButton("Browse…")
        self.btnBrowseImport.clicked.connect(lambda: self._browse(self.importEdit))
        self.exportEdit = QLineEdit()
        self.btnBrowseExport = QPushButton("Browse…")
        self.btnBrowseExport.clicked.connect(lambda: self._browse(self.exportEdit))
        self.descEdit = PlainNoTab()

        form = QFormLayout()
        form.addRow("Project name:", self.nameEdit)
        row1 = QWidget(); r1 = QHBoxLayout(row1); r1.setContentsMargins(0,0,0,0)
        r1.addWidget(self.importEdit, 1); r1.addWidget(self.btnBrowseImport, 0)
        form.addRow("Import folder:", row1)
        row2 = QWidget(); r2 = QHBoxLayout(row2); r2.setContentsMargins(0,0,0,0)
        r2.addWidget(self.exportEdit, 1); r2.addWidget(self.btnBrowseExport, 0)
        form.addRow("Export folder:", row2)
        form.addRow("Description:", self.descEdit)

        self.btnSave = QPushButton("Save")
        self.btnCancel = QPushButton("Cancel")
        self.btnSave.clicked.connect(self._save_current)
        self.btnCancel.clicked.connect(self.reject)

        rightWrap = QWidget(); rv = QVBoxLayout(rightWrap); rv.setContentsMargins(6,6,6,6); rv.setSpacing(6)
        rightFormWrap = QWidget(); rf = QVBoxLayout(rightFormWrap); rf.setContentsMargins(0,0,0,0)
        rf.addLayout(form)
        rv.addWidget(rightFormWrap, 1)
        rowBtns = QWidget(); rb = QHBoxLayout(rowBtns); rb.setContentsMargins(0,0,0,0)
        rb.addStretch(1); rb.addWidget(self.btnSave); rb.addWidget(self.btnCancel)
        rv.addWidget(rowBtns, 0)

        splitter = QSplitter()
        splitter.addWidget(leftWrap)
        splitter.addWidget(rightWrap)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.addWidget(splitter)

        # Signals + context menu
        self.list.currentItemChanged.connect(self._load_selected_into_form)
        self.list.itemDoubleClicked.connect(self._open_selected)
        self.btnNew.clicked.connect(self._new_project)

        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._ctx_menu)
        self.list.installEventFilter(self)  # to catch F2

        # Preselect current project
        cur_pid = getattr(self.app, "_current_project_id", None)
        if cur_pid:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == cur_pid:
                    self.list.setCurrentRow(i)
                    break
        else:
            if self.list.count():
                self.list.setCurrentRow(0)

    # --- Helpers ---
    def _load_projects(self):
        self.list.clear()
        cur = self.app.db.conn.cursor()
        cur.execute("SELECT id, name FROM projects WHERE COALESCE(deleted,0)=0 ORDER BY created_at, id")
        for pid, name in cur.fetchall():
            it = QListWidgetItem(name or "(Untitled)")
            it.setData(Qt.UserRole, int(pid))
            self.list.addItem(it)

    def _load_selected_into_form(self, cur: QListWidgetItem, prev: QListWidgetItem):
        pid = cur.data(Qt.UserRole) if cur else None
        if not pid:
            self.nameEdit.clear(); self.importEdit.clear(); self.exportEdit.clear(); self.descEdit.clear()
            return
        c = self.app.db.conn.cursor()
        c.execute("SELECT name, import_dir, export_dir, description FROM projects WHERE id=?", (pid,))
        row = c.fetchone()
        if not row:
            self.nameEdit.clear(); self.importEdit.clear(); self.exportEdit.clear(); self.descEdit.clear()
            return
        if isinstance(row, sqlite3.Row):
            name, imprt, exprt, desc = row["name"], row["import_dir"], row["export_dir"], row["description"]
        else:
            name, imprt, exprt, desc = row
        self.nameEdit.setText(name or "")
        self.importEdit.setText(imprt or "")
        self.exportEdit.setText(exprt or "")
        self.descEdit.setPlainText(desc or "")

    def _open_selected(self, *args):
        pid = self._current_pid()
        if not pid:
            return
        # Save any edits to the selected project first
        self._save_current()
        # Switch the app to this project
        self.app.switch_project(int(pid))
        self.accept()

    def _browse(self, line: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Choose folder", line.text() or "")
        if path:
            line.setText(path)

    def _current_pid(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _save_current(self):
        pid = self._current_pid()
        if not pid:
            return
        name = self.nameEdit.text().strip() or "(Untitled)"
        imprt = self.importEdit.text().strip() or None
        exprt = self.exportEdit.text().strip() or None
        desc  = self.descEdit.toPlainText().strip() or None
        c = self.app.db.conn.cursor()
        c.execute("""UPDATE projects
                     SET name=?, import_dir=?, export_dir=?, description=?, deleted=COALESCE(deleted,0)
                     WHERE id=?""", (name, imprt, exprt, desc, pid))
        self.app.db.conn.commit()
        # Update list display text
        it = self.list.currentItem()
        if it: it.setText(name)
        # If current project edited, refresh header
        if pid == getattr(self.app, "_current_project_id", None):
            self.app.refresh_project_header()

    def _new_project(self):
        name, ok = QInputDialog.getText(self, "Untitled Project", "Project name:")
        if not ok or not name.strip(): return
        c = self.app.db.conn.cursor()
        c.execute("INSERT INTO projects(name) VALUES(?)", (name.strip(),))
        self.app.db.conn.commit()
        self._load_projects()
        # select the new one
        for i in range(self.list.count()):
            if self.list.item(i).text() == name.strip():
                self.list.setCurrentRow(i)
                break

    def _ctx_menu(self, pos):
        it = self.list.itemAt(pos)
        if not it: return
        pid = it.data(Qt.UserRole)
        menu = QMenu(self)
        actClone  = menu.addAction("Clone")
        actRename = menu.addAction("Rename")
        actDelete = menu.addAction("Delete")
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen == actClone:
            self._clone_project(pid)
        elif chosen == actRename:
            self._rename_project_inline(it, pid)
        elif chosen == actDelete:
            self._delete_project(pid)

    def eventFilter(self, obj, ev):
        if obj is self.list and ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_F2:
            it = self.list.currentItem()
            if it:
                self._rename_project_inline(it, it.data(Qt.UserRole))
            return True
        return super().eventFilter(obj, ev)

    def _rename_project_inline(self, item: QListWidgetItem, pid: int):
        old = item.text()
        new, ok = QInputDialog.getText(self, "Rename Project", "New name:", text=old)
        if not ok or not new.strip(): return
        c = self.app.db.conn.cursor()
        c.execute("UPDATE projects SET name=? WHERE id=?", (new.strip(), pid))
        self.app.db.conn.commit()
        item.setText(new.strip())
        if pid == getattr(self.app, "_current_project_id", None):
            self.app.refresh_project_header()

    def _clone_project(self, pid: int):
        c = self.app.db.conn.cursor()
        c.execute("SELECT name FROM projects WHERE id=?", (pid,))
        row = c.fetchone()
        base = (row[0] if row and not isinstance(row, sqlite3.Row) else (row["name"] if row else "Project"))
        name = f"{base} (copy)"
        self.app.duplicate_project(pid, name)
        self._load_projects()

    def _is_project_empty(self, pid: int) -> bool:
        c = self.app.db.conn.cursor()
        for sql in (
            "SELECT 1 FROM books WHERE project_id=? LIMIT 1",
            "SELECT 1 FROM chapters WHERE project_id=? AND COALESCE(deleted,0)=0 LIMIT 1",
            "SELECT 1 FROM world_categories WHERE project_id=? AND COALESCE(deleted,0)=0 LIMIT 1",
            "SELECT 1 FROM world_items      WHERE project_id=? AND COALESCE(deleted,0)=0 LIMIT 1",
        ):
            c.execute(sql, (pid,))
            if c.fetchone():
                return False
        return True

    def _delete_project(self, pid: int):
        if self._is_project_empty(pid):
            # hard delete
            btn = QMessageBox.question(self, "Delete Project",
                "This project is empty. Permanently delete it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes:
                return
            c = self.app.db.conn.cursor()
            c.execute("DELETE FROM projects WHERE id=?", (pid,))
            # cascade deletes not declared; dependent rows should not exist if empty
            self.app.db.conn.commit()
        else:
            # soft delete
            btn = QMessageBox.question(self, "Delete Project",
                "This project has content. Hide it (soft delete)? You can restore it later.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes:
                return
            c = self.app.db.conn.cursor()
            c.execute("UPDATE projects SET deleted=1 WHERE id=?", (pid,))
            self.app.db.conn.commit()

        # If we deleted the current project, switch away
        if pid == getattr(self.app, "_current_project_id", None):
            self.app._current_project_id = None
            self.app.refresh_project_header()
            # try pick another project automatically
            c = self.app.db.conn.cursor()
            c.execute("SELECT id FROM projects WHERE COALESCE(deleted,0)=0 ORDER BY created_at, id LIMIT 1")
            row = c.fetchone()
            if row:
                self.app.switch_project(int(row[0]))
        self._load_projects()

class BulkChapterImportDialog(QDialog):
    def __init__(self, app, paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bulk Import Chapters")
        self.app = app
        self.paths = paths

        self.placeBox = QComboBox()
        self._populate_placement_options()

        self.sepBox = QComboBox()
        self.sepBox.setEditable(True)         # allow custom separators
        self.sepBox.addItems(["", " - ", ". "])  # "" = no split
        self.sepBox.setCurrentIndex(0)

        form = QFormLayout()
        form.addRow("Insert:", self.placeBox)
        form.addRow("Split on first:", self.sepBox)

        filesLabel = QLabel("Files to import:\n" + "\n".join(Path(p).name for p in paths))
        filesLabel.setWordWrap(True)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(filesLabel)
        lay.addWidget(btns)
        self.resize(520, 360)

    def _populate_placement_options(self):
        """Build: First, Last, After <each chapter>."""
        pid = self.app._current_project_id
        bid = self.app._current_book_id
        cur = self.app.db.conn.cursor()
        cur.execute("""
            SELECT id, title, position FROM chapters
            WHERE project_id=? AND book_id=?
            ORDER BY position, id
        """, (pid, bid))
        rows = cur.fetchall()

        self.placeBox.addItem("As first chapter", ("first", None))

        if len(rows) > 0:
            self.placeBox.addItem("As last chapter", ("last", None))
            self.placeBox.insertSeparator(self.placeBox.count())
            for cid, title, pos in rows:
                label = f"After {chapter_display_label(pos, title)}"
                self.placeBox.addItem(label, ("after", int(cid)))

    def chosen(self):
        sep = self.sepBox.currentText()
        mode, cid = self.placeBox.currentData()
        return {"mode": mode, "anchor_cid": cid, "sep": (sep if sep != "" else None)}

class WorldImportDialog(QDialog):
    def __init__(self, app, paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import World Items")
        self.app = app
        self.paths = paths
        self.resize(420, 240)

        # ensure roots exist
        self.app.ensure_world_roots()

        self.parentBox = QComboBox()
        self._load_world_categories()

        # Use the first file’s stem as the default name
        default_name = Path(paths[0]).stem if paths else ""
        self.nameEdit = QLineEdit(default_name)
        self.nameEdit.setPlaceholderText("World item name")

        form = QFormLayout()
        form.addRow("Category:", self.parentBox)
        name_label = QLabel("Name:")
        form.addRow(name_label, self.nameEdit)

        # If multiple files: hide the name editor; we'll use each file's stem
        if len(paths) > 1:
            name_label.setVisible(False)
            self.nameEdit.setVisible(False)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Files to import:\n" + "\n".join(str(Path(p).name) for p in paths)))
        lay.addWidget(btns)

    def _load_world_categories(self):
        self.parentBox.clear()
        cur = self.app.db.conn.cursor()
        pid = self.app._current_project_id
        cur.execute("""
            SELECT id, parent_id, name, position
            FROM world_categories
            WHERE project_id=? AND COALESCE(deleted,0)=0
            ORDER BY COALESCE(position,0), name, id
        """, (pid,))
        rows = cur.fetchall()

        children, names = defaultdict(list), {}
        for r in rows:
            cid, parent_id, name, pos = (r["id"], r["parent_id"], r["name"], r["position"]) if isinstance(r, sqlite3.Row) else r
            names[cid] = name
            children[parent_id].append(cid)

        def walk(parent_id, path):
            for cid in children.get(parent_id, []):
                label = " ▸ ".join(path + [names.get(cid, f"({cid})")])
                self.parentBox.addItem(label, cid)
                walk(cid, path + [names.get(cid, f"({cid})")])
        walk(None, [])

    def chosen(self):
        # If multiple files: nameEdit is hidden; caller will use each file’s stem
        return {
            "parent_id": self.parentBox.currentData(),
            "name": (self.nameEdit.text().strip() if self.nameEdit.isVisible() else None),
        }
