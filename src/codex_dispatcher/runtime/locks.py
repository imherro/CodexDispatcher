import threading


class ThreadLocks:
    _guard = threading.Lock()
    _locks = {}

    @classmethod
    def get(cls, thread_id):
        with cls._guard:
            return cls._locks.setdefault(thread_id, threading.Lock())
