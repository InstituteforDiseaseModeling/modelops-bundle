"""Hashing utilities for deterministic bundle digests.

This module provides simple file hashing and composite digest computation
for provenance tracking in bundles.
"""

from pathlib import Path
from typing import List, Tuple
import hashlib


def compute_file_digest(path: Path) -> str:
    """Compute SHA256 hash of file contents.

    Simple byte-for-byte hashing - any change invalidates the digest.

    Args:
        path: Path to file to hash

    Returns:
        SHA256 digest in format "sha256:xxxx"
    """
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


def compute_composite_digest(
    components: List[Tuple[str, str, str]],
    env_digest: str
) -> str:
    """Compute composite digest from all bundle components.

    Uses cryptographic domain separation (null bytes) to prevent ambiguity
    between concatenated strings. For example, without separators, we couldn't
    distinguish between ("AB", "C") and ("A", "BC") - both would concatenate
    to "ABC". With domain separation, they produce different hashes.

    Args:
        components: List of (type, path, digest) tuples where:
            - type: Component type like "MODEL", "DATA", "CODE_DEP"
            - path: Relative file path
            - digest: File content hash (SHA256)
        env_digest: Environment digest string

    Returns:
        64-character hex bundle digest (BLAKE2b)

    Example:
        >>> components = [
        ...     ("MODEL", "src/model.py", "abc123..."),
        ...     ("DATA", "data/pop.csv", "def456..."),
        ... ]
        >>> bundle_digest = compute_composite_digest(components, "env123")
    """
    # Sort for deterministic ordering
    sorted_components = sorted(components)

    # Use BLAKE2b for the composite digest
    h = hashlib.blake2b(digest_size=32)

    # Environment section with domain separator
    # The null bytes \x00 act as unambiguous delimiters
    h.update(b'\x00ENV\x00')
    h.update(env_digest.encode('utf-8'))
    h.update(b'\x00')

    # Add each component with domain separation
    for component_type, path, digest in sorted_components:
        h.update(b'\x00')  # Start delimiter
        h.update(component_type.encode('utf-8'))
        h.update(b'\x00')  # Separator
        h.update(str(path).encode('utf-8'))
        h.update(b'\x00')  # Separator
        h.update(digest.encode('utf-8'))
        h.update(b'\x00')  # End delimiter

    return h.hexdigest()


# Backwards compatibility aliases
file_hash = compute_file_digest


__all__ = [
    "compute_file_digest",
    "compute_composite_digest",
    "file_hash",  # Backwards compatibility
]