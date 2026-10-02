"""Store uploaded originals privately and parse in a bounded child process."""

import asyncio
import json
import os
import subprocess
import sys
import time

import psutil
from pathlib import Path
from uuid import uuid4

from app.services.resume_parser import MAX_INPUT


class ResumeImportError(ValueError):
    pass


async def save_upload(request, data_dir: Path) -> tuple[Path, str]:
    upload_dir = data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(upload_dir, 0o700)
    target = upload_dir / f"{uuid4()}.upload"
    temporary = upload_dir / f"{uuid4()}.tmp"
    count = 0
    try:
        with temporary.open("xb") as output:
            os.chmod(temporary, 0o600)
            async for chunk in request.stream():
                count += len(chunk)
                if count > MAX_INPUT:
                    raise ResumeImportError("Resume exceeds the 10 MiB limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if count == 0:
            raise ResumeImportError("Resume file is empty")
        temporary.replace(target)
        return target, str(target.relative_to(data_dir))
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _parse(path: Path, kind: str) -> list[dict[str, str]]:
    script = Path(__file__).with_name("resume_parser.py")
    process = subprocess.Popen(
        [sys.executable, "-I", str(script), str(path), kind],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"},
    )
    deadline = time.monotonic() + 32
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResumeImportError("Resume parsing timed out")
            try:
                stdout, _ = process.communicate(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                if sys.platform == "darwin":
                    try:
                        memory = psutil.Process(process.pid).memory_info().rss
                    except psutil.NoSuchProcess:
                        continue
                    except psutil.Error as exc:
                        raise ResumeImportError("Parser memory supervision is unavailable") from exc
                    if memory > 512 * 1024 * 1024:
                        raise ResumeImportError("Resume parser exceeded its memory limit")
    except Exception:
        process.kill()
        process.communicate()
        raise
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError) as exc:
        raise ResumeImportError("Resume parsing failed") from exc
    if process.returncode != 0:
        raise ResumeImportError(payload.get("error", "Resume parsing failed"))
    return payload["sections"]


async def parse_resume(path: Path, kind: str) -> list[dict[str, str]]:
    return await asyncio.to_thread(_parse, path, kind)
