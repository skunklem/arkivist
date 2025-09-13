import sys
from PySide6.QtWidgets import QApplication

from config import DEV_MODE, DB_PATH
from ui.main_window import StoryArkivist

def main():
    app = QApplication(sys.argv)
    w = StoryArkivist(dev_mode=DEV_MODE, db_path=DB_PATH)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
