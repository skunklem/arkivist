from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QComboBox,
    QMenu, QHeaderView, QAbstractScrollArea, QFrame, QStyledItemDelegate,
    QSizePolicy
)

TYPE_COL  = 0   # alias_type column index
ALIAS_COL = 1   # alias column index

class _TypeDelegate(QStyledItemDelegate):
    """Editable combo for the Type column on existing rows."""
    def __init__(self, types_provider, parent=None):
        super().__init__(parent)
        self._types = types_provider  # callable -> list[str]

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setEditable(True)  # write-ins
        cb.addItems(self._types())
        return cb

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.model().data(index, Qt.EditRole) or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText().strip(), Qt.EditRole)

class InlineAliasTable(QWidget):
    """Aliases with an 'add row' under headers and inline edit/delete."""
    rowAdded   = Signal(str, str)          # alias, type
    rowEdited  = Signal(int, str, str)     # id, alias, type
    rowDeleted = Signal(int)               # id

    def __init__(self, types_provider, parent=None):
        super().__init__(parent)
        self._types_provider = types_provider
        self._rows = []  # [{'id':..,'alias':..,'alias_type':..},...]

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Type", "Alias"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.SelectedClicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        # compact look (match facet tables)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableView { border: none; }")
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # ← important
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)

        vh = self.table.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.Fixed)      # ← fixed per-row height
        self.table.setWordWrap(False)  # This one is supported on QTableWidget

        # choose a sensible default row height (font + padding)
        row_h = self.table.fontMetrics().height() + 10
        vh.setDefaultSectionSize(row_h)

        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)

        # delegate for Type col (existing rows)
        self.table.setItemDelegateForColumn(0, _TypeDelegate(self._types_provider, self.table))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)  # ← match facet tables to avoid extra vertical gap
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)  # ← let dialog scroll, not table
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.table)

        self._install_add_row()

        # persist inline edits on existing rows
        self.table.itemChanged.connect(self._on_item_changed)

    # --- public API ---
    def set_rows(self, rows: list[dict]):
        """rows: list of sqlite3.Row or dict with keys id, alias, alias_type."""
        # rows => [{'id':..,'alias':..,'alias_type':..}, ...]
        t = self.table
        self._rows = list(rows)             # ← keep model in sync
        t.blockSignals(True)

        # keep add-row at 0
        while t.rowCount() > 1:
            t.removeRow(1)
        
        # rebuild other rows
        for row in rows:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, TYPE_COL,  QTableWidgetItem(row["alias_type"] or ""))
            t.setItem(r, ALIAS_COL, QTableWidgetItem(row["alias"] or ""))

        t.blockSignals(False)
        self.fit_height()
        QTimer.singleShot(0, self.fit_height)

    def fit_height(self, min_rows=1, pad=6):
        # rows visible = add-row (1) + data rows
        visible_rows = 1 + len(self._rows)
        rows = max(visible_rows, min_rows)

        hh = self.table.horizontalHeader()
        header_h = hh.height() if hh else 0

        vh = self.table.verticalHeader()
        row_h = vh.defaultSectionSize() or (self.table.fontMetrics().height() + 10)

        frame = self.table.frameWidth() * 2
        hscroll = self.table.horizontalScrollBar()
        hsb_h = (hscroll.sizeHint().height() if hscroll and hscroll.isVisible() else 0)

        total_h = header_h + rows * row_h + frame + pad + hsb_h
        self.table.setMinimumHeight(total_h)
        self.table.setMaximumHeight(total_h)

    # --- internals ---
    def _install_add_row(self):
        self.table.insertRow(0)

        # Type combobox with placeholder
        cb = QComboBox()
        cb.setEditable(False)
        types = self._types_provider()
        placeholder = "Add alias"
        # build list with placeholder at 0
        items = [placeholder]
        # prefer "nickname" first if present
        if "nickname" in types:
            items.append("nickname")
            items += [t for t in types if t != "nickname"]
        else:
            items += types
        cb.addItems(items)
        self.table.setCellWidget(0, TYPE_COL, cb)

        # Alias cell (start empty; we’ll focus it after type is chosen)
        it_alias = QTableWidgetItem("")
        it_alias.setFlags(it_alias.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled)
        self.table.setItem(0, ALIAS_COL, it_alias)

        # When user picks a real type, jump to alias cell editor
        def _type_changed(idx):
            if idx <= 0:  # still on placeholder
                return
            self.table.setCurrentCell(0, 1)
            self.table.editItem(self.table.item(0, 1))
        cb.currentIndexChanged.connect(_type_changed)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.table.currentRow() == 0:
                # TYPE is col 0, ALIAS is col 1
                alias_item = self.table.item(0, ALIAS_COL)
                alias = (alias_item.text() if alias_item else "").strip()

                cb = self.table.cellWidget(0, TYPE_COL)  # QComboBox for type
                a_type = cb.currentText().strip() if cb else ""

                # ignore if placeholder or empty
                if not alias or not a_type or a_type == "Add alias…":
                    return

                # emit in the *correct order*: (alias_type, alias)
                self.rowAdded.emit(a_type, alias)

                # reset add-row
                if alias_item:
                    alias_item.setText("")
                if cb:
                    cb.blockSignals(True)
                    cb.setCurrentIndex(0)  # back to placeholder
                    cb.blockSignals(False)

                self.fit_height()
                QTimer.singleShot(0, self.fit_height)
                return
        super().keyPressEvent(e)

    def _append_existing_row(self, rid, alias, a_type):
        self._rows.append({"id":rid,"alias_type":a_type,"alias":alias})
        row = self.table.rowCount()
        self.table.insertRow(row)

        c0 = QTableWidgetItem(a_type)
        c0.setData(Qt.UserRole, rid)
        c0.setFlags(c0.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, 0, c0)

        c1 = QTableWidgetItem(alias)
        c1.setFlags(c1.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, 1, c1)

        self.fit_height()
        QTimer.singleShot(0, self.fit_height)

    def _on_item_changed(self, item: QTableWidgetItem):
        r, c = item.row(), item.column()
        if r == 0:   # ignore add-row
            return
        val = (item.text() or "").strip()
        row_id = self._rows[r-1]["id"]

        if c == TYPE_COL:
            # update alias_type
            self.rowEdited.emit(row_id, "alias_type", val)
        elif c == ALIAS_COL:
            # update alias
            self.rowEdited.emit(row_id, "alias", val)

    def _context_menu(self, pos):
        r = self.table.rowAt(pos.y())
        if r <= 0:
            return
        m = QMenu(self)
        aDel = m.addAction("Delete")
        if m.exec(self.table.viewport().mapToGlobal(pos)) == aDel:
            rid = self.table.item(r,0).data(Qt.UserRole)
            if rid:
                self.rowDeleted.emit(int(rid))
            self.table.removeRow(r)
            self.fit_height()
            QTimer.singleShot(0, self.fit_height)
