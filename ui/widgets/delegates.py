from PySide6.QtWidgets import QStyledItemDelegate, QComboBox

class AliasTypeDelegate(QStyledItemDelegate):
    def __init__(self, types_provider, parent=None):
        super().__init__(parent)
        self._types_provider = types_provider  # callable -> list[str]

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setEditable(True)               # allow write-ins
        cb.addItems(self._types_provider())
        return cb

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.model().data(index, Qt.EditRole) or "")

    def setModelData(self, editor, model, index):
        text = editor.currentText().strip()
        model.setData(index, text, Qt.EditRole)