"""Local content-addressed storage (CAS) implementation.

This module provides a local cache for bundle files using content-addressed storage
with SHA256 digests. It implements atomic operations, proper locking, and multiple
materialization strategies (reflink, hardlink, copy).

Key Features:
- Content-addressed storage with SHA256 digests
- Atomic file operations with fsync for durability
- Cross-platform file locking via portalocker
- Multiple materialization strategies with automatic fallback
- Protection against path traversal attacks
- Cache object immutability to prevent corruption

Technical Considerations:
- Cache objects are made read-only (0o444) after storage to prevent accidental modification
- Hardlink × read-only interaction: hardlinked files share inodes, so chmod affects both
- Lock files persist to avoid inode coordination issues (OS cleans up on crash)
- Platform-specific optimizations: sendfile on Linux, reflink via FICLONE
"""

from __future__ import annotations
import contextlib
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Literal, Optional

try:
    import portalocker
except ImportError:
    raise ImportError("portalocker is required for LocalCAS. Install with: pip install portalocker")

try:
    import platformdirs
except ImportError:
    # Fallback to basic implementation if platformdirs not available
    platformdirs = None

LinkMode = Literal["auto", "reflink", "hardlink", "copy"]

logger = logging.getLogger(__name__)

# ---- Platform-specific helpers ---------------------------------------------

def _get_default_cache_dir() -> Path:
    """Get platform-appropriate cache directory.
    
    Uses platformdirs if available, otherwise falls back to:
    - Linux/macOS: ~/.cache/modelops-bundle
    - Windows: %LOCALAPPDATA%/modelops-bundle/cache
    """
    if platformdirs:
        return Path(platformdirs.user_cache_dir("modelops-bundle", "modelops"))
    else:
        # Fallback implementation
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            return base / "modelops-bundle" / "cache"
        else:
            return Path.home() / ".cache" / "modelops-bundle"

def _fsync_file(path: Path) -> None:
    """Fsync a file to ensure durability.
    
    Opens with write permissions to ensure fsync actually works.
    """
    with open(path, "r+b") as f:
        os.fsync(f.fileno())

def _fsync_dir(path: Path) -> None:
    """Fsync a directory to ensure directory entry updates are durable.
    
    This is a best-effort operation that may not work on all platforms/filesystems.
    Windows and some filesystems don't support directory fsync.
    """
    try:
        # Use O_DIRECTORY flag if available (Linux)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        
        fd = os.open(str(path), flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, IOError):
        # Expected on Windows or filesystems that don't support directory fsync
        logger.debug("Directory fsync not supported for %s", path)

# ---- Safety validators ------------------------------------------------------

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

def _validate_sha256(digest: str) -> str:
    """Validate and extract SHA256 hex from digest string.
    
    Args:
        digest: Digest string in format "sha256:hexvalue"
        
    Returns:
        The 64-character hex string
        
    Raises:
        ValueError: If digest format is invalid
        
    Security:
        Prevents path traversal by validating hex format before using in paths.
    """
    if not digest.startswith("sha256:"):
        raise ValueError(f"Invalid digest scheme: {digest!r}")
    
    hex_part = digest.split(":", 1)[1]
    if not _HEX64.fullmatch(hex_part):
        raise ValueError(f"Invalid sha256 hex (must be 64 hex chars): {hex_part!r}")
    
    return hex_part

# ---- Materialization strategies --------------------------------------------

def _try_reflink(src: Path, dst: Path) -> bool:
    """Attempt to create a reflink (copy-on-write clone).
    
    Linux-only operation using FICLONE ioctl. Falls back silently on other platforms.
    
    Args:
        src: Source file in cache
        dst: Destination path
        
    Returns:
        True if reflink succeeded, False otherwise
        
    Technical Note:
        Reflinks create a new inode that shares data blocks via COW semantics.
        This means the destination can have independent permissions from the source.
    """
    if not sys.platform.startswith("linux"):
        return False
    
    try:
        import fcntl
        FICLONE = 0x40049409  # Linux ioctl value
        
        with open(src, "rb") as s, open(dst, "wb") as d:
            fcntl.ioctl(d.fileno(), FICLONE, s.fileno())
        return True
    except Exception:
        return False

def _copy_with_sendfile(src: Path, dst: Path) -> None:
    """Fast copy using sendfile syscall when available.
    
    Falls back to shutil.copy2 if sendfile not available or fails.
    Preserves file metadata but doesn't fsync (caller's responsibility).
    
    Technical Note:
        sendfile is a zero-copy optimization on Linux that transfers data
        directly between file descriptors in kernel space.
    """
    if not sys.platform.startswith("linux"):
        shutil.copy2(src, dst)
        return
    
    try:
        with open(src, "rb") as s, open(dst, "wb") as d:
            src_fd, dst_fd = s.fileno(), d.fileno()
            offset = 0
            size = os.fstat(src_fd).st_size
            
            while offset < size:
                sent = os.sendfile(dst_fd, src_fd, offset, size - offset)
                if sent == 0:
                    break
                offset += sent
            
            # Preserve metadata
            shutil.copystat(src, dst, follow_symlinks=True)
    except Exception:
        # Fallback to standard copy
        shutil.copy2(src, dst)

# ---- LocalCAS implementation ------------------------------------------------

class LocalCAS:
    """Local content-addressed storage with atomic operations and proper locking.
    
    This class provides a cache for bundle files using content-addressed storage.
    Files are stored by their SHA256 digest and can be materialized to destinations
    using various strategies (reflink, hardlink, copy).
    
    Directory Structure:
        <cache_root>/objects/sha256/ab/cd/<full_sha256_hex>
        
    Attributes:
        root: Cache root directory
        objdir: Object storage directory (root/objects/sha256)
        
    Thread Safety:
        All operations use file locking and are safe for concurrent access.
        
    Technical Notes:
        - Cache objects are made read-only (0o444) to prevent corruption
        - Lock files persist to avoid inode coordination issues
        - All operations are atomic with proper cleanup on failure
    """
    
    def __init__(self, root: Optional[Path] = None):
        """Initialize LocalCAS with specified or default cache directory.
        
        Args:
            root: Cache root directory. If None, uses platform-appropriate default.
        """
        self.root = Path(root) if root else _get_default_cache_dir()
        self.objdir = self.root / "objects" / "sha256"
        self.objdir.mkdir(parents=True, exist_ok=True)
    
    def path_for(self, digest: str) -> Path:
        """Get cache path for a digest.
        
        Args:
            digest: Digest string in format "sha256:hexvalue"
            
        Returns:
            Path where this object would be stored in cache
            
        Raises:
            ValueError: If digest format is invalid
            
        Security:
            Validates digest format to prevent path traversal attacks.
        """
        hex_part = _validate_sha256(digest)
        # Shard by first 4 hex characters for filesystem performance
        return self.objdir / hex_part[:2] / hex_part[2:4] / hex_part
    
    def has(self, digest: str) -> bool:
        """Check if object exists in cache.
        
        Args:
            digest: Digest to check
            
        Returns:
            True if object exists in cache
        """
        try:
            return self.path_for(digest).exists()
        except ValueError:
            return False
    
    def ensure_present(self, digest: str, fetch_to_path: Callable[[str], None]) -> Path:
        """Ensure object exists in cache, fetching if necessary.
        
        This method is atomic and safe for concurrent calls. If multiple processes
        try to fetch the same object, only one will perform the download.
        
        Args:
            digest: Expected digest of the object
            fetch_to_path: Callback that downloads content to a given path
            
        Returns:
            Path to the object in cache
            
        Raises:
            ValueError: If fetched content doesn't match expected digest
            
        Technical Details:
            1. Uses portalocker for cross-platform file locking
            2. Re-checks existence after acquiring lock (TOCTOU fix)
            3. Downloads to temp file, verifies digest, then atomically promotes
            4. Makes cache object read-only to prevent corruption
            5. Lock files persist but OS cleans up on process crash
        """
        dst = self.path_for(digest)
        
        # Fast path: already in cache
        if dst.exists():
            return dst
        
        # Ensure parent directories exist
        dst.parent.mkdir(parents=True, exist_ok=True)
        
        # Lock on a per-object lock file (persists, but OS cleans up on crash)
        lock_path = dst.with_suffix(".lock")
        
        with portalocker.Lock(str(lock_path), "w", timeout=300) as lock:
            # Re-check after acquiring lock (TOCTOU fix)
            if dst.exists():
                return dst
            
            # Download to temp file in same directory (for atomic rename)
            with tempfile.NamedTemporaryFile(
                prefix=".cas-",
                dir=str(dst.parent),
                delete=False
            ) as tmp:
                tmppath = Path(tmp.name)
            
            try:
                # Fetch content to temp file
                fetch_to_path(str(tmppath))
                
                # Verify digest
                from .utils import compute_digest
                actual = compute_digest(tmppath)
                if actual != digest:
                    raise ValueError(
                        f"Digest mismatch for {digest}: "
                        f"computed {actual} from {tmppath}"
                    )
                
                # Ensure content is durable BEFORE changing permissions
                _fsync_file(tmppath)
                
                # Make cache object read-only to prevent corruption
                # This happens BEFORE the atomic rename so the cache object
                # is immutable from the moment it becomes visible
                os.chmod(tmppath, 0o444)
                
                # Atomically promote to cache
                os.replace(str(tmppath), str(dst))
                
                # Ensure directory entry is durable
                _fsync_dir(dst.parent)
                
                logger.debug("CAS promoted: %s", dst)
                
            except Exception:
                # Clean up temp file on any failure
                with contextlib.suppress(OSError):
                    tmppath.unlink()
                raise
        
        return dst
    
    def materialize(
        self,
        digest: str,
        dest: Path,
        mode: LinkMode = "auto",
        skip_if_hardlink_and_readonly: bool = False
    ) -> None:
        """Materialize an object from cache to a destination.
        
        Attempts strategies in order based on mode:
        - auto: reflink → hardlink → copy
        - reflink: reflink only (fails if not supported)
        - hardlink: hardlink only (fails if not possible)
        - copy: always copy
        
        Args:
            digest: Digest of object to materialize
            dest: Destination path
            mode: Link mode to use
            skip_if_hardlink_and_readonly: If True and planning to make dest
                read-only, skip hardlink strategy to avoid affecting cache
                
        Raises:
            FileNotFoundError: If object not in cache
            OSError: If materialization fails
            
        Technical Note - Hardlink × Read-only Interaction:
            Hardlinks share the same inode as the source file. If you chmod or
            set immutable bits on a hardlinked file, it affects the cache object too!
            This can prevent cache cleanup and cause permission issues.
            
            Solutions:
            1. Set skip_if_hardlink_and_readonly=True when planning to make read-only
            2. Use copy mode instead of hardlink when read-only is needed
            3. Check st_nlink before applying chmod/immutable operations
            
        Atomicity:
            All strategies use atomic operations (temp file + rename) to ensure
            the destination either fully exists or doesn't exist at all.
        """
        src = self.path_for(digest)
        if not src.exists():
            raise FileNotFoundError(f"Object not in cache: {digest}")
        
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        # Try reflink first (if requested)
        if mode in ("reflink", "auto"):
            tmp = dest.with_name(f".{dest.name}.reflink")
            try:
                if _try_reflink(src, tmp):
                    _fsync_file(tmp)
                    os.replace(str(tmp), str(dest))
                    _fsync_dir(dest.parent)
                    logger.debug("Materialized via reflink: %s <- %s", dest, src)
                    return
                elif mode == "reflink":
                    raise OSError("Reflink not supported on this filesystem")
            finally:
                with contextlib.suppress(OSError):
                    tmp.unlink()
        
        # Try hardlink (if requested and safe)
        if mode in ("hardlink", "auto"):
            if skip_if_hardlink_and_readonly:
                logger.debug("Skipping hardlink due to read-only conflict")
            else:
                # Atomic hardlink via temp + rename
                tmp = dest.with_name(f".{dest.name}.hardlink")
                try:
                    os.link(src, tmp)
                    os.replace(str(tmp), str(dest))
                    _fsync_dir(dest.parent)
                    logger.debug("Materialized via hardlink: %s <- %s", dest, src)
                    return
                except OSError:
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                    if mode == "hardlink":
                        raise
        
        # Fall back to copy (always works)
        if mode in ("copy", "auto"):
            tmp = dest.with_name(f".{dest.name}.copy")
            try:
                _copy_with_sendfile(src, tmp)
                # Reset permissions since cache object is read-only
                os.chmod(tmp, 0o644)
                _fsync_file(tmp)
                os.replace(str(tmp), str(dest))
                _fsync_dir(dest.parent)
                logger.debug("Materialized via copy: %s <- %s", dest, src)
            finally:
                with contextlib.suppress(OSError):
                    tmp.unlink()
        else:
            raise ValueError(f"Invalid link mode: {mode}")
    
    def cleanup_old_objects(self, keep_recent_hours: int = 168) -> int:
        """Remove old objects from cache that haven't been accessed recently.
        
        Args:
            keep_recent_hours: Keep objects accessed within this many hours
            
        Returns:
            Number of objects removed
            
        Note:
            This is a best-effort operation. Objects may be locked or in use.
            Immutable objects (if set externally) cannot be removed.
        """
        import time
        cutoff = time.time() - (keep_recent_hours * 3600)
        removed = 0
        
        for obj_path in self.objdir.rglob("*"):
            if obj_path.is_file() and not obj_path.name.endswith(".lock"):
                try:
                    stat = obj_path.stat()
                    # Use access time if available, otherwise modification time
                    last_used = getattr(stat, "st_atime", stat.st_mtime)
                    
                    if last_used < cutoff:
                        obj_path.unlink()
                        removed += 1
                        logger.debug("Removed old cache object: %s", obj_path)
                except OSError as e:
                    logger.debug("Could not remove %s: %s", obj_path, e)
        
        return removed