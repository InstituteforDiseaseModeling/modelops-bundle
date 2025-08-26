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


def format_iso_date(iso_string: str) -> str:
    """Clean up ISO 8601 timestamp for display.
    
    Examples:
        "2025-08-26T02:51:17.317839Z" -> "2025-08-26 02:51:17"
        "2025-08-26T02:51:17Z" -> "2025-08-26 02:51:17"
    """
    if "T" in iso_string and "." in iso_string:
        # Remove microseconds: 2025-08-26T02:51:17.317839Z -> 2025-08-26 02:51:17
        clean_date = iso_string.split(".")[0].replace("T", " ")
    elif "T" in iso_string:
        # Remove Z: 2025-08-26T02:51:17Z -> 2025-08-26 02:51:17
        clean_date = iso_string.rstrip("Z").replace("T", " ")
    else:
        clean_date = iso_string
    return clean_date


def humanize_date(iso_string: str) -> str:
    """Convert ISO 8601 timestamp to human-readable relative time.
    
    Examples:
        "2024-01-15T10:30:45Z" -> "2 hours ago"
        "2024-01-10T10:30:45Z" -> "5 days ago"
    """
    from datetime import datetime
    
    try:
        # Parse ISO timestamp
        dt = datetime.fromisoformat(iso_string.rstrip('Z'))
        now = datetime.utcnow()
        delta = now - dt
        
        # Convert to human-readable format
        seconds = delta.total_seconds()
        
        if seconds < 60:
            return "just now"
        elif seconds < 3600:  # Less than 1 hour
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif seconds < 86400:  # Less than 1 day
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif seconds < 604800:  # Less than 1 week
            days = int(seconds / 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"
        elif seconds < 2592000:  # Less than 30 days
            weeks = int(seconds / 604800)
            return f"{weeks} week{'s' if weeks != 1 else ''} ago"
        elif seconds < 31536000:  # Less than 1 year
            months = int(seconds / 2592000)
            return f"{months} month{'s' if months != 1 else ''} ago"
        else:
            years = int(seconds / 31536000)
            return f"{years} year{'s' if years != 1 else ''} ago"
    except (ValueError, AttributeError):
        # If parsing fails, return the original string
        return iso_string
