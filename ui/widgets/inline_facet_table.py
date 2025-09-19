from PySide6.QtWidgets import (
    QWidget, QTableWidget, QTableWidgetItem, QComboBox, QVBoxLayout, QMenu
)
from PySide6.QtCore import Qt, Signal

from ui.widgets.helpers import fit_table_height_to_rows

class InlineFacetTable(QWidget):
    rowAdded = Signal(str, str, str)        # label, value, note
    rowEdited = Signal(int, str, str, str)  # facet_id, label, value, note
    rowDeleted = Signal(int)                # facet_id

    def __init__(self, app, labels_suggest: list[str], parent=None):
        super().__init__(parent)
        self.app = app
        self._labels = labels_suggest or []
        self._rows = []  # [{'id':..,'alias':..,'alias_type':..},...]
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Trait", "Description", "Note"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.SelectedClicked)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)

        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.addWidget(self._table)

        self._install_add_row()

    @property
    def table(self):
        return self._table

    def _install_add_row(self):
        # Row 0 is “add” row
        self._table.insertRow(0)
        # Column 0: combobox w/ “Add trait”
        cb = QComboBox()
        cb.addItem("Add trait")
        for s in self._labels:
            cb.addItem(s)
        cb.currentIndexChanged.connect(lambda i: self._on_add_combo_changed(i, cb))
        self._table.setCellWidget(0, 0, cb)

        # Col 1 & 2: disabled until combo changed
        for col in (1,2):
            it = QTableWidgetItem("")
            it.setFlags((it.flags() | Qt.ItemIsEnabled) & ~Qt.ItemIsEditable)
            self._table.setItem(0, col, it)

    def _on_add_combo_changed(self, idx, cb):
        if idx <= 0:
            # reset value/note cells disabled
            for col in (1,2):
                it = self._table.item(0, col)
                it.setText("")
                it.setFlags((it.flags() | Qt.ItemIsEnabled) & ~Qt.ItemIsEditable)
            return
        # enable inline editing for value/note
        for col in (1,2):
            it = self._table.item(0, col)
            it.setFlags(it.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled)
        self._table.editItem(self._table.item(0,1))  # focus “Value”

    def keyPressEvent(self, e):
        # Enter on add row commits if label chosen
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            r = self._table.currentRow()
            if r == 0:
                cb = self._table.cellWidget(0,0)
                label = cb.currentText()
                if label and label != "Add trait":
                    value = self._table.item(0,1).text().strip()
                    note  = self._table.item(0,2).text().strip()
                    # Allow empty value; if both value and note empty, revert to default
                    if not value and not note:
                        cb.setCurrentIndex(0)
                        self._on_add_combo_changed(0, cb)
                        return
                    # Emit add
                    self.rowAdded.emit(label, value, note)
                    # Reset add row
                    cb.setCurrentIndex(0)
                    self._on_add_combo_changed(0, cb)
                    return
        super().keyPressEvent(e)

    def set_rows(self, rows: list[dict], min_rows=1):
        """rows: [{id,label,value,note}]"""
        # clear (keep add row)
        while self._table.rowCount() > 1:
            self._table.removeRow(1)
        self._rows = rows[:]
        # populate after add-row
        for i, r in enumerate(self._rows, start=1):
            print(f"row {i}: {r}")
            self._table.insertRow(i)
            # label
            it0 = QTableWidgetItem(r["label"])
            it0.setData(Qt.UserRole, int(r["id"]))
            # editable
            it0.setFlags(it0.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(i, 0, it0)
            # value
            it1 = QTableWidgetItem(r["value"])
            it1.setFlags(it1.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(i, 1, it1)
            # note (tooltip helpful)
            it2 = QTableWidgetItem(r["note"])
            it2.setToolTip(r["note"])
            it2.setFlags(it2.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(i, 2, it2)

        # set table height
        fit_table_height_to_rows(self._table, min_rows=min_rows)

        # react to edits
        self._table.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, it: QTableWidgetItem):
        r = it.row()
        if r == 0:  # add row
            return
        print("row changed", r)
        facet_id = self._table.item(r, 0).data(Qt.UserRole)
        label = self._table.item(r, 0).text().strip()
        value = self._table.item(r, 1)
        value = value.text().strip() if value is not None else None
        note  = self._table.item(r, 2)
        note  = note.text().strip() if note is not None else None
        if facet_id:
            self.rowEdited.emit(int(facet_id), label, value, note)

    def _context_menu(self, pos):
        r = self._table.rowAt(pos.y())
        if r <= 0:  # not on add-row or outside
            return
        m = QMenu(self)
        aDel = m.addAction("Delete")
        if m.exec(self._table.viewport().mapToGlobal(pos)) == aDel:
            facet_id = self._table.item(r, 0).data(Qt.UserRole)
            if facet_id:
                self.rowDeleted.emit(int(facet_id))
            self._table.removeRow(r)
