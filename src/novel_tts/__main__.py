import argparse
import sys
import tempfile
from pathlib import Path


def main():
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication
    from novel_tts.infrastructure.paths import Paths
    from novel_tts.ui.window import ReaderWindow

    parser = argparse.ArgumentParser(description="小说朗读室")
    parser.add_argument("--mode", choices=["mock"], default="mock")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--scenario")
    parser.add_argument("--smoke", action="store_true", help="打开并关闭窗口")
    parser.add_argument("--self-test",action="store_true",help="在临时书库测试样书和整章导出")
    parser.add_argument("--screenshot",type=Path,help="保存 self-test 的阅读窗口")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("NovelTTSReader")
    scratch = tempfile.TemporaryDirectory(prefix="novel-check-") if args.self_test else None
    paths = Paths(Path(scratch.name) if scratch else args.data_dir)
    lock = QLockFile(str(paths.data / "reader.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        print("书库已由另一窗口打开",file=sys.stderr)
        return 2
    window = ReaderWindow(paths, args.scenario)
    window.show()
    if args.smoke:
        QTimer.singleShot(450, window.close)
    if args.self_test:
        from novel_tts.ui.selfcheck import start_check
        start_check(app,window,paths.data,args.screenshot)
    result = app.exec()
    lock.unlock()
    if scratch:
        scratch.cleanup()
    return result


if __name__ == "__main__":
    sys.exit(main())
