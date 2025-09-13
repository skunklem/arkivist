import re

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QPlainTextEdit,
    QLabel,
    QFrame, QInputDialog, QMenu, QToolButton, QHBoxLayout,
)

from utils.icons import make_checkbox_plus_icon

class TodoTree(QTreeWidget):
    def __init__(self, owner_widget, app, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._owner = owner_widget  # ChapterTodosWidget
        self._app = app
        self.setHeaderHidden(True)
        self.setIndentation(0)
        self.setFrameShape(QFrame.NoFrame)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def startDrag(self, actions):
        self._drag_item = self.currentItem()
        super().startDrag(actions)

    def dragMoveEvent(self, event):
        # Always show a valid “between items” indicator while over the list
        if event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        # We implement a deterministic “insert below”
        drag_item = getattr(self, "_drag_item", None)
        if drag_item is None:
            super().dropEvent(event); return

        pos = event.position().toPoint()
        target = self.itemAt(pos)
        indicator = self.dropIndicatorPosition()  # AboveItem / BelowItem / OnItem / OnViewport

        def is_todo(it):
            if not it: return False
            d = it.data(0, Qt.UserRole)
            return bool(d and d[0] == "todo")

        # Only handle internal to-do moves we understand
        if not is_todo(drag_item):
            super().dropEvent(event)
            return

        # Compute flat destination index among top-level items
        if indicator == QAbstractItemView.OnViewport or target is None:
            event.ignore(); return

        # Translate target + indicator to “insert position”
        # Rule: OnItem => insert BELOW that item. Above/Below behave as named.
        if is_todo(target):
            base = self.indexOfTopLevelItem(target)
            if base < 0: event.ignore(); return
            if indicator == QAbstractItemView.OnItem:
                insert_index = base + 1
            elif indicator == QAbstractItemView.AboveItem:
                insert_index = base
            elif indicator == QAbstractItemView.BelowItem:
                insert_index = base + 1
            else:
                event.ignore(); return
        else:
            event.ignore(); return

        # Build current ordered id list (top-level only)
        ordered = []
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            d = it.data(0, Qt.UserRole)
            if d and d[0] == "todo":
                ordered.append(int(d[1]))

        # Identify dragged id and compute new order
        ddrag = drag_item.data(0, Qt.UserRole)
        if not ddrag or ddrag[0] != "todo":
            event.ignore(); return
        drag_id = int(ddrag[1])

        if drag_id in ordered:
            old_i = ordered.index(drag_id)
            ordered.pop(old_i)
            # Adjust insert index if removing from before the target slot
            if insert_index > old_i:
                insert_index -= 1

        insert_index = max(0, min(insert_index, len(ordered)))
        ordered.insert(insert_index, drag_id)

        # Persist to DB (0..N-1)
        cur = self._app.conn.cursor()
        for pos_i, nid in enumerate(ordered):
            cur.execute("UPDATE chapter_notes SET position=? WHERE id=?", (pos_i, nid))
        self._app.conn.commit()

        # Reload UI and focus the moved item
        self._owner.reload()
        # re-select the moved one
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            d = it.data(0, Qt.UserRole)
            if d and d[0] == "todo" and int(d[1]) == drag_id:
                self.setCurrentItem(it)
                self.scrollToItem(it)
                break

        event.acceptProposedAction()

class ChapterTodosWidget(QWidget):
    """
    Left: To-Dos (reorderable, deletable, checkable)
    Right: Notes (single multiline editor)
    Saves: call save_if_dirty(chapter_id) from the main chapter save path.
    """
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self._chapter_id = None
        self._notes_dirty = False
        self._pending_new_todo_id = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(6,6,6,6)

        # ----- To-Dos column -----
        left = QVBoxLayout()

        # Header row: "To-Dos" label + small add button
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(4)  # tight spacing between label and icon

        lblT = QLabel("To-Dos")

        btnAdd = QToolButton()
        btnAdd.setToolTip("Add to-do")
        btnAdd.setAutoRaise(True)
        btnAdd.setCursor(Qt.PointingHandCursor)
        btnAdd.setIcon(make_checkbox_plus_icon(16))  # ← use the new icon
        btnAdd.setIconSize(QSize(16, 16))
        btnAdd.setStyleSheet("QToolButton{padding:0; margin-left:2px;}")

        btnAdd.clicked.connect(self.add_new_todo)

        hdr.addWidget(lblT)
        hdr.addWidget(btnAdd)   # icon sits right next to "To-Dos"
        hdr.addStretch(1)
        left.addLayout(hdr)

        self.todoList = TodoTree(owner_widget=self, app=self.app, parent=self)

        self.todoList.itemDoubleClicked.connect(self.toggle_todo_done)

        left.addWidget(self.todoList, 1)

        # context menu for delete
        self.todoList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.todoList.customContextMenuRequested.connect(self._todo_context_menu)

        # F2 edits the currently selected to-do
        editAct = QAction(self)
        editAct.setShortcut(QKeySequence(Qt.Key_F2))
        editAct.triggered.connect(self._edit_selected_todo)
        self.addAction(editAct)

        # persist order on drop
        self.todoList.dropEvent = self._todo_dropEvent  # override

        # ----- Notes column -----
        right = QVBoxLayout()
        lblN = QLabel("Notes")
        self.notesEdit = QPlainTextEdit()
        self.notesEdit.setPlaceholderText("Notes for this chapter…")
        self.notesEdit.textChanged.connect(self._mark_notes_dirty)

        right.addWidget(lblN)
        right.addWidget(self.notesEdit, 1)

        outer.addLayout(left, 1)
        outer.addLayout(right, 1)

    # ----- Chapter binding -----
    def set_chapter(self, chap_id: int | None):
        self._chapter_id = chap_id
        self._notes_dirty = False
        self.reload()

    def reload(self):
        self.todoList.clear()
        self.notesEdit.blockSignals(True)
        self.notesEdit.setPlainText("")
        self.notesEdit.blockSignals(False)

        if not self._chapter_id:
            return

        cur = self.app.db.conn.cursor()
        # To-Dos ordered
        cur.execute("""SELECT id, text, is_done FROM chapter_notes
                       WHERE chapter_id=? AND kind='todo'
                       ORDER BY position, id""", (self._chapter_id,))
        for nid, text, done in cur.fetchall():
            it = QTreeWidgetItem([("✓ " if done else "□ ") + (text or "")])
            it.setData(0, Qt.UserRole, ("todo", int(nid), bool(done)))
            flags = it.flags()
            flags |= (Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)  # <- allow OnItem
            it.setFlags(flags)
            self.todoList.addTopLevelItem(it)


        # Notes: single row if exists
        cur.execute("""SELECT id, text FROM chapter_notes
                       WHERE chapter_id=? AND kind='note'
                       ORDER BY id LIMIT 1""", (self._chapter_id,))
        row = cur.fetchone()
        if row:
            self._note_row_id = int(row[0])
            txt = row[1] or ""
        else:
            self._note_row_id = None
            txt = ""
        self.notesEdit.blockSignals(True)
        self.notesEdit.setPlainText(txt)
        self.notesEdit.blockSignals(False)
        self._notes_dirty = False

    # ----- To-Dos -----
    def add_new_todo(self):
        """Insert an empty to-do at the end, select it, and open the edit dialog.
        If canceled or left blank, the placeholder is removed."""
        if not self._chapter_id:
            return
        cur = self.app.db.conn.cursor()
        # next position
        cur.execute("""SELECT COALESCE(MAX(position), -1) FROM chapter_notes
                    WHERE chapter_id=? AND kind='todo'""", (self._chapter_id,))
        next_pos = (cur.fetchone()[0] or -1) + 1
        # create empty placeholder
        cur.execute("""INSERT INTO chapter_notes (chapter_id, kind, text, is_done, position)
                    VALUES (?, 'todo', '', 0, ?)""", (self._chapter_id, next_pos))
        self.app.db.conn.commit()
        nid = cur.lastrowid

        # refresh UI, select new item
        self.reload()
        # find it and select
        for i in range(self.todoList.topLevelItemCount()):
            it = self.todoList.topLevelItem(i)
            d = it.data(0, Qt.UserRole)
            if d and d[0] == "todo" and int(d[1]) == int(nid):
                self.todoList.setCurrentItem(it)
                self.todoList.scrollToItem(it)
                break

        # mark as pending in case user cancels/empties
        self._pending_new_todo_id = nid
        # open edit popup
        self._edit_selected_todo()


    def toggle_todo_done(self, item, _col):
        d = item.data(0, Qt.UserRole)
        if not d or d[0] != "todo": return
        _, nid, done = d
        cur = self.app.db.conn.cursor()
        cur.execute("UPDATE chapter_notes SET is_done=? WHERE id=?", (0 if done else 1, nid))
        self.app.db.conn.commit()
        self.reload()

    def _todo_context_menu(self, pos):
        it = self.todoList.itemAt(pos)
        if not it:
            return
        d = it.data(0, Qt.UserRole)
        if not d or d[0] != "todo":
            return

        def current_text_without_box():
            # item text looks like "✓ Something" or "□ Something"
            raw = it.text(0)
            return re.sub(r'^[✓□]\s+', '', raw).strip()

        menu = QMenu(self)
        actEdit = menu.addAction("Edit…")
        actDel  = menu.addAction("Delete")
        a = menu.exec(self.todoList.viewport().mapToGlobal(pos))
        if a == actEdit:
            _, nid, done = d
            old_text = current_text_without_box()
            new_text, ok = QInputDialog.getText(self, "Edit To-Do", "To-Do text:", text=old_text)
            if ok and new_text.strip():
                cur = self.app.db.conn.cursor()
                cur.execute("UPDATE chapter_notes SET text=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (new_text.strip(), nid))
                self.app.db.conn.commit()
                self.reload()
        elif a == actDel:
            nid = d[1]
            cur = self.app.db.conn.cursor()
            cur.execute("DELETE FROM chapter_notes WHERE id=?", (nid,))
            self.app.db.conn.commit()
            self._compact_todo_positions()
            self.reload()

    def _edit_selected_todo(self):
        it = self.todoList.currentItem()
        if not it:
            return
        d = it.data(0, Qt.UserRole)
        if not d or d[0] != "todo":
            return

        _, nid, done = d
        old_text = re.sub(r'^[✓□]\s+', '', it.text(0)).strip()

        new_text, ok = QInputDialog.getText(self, "Edit To-Do", "To-Do text:", text=old_text)

        cur = self.app.db.conn.cursor()
        try:
            if ok and new_text.strip():
                cur.execute("UPDATE chapter_notes SET text=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (new_text.strip(), nid))
                self.app.db.conn.commit()
            else:
                # If this was a brand-new placeholder and user canceled/emptied, remove it
                if self._pending_new_todo_id == nid:
                    cur.execute("DELETE FROM chapter_notes WHERE id=?", (nid,))
                    self.app.db.conn.commit()
                    self._pending_new_todo_id = None
                    self._compact_todo_positions()
        finally:
            self.reload()
            # clear pending marker if it wasn't already cleared
            if self._pending_new_todo_id == nid:
                self._pending_new_todo_id = None

    def _compact_todo_positions(self):
        if not self._chapter_id: return
        cur = self.app.db.conn.cursor()
        cur.execute("""SELECT id FROM chapter_notes
                       WHERE chapter_id=? AND kind='todo'
                       ORDER BY position, id""", (self._chapter_id,))
        ids = [r[0] for r in cur.fetchall()]
        for pos, nid in enumerate(ids):
            cur.execute("UPDATE chapter_notes SET position=? WHERE id=?", (pos, nid))
        self.app.db.conn.commit()

    def _todo_dropEvent(self, event):
        # Let Qt do the reorder (shows the drop line, may temporarily nest)
        QTreeWidget.dropEvent(self.todoList, event)
        event.acceptProposedAction()

        if not self._chapter_id:
            return

        # Flatten: lift any children to be top-level right after their parent
        def flatten_once():
            changed = False
            for i in range(self.todoList.topLevelItemCount()):
                parent = self.todoList.topLevelItem(i)
                while parent.childCount() > 0:
                    child = parent.takeChild(0)
                    idx = self.todoList.indexOfTopLevelItem(parent)
                    self.todoList.insertTopLevelItem(idx + 1, child)
                    changed = True
            return changed
        while flatten_once():
            pass

        # Persist order 0..N-1
        cur = self.app.db.conn.cursor()
        ordered_ids = []
        for i in range(self.todoList.topLevelItemCount()):
            it = self.todoList.topLevelItem(i)
            d = it.data(0, Qt.UserRole)
            if d and d[0] == "todo":
                ordered_ids.append(int(d[1]))
        for pos, nid in enumerate(ordered_ids):
            cur.execute("UPDATE chapter_notes SET position=? WHERE id=?", (pos, nid))
        self.app.db.conn.commit()

        # (Optional) self.reload()

    # ----- Notes -----
    def _mark_notes_dirty(self):
        self._notes_dirty = True

    def save_if_dirty(self, chapter_id: int):
        """Persist notes text iff changed; To-Do list already persists on actions."""
        if not chapter_id or chapter_id != self._chapter_id:
            return
        if not self._notes_dirty:
            return
        txt = self.notesEdit.toPlainText().strip()
        cur = self.app.db.conn.cursor()
        if self._note_row_id is None:
            if txt:
                # create the single notes row
                cur.execute("""INSERT INTO chapter_notes (chapter_id, kind, text, is_done, position)
                               VALUES (?, 'note', ?, 0, 0)""", (chapter_id, txt))
                self._note_row_id = cur.lastrowid
        else:
            cur.execute("UPDATE chapter_notes SET text=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (txt, self._note_row_id))
        self.app.db.conn.commit()
        self._notes_dirty = False

