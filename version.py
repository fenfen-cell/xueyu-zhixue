"""Detect stale background services without stopping unrelated Python processes."""
import hashlib
from pathlib import Path

VERSION='2.4.0'


def source_revision():
    root=Path(__file__).resolve().parent
    digest=hashlib.sha256()
    for name in ['version.py','app.py','agent.py','core.py','learning.py','access.py','connections.py','ocr_local.py','local_ocr_worker.cjs','ocr_vision.py','usage_limits.py','quality.py']:
        digest.update((root/name).read_bytes())
    return digest.hexdigest()[:16]
