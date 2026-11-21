from PySide6.QtWidgets import QLabel, QMainWindow


class ReaderWindow(QMainWindow):
    def __init__(self, paths, scenario=None):
        super().__init__()
        self.setWindowTitle("小说朗读室")
        self.setCentralWidget(QLabel("离线 Mock · 书架"))
        self.resize(1040, 690)
