from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QAbstractItemView, QTreeWidgetItem


class NotesTree(QTreeWidget):
    """Simple tree for Notes nodes (categories, notes, member lists).

    This widget itself does not talk to the database; the main window
    is responsible for populating it using Database.notes_children().
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        # drag/drop, context menus etc can be added later as needed

    @staticmethod
    def make_item(title: str, node_id: int) -> QTreeWidgetItem:
        it = QTreeWidgetItem([title])
        it.setData(0, Qt.UserRole, ("notes_node", int(node_id)))
        return it
