from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QAbstractItemView, QMenu

from utils.files import singularize


class WorldTree(QTreeWidget):
    fileDropped = Signal(list)  # optional future use
    def __init__(self, parent=None, db=None):
        super().__init__(parent)
        self.db = db
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.viewport().setAcceptDrops(True)
        self._drag_item = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._open_context_menu)

    def startDrag(self, actions):
        self._drag_item = self.currentItem()
        super().startDrag(actions)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() or event.source() is self:
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
        mw = self.window()
        if hasattr(mw, "sync_world_order_from_tree"):
            mw.sync_world_order_from_tree()
        event.acceptProposedAction()

    def _open_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        d = item.data(0, Qt.UserRole) or ()
        kind = d[0] if len(d) else None
        obj_id = int(d[1]) if len(d) > 1 else None
        if kind not in ("world_cat", "world_item"):
            return

        menu = QMenu(self)
        mw = self.window()
        item_type = self.db.world_item_type(obj_id)

        # --- Add menu items ---
        actInsert = None
        actInsertAbove = None
        actInsertBelow = None
        actEditChar = None

        # OPTION: Insert nested item under category
        if kind == "world_cat":
            cat_id = int(d[1])
            cat_name = item.text(0)
            singular = singularize(cat_name)
            actInsert = menu.addAction(f"New {singular}")

        # Make character editor available for characters
        elif kind == "world_item":
            # OPTION: Character editor
            if item_type == "character":
                actEditChar = menu.addAction("Edit Character")
            # OPTION: Insert new chapter above or below
            actInsertAbove = menu.addAction("New above")
            actInsertBelow = menu.addAction("New below")

        actRename = menu.addAction("Rename")
        actDelete = menu.addAction("Delete (soft)")

        # --- Finalize menu ---
        act = menu.exec(self.viewport().mapToGlobal(pos))

        # --- Handle actions ---
        if act == actEditChar and actEditChar is not None:
            # mw.open_character_editor(obj_id)
            mw.open_character_dialog(obj_id)
        elif act == actRename and hasattr(mw, "_rename_world_object"):
            mw._rename_world_object(kind, obj_id)
        elif act == actDelete and hasattr(mw, "_soft_delete_world_object"):
            mw._soft_delete_world_object(kind, obj_id)
        if act == actInsert and actInsert is not None:
            # ask main window to create an inline editable temp item
            if hasattr(mw, "insert_new_world_item_inline"):
                mw.insert_new_world_item_inline(obj_id, item)
        if act == actInsertAbove and hasattr(mw, "insert_new_world_item_inline"):
            parent_cat = item.parent()
            cat_data = parent_cat.data(0, Qt.UserRole) if parent_cat else None
            if not cat_data or cat_data[0] != "world_cat":
                return
            mw.insert_new_world_item_inline(int(cat_data[1]), parent_cat, mode="above", ref_item=item)
            return
        if act == actInsertBelow and hasattr(mw, "insert_new_world_item_inline"):
            parent_cat = item.parent()
            cat_data = parent_cat.data(0, Qt.UserRole) if parent_cat else None
            if not cat_data or cat_data[0] != "world_cat":
                return
            mw.insert_new_world_item_inline(int(cat_data[1]), parent_cat, mode="below", ref_item=item)
            return