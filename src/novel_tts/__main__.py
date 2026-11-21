import argparse
import sys
from pathlib import Path


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from novel_tts.infrastructure.paths import Paths
    from novel_tts.ui.window import ReaderWindow

    parser = argparse.ArgumentParser(description="小说朗读室")
    parser.add_argument("--mode", choices=["mock"], default="mock")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--scenario")
    parser.add_argument("--smoke", action="store_true", help="打开并关闭窗口")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("NovelTTSReader")
    window = ReaderWindow(Paths(args.data_dir), args.scenario)
    window.show()
    if args.smoke:
        QTimer.singleShot(450, window.close)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
