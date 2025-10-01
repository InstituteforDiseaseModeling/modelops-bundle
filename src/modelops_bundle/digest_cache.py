"""Digest caching for performance optimization."""

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .hashing import compute_file_digest


class DigestCache:
    """Cache file digests based on stat info for performance.

    Uses SQLite for persistence with (path, size, mtime, inode) as cache key.
    Thread-safe with proper locking.
    """

    def __init__(self, cache_path: Path):
        """Initialize cache with database path.

        Args:
            cache_path: Path to SQLite database file
        """
        self.cache_path = cache_path
        self._lock = threading.Lock()
        self._init_database()

    def _init_database(self):
        """Initialize SQLite schema."""
        with self._lock:
            conn = sqlite3.connect(str(self.cache_path))
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS digest_cache (
                        path TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        inode INTEGER NOT NULL,
                        digest TEXT NOT NULL,
                        PRIMARY KEY (path, size, mtime_ns, inode)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_path ON digest_cache(path)
                """)
                conn.commit()
            finally:
                conn.close()

    def get_or_compute(self, path: Path) -> str:
        """Get cached digest or compute if not cached.

        Args:
            path: Path to get digest for

        Returns:
            File digest as sha256:xxxx
        """
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        stat = path.stat()
        cache_key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ino)

        # Try cache first
        cached = self._lookup(cache_key)
        if cached:
            return cached

        # Compute and cache
        digest = compute_file_digest(path)
        self._store(cache_key, digest)
        return digest

    def _lookup(self, cache_key: Tuple[str, int, int, int]) -> Optional[str]:
        """Look up digest in cache.

        Args:
            cache_key: (path, size, mtime_ns, inode)

        Returns:
            Cached digest or None
        """
        with self._lock:
            conn = sqlite3.connect(str(self.cache_path))
            try:
                cursor = conn.execute(
                    "SELECT digest FROM digest_cache WHERE path = ? AND size = ? AND mtime_ns = ? AND inode = ?",
                    cache_key,
                )
                row = cursor.fetchone()
                return row[0] if row else None
            finally:
                conn.close()

    def _store(self, cache_key: Tuple[str, int, int, int], digest: str):
        """Store digest in cache.

        Args:
            cache_key: (path, size, mtime_ns, inode)
            digest: Computed digest
        """
        with self._lock:
            conn = sqlite3.connect(str(self.cache_path))
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO digest_cache (path, size, mtime_ns, inode, digest)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    cache_key + (digest,),
                )
                conn.commit()
            finally:
                conn.close()

    def clear_stale(self):
        """Remove entries for files that no longer exist or have changed."""
        with self._lock:
            conn = sqlite3.connect(str(self.cache_path))
            try:
                cursor = conn.execute("SELECT DISTINCT path FROM digest_cache")
                paths = [row[0] for row in cursor]

                stale_paths = []
                for path_str in paths:
                    path = Path(path_str)
                    if not path.exists():
                        stale_paths.append(path_str)

                if stale_paths:
                    placeholders = ",".join("?" * len(stale_paths))
                    conn.execute(
                        f"DELETE FROM digest_cache WHERE path IN ({placeholders})",
                        stale_paths,
                    )
                    conn.commit()
            finally:
                conn.close()


def compute_digests_parallel(
    paths: List[Path], max_workers: int = 4, cache: Optional[DigestCache] = None
) -> Dict[str, str]:
    """Compute digests for multiple files in parallel.

    Args:
        paths: List of paths to compute digests for
        max_workers: Number of parallel workers
        cache: Optional digest cache to use

    Returns:
        Dict mapping canonical paths to digests
    """
    def compute_one(path: Path) -> Tuple[str, Optional[str]]:
        """Compute digest for one file."""
        if not path.exists():
            return str(path), None

        if cache:
            try:
                digest = cache.get_or_compute(path)
            except Exception:
                # Fall back to direct computation
                digest = compute_file_digest(path)
        else:
            digest = compute_file_digest(path)

        return str(path), digest

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(compute_one, p) for p in paths]
        results = {}
        for future in futures:
            path_str, digest = future.result()
            if digest:
                results[path_str] = digest
        return results


class SymlinkPolicy:
    """Policy for handling symbolic links."""

    FOLLOW = "follow"  # Hash target file
    HASH_LINK = "hash_link"  # Hash link itself
    SKIP = "skip"  # Skip symlinks
    ERROR = "error"  # Raise error on symlinks


def handle_symlink(path: Path, policy: str = SymlinkPolicy.FOLLOW) -> Optional[str]:
    """Handle symbolic link according to policy.

    Args:
        path: Path that might be a symlink
        policy: How to handle symlinks

    Returns:
        Digest or None if skipped

    Raises:
        ValueError: If policy is ERROR and path is a symlink
    """
    if not path.is_symlink():
        return compute_file_digest(path)

    if policy == SymlinkPolicy.FOLLOW:
        # Follow symlink and hash target
        target = path.resolve()
        if not target.exists():
            return None  # Broken symlink
        return compute_file_digest(target)
    elif policy == SymlinkPolicy.HASH_LINK:
        # Hash the symlink itself (its target path)
        target_str = str(path.readlink())
        h = hashlib.sha256()
        h.update(target_str.encode("utf-8"))
        return f"sha256:{h.hexdigest()}"
    elif policy == SymlinkPolicy.SKIP:
        return None
    elif policy == SymlinkPolicy.ERROR:
        raise ValueError(f"Symlink not allowed: {path}")
    else:
        raise ValueError(f"Unknown symlink policy: {policy}")


# File size limits
MAX_AUTO_HASH_SIZE = 100 * 1024 * 1024  # 100MB


def should_hash_file(path: Path) -> Tuple[bool, Optional[str]]:
    """Check if file should be automatically hashed.

    Args:
        path: File to check

    Returns:
        Tuple of (should_hash, reason_if_not)
    """
    if not path.exists():
        return False, "File does not exist"

    if path.is_symlink():
        # Let symlink policy handle this
        return True, None

    size = path.stat().st_size
    if size > MAX_AUTO_HASH_SIZE:
        return False, f"File too large ({size:,} bytes, limit {MAX_AUTO_HASH_SIZE:,})"

    return True, None