from __future__ import annotations
import json
from PySide6 import QtCore

from ui.widgets.helpers import chapter_display_label
from .data import Chapter

class ChaptersModel(QtCore.QAbstractListModel):
    TitleRole = QtCore.Qt.UserRole + 1

    def __init__(self, chapters: list[Chapter]):
        super().__init__()
        self._chapters = chapters

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self._chapters)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        ch = self._chapters[index.row()]
        if role == QtCore.Qt.DisplayRole:
            return chapter_display_label(index.row(), ch.title)
        if role == self.TitleRole:
            return ch.title  # raw title for internal use
        return None

    def flags(self, index):
        base = super().flags(index)
        if index.isValid():
            return base | QtCore.Qt.ItemIsDragEnabled | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled
        return base | QtCore.Qt.ItemIsDropEnabled

    def supportedDropActions(self):
        return QtCore.Qt.MoveAction

    def mimeTypes(self): return ["application/x-chapter-row"]

    def mimeData(self, indexes):
        md = QtCore.QMimeData()
        rows = sorted({ix.row() for ix in indexes if ix.isValid()})
        md.setData("application/x-chapter-row", json.dumps(rows).encode("utf-8"))
        return md

    def dropMimeData(self, md, action, row, column, parent):
        if action != QtCore.Qt.MoveAction: return False
        if not md.hasFormat("application/x-chapter-row"): return False
        src_rows = json.loads(bytes(md.data("application/x-chapter-row")).decode("utf-8"))
        # Drop target row
        insert_row = row if row != -1 else self.rowCount()
        # Move contiguous blocks respecting order
        self.beginResetModel()
        moved = [self._chapters[r] for r in src_rows]
        for r in reversed(src_rows):
            del self._chapters[r]
            if r < insert_row:
                insert_row -= 1
        for i, ch in enumerate(moved):
            self._chapters.insert(insert_row + i, ch)
        self.endResetModel()
        return True

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if role == QtCore.Qt.EditRole and index.isValid():
            self._chapters[index.row()].title = str(value)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def insertChapter(self, row: int, chapter: "Chapter") -> int:
        row = max(0, min(row, self.rowCount()))
        self.beginInsertRows(QtCore.QModelIndex(), row, row)
        self._chapters.insert(row, chapter)
        self.endInsertRows()
        return row

    def removeChapter(self, row: int) -> bool:
        if 0 <= row < self.rowCount():
            self.beginRemoveRows(QtCore.QModelIndex(), row, row)
            self._chapters.pop(row)
            self.endRemoveRows()
            return True
        return False

    def chapter(self, row: int) -> Chapter:
        return self._chapters[row]

    def row_for_chapter_id(self, chap_id: int) -> int:
        items = self._chapters
        for i, ch in enumerate(items):
            if (hasattr(ch, "id") and ch.id == chap_id) \
               or (isinstance(ch, dict) and ch.get("id") == chap_id) \
               or (isinstance(ch, (list, tuple)) and ch and ch[0] == chap_id):
                return i
        return -1
    
    def chapter_id_for_row(self, row: int) -> int:
        items = getattr(self, "_chapters", None) or []
        if row < 0 or row >= len(items):
            return -1
        ch = items[row]
        return getattr(ch, "id", -1)

    def chapter_id_for_index(self, idx) -> int:
        # Accept QModelIndex OR int for extra robustness
        if isinstance(idx, int):
            return self.chapter_id_for_row(idx)
        if not isinstance(idx, QtCore.QModelIndex) or not idx.isValid():
            return -1
        return self.chapter_id_for_row(idx.row())
