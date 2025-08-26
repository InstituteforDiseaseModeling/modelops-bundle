"""Utility functions for modelops-bundle."""

from pathlib import Path
import hashlib


def compute_digest(path: Path) -> str:
    """Compute SHA256 digest of a file."""
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


def humanize_size(size: float) -> str:
    """Convert bytes to human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

def get_timestamp() -> float:
    """Get current timestamp as Unix epoch."""
    import time
    return time.time()


def get_iso_timestamp() -> str:
    """Get current timestamp in ISO 8601 format for OCI annotations."""
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"
