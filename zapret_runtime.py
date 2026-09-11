"""Serial process transitions with cancellation independent of Tk callbacks."""
import threading
from contextlib import contextmanager


class Superseded(RuntimeError):
    pass


class RuntimeController:
    def __init__(self, desired="stopped"):
        self.desired = desired
        self.token = threading.Event()
        self.lock = threading.RLock()
        self._state_lock = threading.Lock()
        self.trial = None
        self.busy = False
        self.closed = False

    def request(self, desired=None, expected=None):
        with self._state_lock:
            if expected is not None and expected is not self.token:
                return None
            self.token.set()
            self.token = threading.Event()
            if desired is not None:
                self.desired = desired
            return self.token

    def valid(self, token):
        return token is self.token and not token.is_set() and not self.closed

    def set_desired(self, token, desired):
        with self._state_lock:
            if self.valid(token):
                self.desired = desired

    def check(self, token):
        if not self.valid(token):
            raise Superseded("Операция отменена")

    @contextmanager
    def transition(self, token):
        with self.lock:
            self.check(token)
            self.busy = True
            try:
                yield
            finally:
                self.busy = False

    def wait(self, token, seconds):
        return not token.wait(seconds) and self.valid(token)

    def close(self):
        self.closed = True
        self.token.set()
