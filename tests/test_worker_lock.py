import tempfile
import unittest
from pathlib import Path

from app.storage.process_lock import WorkerAlreadyRunning, WorkerLock


class WorkerLockTests(unittest.TestCase):
    def test_second_local_worker_cannot_acquire_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.lock"
            with WorkerLock(path):
                with self.assertRaises(WorkerAlreadyRunning):
                    with WorkerLock(path):
                        pass
            with WorkerLock(path):
                pass
