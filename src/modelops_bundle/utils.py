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


def format_storage_display(storage_type, config=None, entry=None, direction=None):
    """Format storage location for consistent display across CLI commands.
    
    Args:
        storage_type: StorageType enum or string ("oci", "blob", "OCI", "BLOB")
        config: Optional BundleConfig for provider/container info
        entry: Optional BundleFileEntry with blobRef for detailed info
        direction: Optional arrow direction ("→" for push, "←" for pull, None for no arrow)
    
    Returns:
        Formatted string like "→ OCI", "blob/azure:container", etc.
    """
    from .storage_models import StorageType
    
    # Normalize storage type
    if isinstance(storage_type, str):
        storage_type = StorageType.OCI if storage_type.lower() == "oci" else StorageType.BLOB
    
    # Build display string
    if storage_type == StorageType.OCI:
        display = "OCI"
    else:
        # Start with blob
        display = "blob"
        
        # Try to get detailed info from entry's blobRef first
        if entry and hasattr(entry, 'blobRef') and entry.blobRef and hasattr(entry.blobRef, 'uri'):
            uri = entry.blobRef.uri
            if uri.startswith("azure://"):
                # Extract container from azure://container/path
                parts = uri.split("/")
                if len(parts) > 2:
                    display = f"blob/azure:{parts[2]}"
            elif uri.startswith("s3://"):
                # Extract bucket from s3://bucket/path
                parts = uri.split("/")
                if len(parts) > 2:
                    display = f"blob/s3:{parts[2]}"
            elif uri.startswith("gs://"):
                # Extract bucket from gs://bucket/path
                parts = uri.split("/")
                if len(parts) > 2:
                    display = f"blob/gcs:{parts[2]}"
            elif uri.startswith("fs://"):
                # For filesystem URIs, just show "blob/fs" for consistency
                # (fs:// URIs often have absolute paths like fs:///abs/path)
                display = "blob/fs"
            elif uri.startswith("file://"):
                # Extract path from file:///path
                path = uri[7:]  # Remove file://
                if path.startswith("/"):
                    # Extract directory path
                    path_parts = path.split("/")
                    # Get parent directory (e.g., /shared/storage from /shared/storage/file.txt)
                    if len(path_parts) > 2:
                        parent = "/".join(path_parts[:-1])
                    else:
                        parent = path
                    display = f"blob/filesystem:{parent}"
                else:
                    display = "blob/filesystem"
        # Fall back to config if no blobRef
        elif config and hasattr(config, 'storage') and config.storage.uses_blob_storage:
            provider = config.storage.provider
            container = config.storage.container
            
            if provider == "azure" and container:
                display = f"blob/azure:{container}"
            elif provider == "s3" and container:
                display = f"blob/s3:{container}"
            elif provider == "gcs" and container:
                display = f"blob/gcs:{container}"
            elif provider == "fs":
                if container:
                    display = f"blob/fs:{container}"
                else:
                    display = "blob/fs"
    
    # Add direction arrow if specified
    if direction:
        display = f"{direction} {display}"
    
    return display
