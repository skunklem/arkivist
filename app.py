import sys
from PySide6.QtWidgets import QApplication

from config import DEV_MODE, DB_PATH
from ui.main_window import StoryArkivist
from ui.widgets.theme_manager import theme_manager

def main():
    app = QApplication(sys.argv)

    # Pick default theme index if you want (0=Light, 1=Dark, 2=High Contrast, 3=Fluent)
    theme_manager.set_index(1)  # start in Dark
    theme_manager.apply(app, font_family="Inter", base_pt=10)

    w = StoryArkivist(dev_mode=DEV_MODE, db_path=DB_PATH)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
