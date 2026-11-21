"""The repository thread owns its connection. Blocking callers run in the pool."""

from concurrent.futures import Future
from threading import Event, get_ident

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Qt, Signal, Slot


class RepositoryWorker(QObject):
    invoke = Signal(object)

    def __init__(self, factory):
        super().__init__()
        self.factory = factory
        self.store = None
        self.thread_id = None
        self.invoke.connect(self.dispatch, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def dispatch(self, command):
        name, args, future = command
        try:
            if self.store is None:
                self.store = self.factory()
                self.thread_id = get_ident()
            result = getattr(self.store, name)(*args)
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)


class Repository:
    def __init__(self, factory):
        self.thread = QThread()
        self.thread.setObjectName("RepositoryWorker")
        self.worker = RepositoryWorker(factory)
        self.worker.moveToThread(self.thread)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    def submit(self, name, *args):
        future = Future()
        self.worker.invoke.emit((name, args, future))
        return future

    def call(self, name, *args):
        return self.submit(name, *args).result()

    def close(self):
        try:
            self.call("close")
        finally:
            self.thread.quit()
            self.thread.wait()


class Task(QRunnable):
    def __init__(self, name, fn, event, done):
        super().__init__()
        self.name, self.fn, self.event, self.done = name, fn, event, done

    def run(self):
        try:
            result, error = self.fn(self.event), None
        except Exception as exc:
            result, error = None, exc
        self.done.emit(self.name, result, error)


class Background(QObject):
    finished = Signal(str, object, object)

    def __init__(self):
        super().__init__()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.events = {}
        self.finished.connect(self._forget, Qt.ConnectionType.QueuedConnection)

    def start(self, name, fn, priority=0):
        if name in self.events or len(self.events) >= 32:
            raise RuntimeError("后台队列已满或任务重复")
        event = Event()
        self.events[name] = event
        self.pool.start(Task(name, fn, event, self.finished), priority)
        return event

    @Slot(str, object, object)
    def _forget(self, name, _result, _error):
        self.events.pop(name, None)

    def close(self):
        for event in self.events.values():
            event.set()
        self.pool.clear()
        self.pool.waitForDone()
