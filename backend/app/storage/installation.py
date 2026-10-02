"""Bind a local installation to one configured MongoDB destination."""

import hashlib
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit


class InstallationError(RuntimeError):
    pass


def _destination_fingerprint(mongo_uri: str, database: str) -> str:
    try:
        parsed = urlsplit(mongo_uri)
    except ValueError as exc:
        raise InstallationError("Configured MongoDB URI is invalid") from exc
    hosts = parsed.netloc.rsplit("@", 1)[-1].lower()
    if parsed.scheme not in {"mongodb", "mongodb+srv"} or not hosts or not database:
        raise InstallationError("Configured MongoDB destination is invalid")
    return hashlib.sha256(f"{parsed.scheme}\0{hosts}\0{database}".encode()).hexdigest()


def _write_identity(path: Path, record: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def installation_identity(data_dir: Path, mongo_uri: str, database: str) -> str:
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)
    path = data_dir / "installation.json"
    destination = _destination_fingerprint(mongo_uri, database)
    if path.exists():
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise InstallationError("Local installation identity is unreadable") from exc
        if not record.get("id"):
            raise InstallationError("Local installation identity is incomplete")
        if record.get("destination_fingerprint") != destination:
            legacy = hashlib.sha256(f"{mongo_uri}\0{database}".encode()).hexdigest()
            if record.get("destination_fingerprint") != legacy:
                raise InstallationError("Configured database differs from this installation's bound database")
            record["destination_fingerprint"] = destination
            record["destination_fingerprint_version"] = 2
            _write_identity(path, record)
        return str(record["id"])
    record = {"id": str(uuid.uuid4()), "destination_fingerprint": destination,
              "destination_fingerprint_version": 2}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return installation_identity(data_dir, mongo_uri, database)
    with os.fdopen(fd, "w") as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return record["id"]
