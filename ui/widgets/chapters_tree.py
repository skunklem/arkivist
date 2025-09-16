from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QTreeWidget, QAbstractItemView, QMenu

class ChaptersTree(QTreeWidget):
    fileDropped = Signal(list)  # paths list
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.viewport().setAcceptDrops(True)
        self._drag_item = None
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropOverwriteMode(False)   # enables “between items” line instead of replacing
        self.setUniformRowHeights(True)        # smoother hit-testing for the drop line

        # beef up the drop indicator visually
        self.setStyleSheet("QTreeView::dropIndicator { height: 3px; background: #2d7dff; }")

        # scroll on edge-drag
        self.setAutoScroll(True)
        self.setAutoScrollMargin(24)   # start scrolling when cursor is 24px from edge
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

        # Right-click context menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._open_context_menu)

        self._reorder_locked = False

    def startDrag(self, actions):
        self._drag_item = self.currentItem()
        super().startDrag(actions)

    def set_reorder_locked(self, locked: bool):
        self._reorder_locked = bool(locked)

    def dragEnterEvent(self, event):
        # External files are always allowed
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        # Internal drag: block if locked
        if self._reorder_locked and event.source() is self:
            event.ignore()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        if self._reorder_locked and event.source() is self:
            event.ignore()
            return
        super().dragMoveEvent(event)

    def _fix_chapter_nesting(self):
        """If any chapter accidentally became a child of another chapter, lift it
        to be a sibling right after its parent chapter. Return set of affected book nodes."""
        affected_books = set()

        def is_book(it):
            d = it.data(0, Qt.UserRole) if it else None
            return bool(d and d[0] == "book")
        def is_chapter(it):
            d = it.data(0, Qt.UserRole) if it else None
            return bool(d and d[0] == "chapter")

        for i in range(self.topLevelItemCount()):
            book = self.topLevelItem(i)
            if not is_book(book):
                continue
            j = 0
            while j < book.childCount():
                node = book.child(j)
                if is_chapter(node) and node.childCount() > 0:
                    # lift all children (chapters) to be siblings after 'node'
                    insert_at = book.indexOfChild(node) + 1
                    while node.childCount() > 0:
                        ch = node.takeChild(0)
                        book.insertChild(insert_at, ch)
                        insert_at += 1
                    affected_books.add(book)
                j += 1
        return affected_books

    def dropEvent(self, event):
        mw = self.window()

        # --- External files → import once, no super().dropEvent in this branch
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            # emit once; the app-level slot should be connected with UniqueConnection
            self.fileDropped.emit(paths)
            event.acceptProposedAction()
            return

        # Internal drop: block reorder if locked
        if self._reorder_locked and event.source() is self:
            event.ignore()
            return

        # --- Internal move (reorder / cross-book move)
        drag_item = getattr(self, "_drag_item", None)   # set in startDrag
        src_book  = drag_item.parent() if drag_item else None
        drag_id   = None
        if drag_item:
            d = drag_item.data(0, Qt.UserRole)
            if d and d[0] == "chapter":
                drag_id = int(d[1])

        # Let Qt perform the visual/internal move first (gives you the drop line UX)
        super().dropEvent(event)
        event.acceptProposedAction()

        # --- Disallow nesting: if any chapter got children, lift them just after the parent
        def flatten_book(book_item):
            changed = False
            if not book_item:
                return False
            for i in range(book_item.childCount()):
                node = book_item.child(i)
                dd = node.data(0, Qt.UserRole)
                if dd and dd[0] == "chapter" and node.childCount() > 0:
                    insert_at = book_item.indexOfChild(node) + 1
                    while node.childCount() > 0:
                        child = node.takeChild(0)
                        book_item.insertChild(insert_at, child)
                        insert_at += 1
                    changed = True
            return changed

        touched_books = set()
        for i in range(self.topLevelItemCount()):
            b = self.topLevelItem(i)
            bd = b.data(0, Qt.UserRole)
            if not (bd and bd[0] == "book"):
                continue
            if flatten_book(b):
                touched_books.add(b)

        # If the dragged item somehow became top-level, append it back to its source book in DB
        if drag_item and drag_item.parent() is None and src_book is not None and drag_id is not None:
            if hasattr(mw, "move_chapter_to_index"):
                bdata = src_book.data(0, Qt.UserRole)
                if bdata and bdata[0] == "book":
                    dest_book_id = int(bdata[1])
                    # count current chapters under the source book
                    count = sum(
                        1 for i in range(src_book.childCount())
                        if (src_book.child(i).data(0, Qt.UserRole) or (None, None))[0] == "chapter"
                    )
                    mw.move_chapter_to_index(drag_id, dest_book_id, count)
                    mw.populate_chapters_tree()
                    if hasattr(mw, "focus_chapter_in_tree"):
                        mw.focus_chapter_in_tree(getattr(mw, "_current_chapter_id", None))
                    return

        # Otherwise compact & renumber source/dest books (and any flattened ones)
        if hasattr(mw, "compact_and_renumber_after_tree_move"):
            # dest book = parent of the moved item after Qt has relocated it
            dest_book = drag_item.parent() if drag_item else None
            src_b     = src_book
            dest_b    = dest_book
            active_id = getattr(mw, "_current_chapter_id", None)

            def do_sync():
                mw.compact_and_renumber_after_tree_move(src_b, dest_b)
                for tb in touched_books:
                    mw.compact_and_renumber_after_tree_move(tb, tb)
                if active_id and hasattr(mw, "focus_chapter_in_tree"):
                    mw.focus_chapter_in_tree(active_id)

            # defer so the model/view settle before we read/write DB
            QTimer.singleShot(0, do_sync)

    def _open_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        d = item.data(0, Qt.UserRole) or ()
        kind = d[0] if len(d) else None
        obj_id = int(d[1]) if len(d) > 1 else None

        menu = QMenu(self)
        if kind == "chapter":
            actRename = menu.addAction("Rename")
            actDelete = menu.addAction("Delete Chapter (soft)")
            act = menu.exec(self.viewport().mapToGlobal(pos))
            mw = self.window()
            if act == actRename and hasattr(mw, "_rename_chapter"):
                mw._rename_chapter(obj_id)
            elif act == actDelete and hasattr(mw, "_soft_delete_chapter"):
                mw._soft_delete_chapter(obj_id)
        elif kind == "book":
            # optional: allow rename/delete book
            actRename = menu.addAction("Rename Book")
            actDelete = menu.addAction("Delete Book (soft)")  # if you added books.deleted
            act = menu.exec(self.viewport().mapToGlobal(pos))
            mw = self.window()
            if act == actRename and hasattr(mw, "_rename_book"):
                mw._rename_book(obj_id)
            elif act == actDelete and hasattr(mw, "_soft_delete_book"):
                mw._soft_delete_book(obj_id)
