"""Minimal fsynced success evidence for a database outage after LinkedIn accepts a post."""

import json
import os
import re
import tempfile
from pathlib import Path


SAFE_ATTEMPT = re.compile(r"\Aattempt:[a-zA-Z0-9:-]+\Z")


def _journal_path(data_dir: Path, attempt_id: str) -> Path:
    if not SAFE_ATTEMPT.fullmatch(attempt_id):
        raise ValueError("Invalid publication attempt ID")
    directory = data_dir / "publication-journal"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory / f"{attempt_id}.json"


def save_success(data_dir: Path, attempt_id: str, post_id: str) -> None:
    path = _journal_path(data_dir, attempt_id)
    directory = path.parent
    fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"attempt_id": attempt_id, "post_id": post_id}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_success(data_dir: Path, attempt_id: str) -> str | None:
    path = _journal_path(data_dir, attempt_id)
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if record.get("attempt_id") != attempt_id or not isinstance(record.get("post_id"), str):
        raise ValueError("Publication journal entry is invalid")
    return record["post_id"]


def remove_success(data_dir: Path, attempt_id: str) -> None:
    path = _journal_path(data_dir, attempt_id)
    path.unlink(missing_ok=True)
