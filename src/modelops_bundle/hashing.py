"""Token-based hashing utilities for deterministic builds.

This module provides whitespace-agnostic hashing of Python files
using tokenization to ignore formatting changes while preserving
semantic content. This ensures that reformatting code or changing
comments doesn't invalidate cached results.

The hashing functions here are used to create deterministic bundle
digests for provenance tracking.
"""

import io
import json
import tokenize
from pathlib import Path
from typing import Iterable, Tuple, Any
import hashlib

try:
    from modelops_contracts import digest_bytes
except ImportError:
    # Fallback for when contracts isn't available
    def digest_bytes(data: bytes) -> str:
        """Compute BLAKE2b-256 hash of bytes."""
        return hashlib.blake2b(data, digest_size=32).hexdigest()


# Tokens to skip when hashing (formatting-only)
SKIP_TOKENS = {
    tokenize.COMMENT,     # Comments don't affect behavior
    tokenize.NL,          # Newlines within statements
    tokenize.NEWLINE,     # Statement-ending newlines
    tokenize.INDENT,      # Indentation changes
    tokenize.DEDENT,      # Dedentation changes
    tokenize.ENCODING,    # Encoding declaration
}


def token_hash(path: Path) -> str:
    """Hash Python file based on tokens only, ignoring formatting.

    This provides deterministic hashing that ignores:
    - Whitespace changes
    - Comment changes
    - Indentation style (tabs vs spaces)
    - Blank lines

    Args:
        path: Path to Python file to hash

    Returns:
        64-character hex hash string

    Example:
        >>> path = Path("model.py")
        >>> hash1 = token_hash(path)
        >>> # Reformat file with black
        >>> hash2 = token_hash(path)
        >>> assert hash1 == hash2  # Same despite formatting
    """
    try:
        src = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fall back to binary hash if not valid UTF-8
        return file_hash(path)

    tokens = []
    try:
        # Tokenize the source code
        for token in tokenize.generate_tokens(io.StringIO(src).readline):
            # Skip formatting-only tokens
            if token.type in SKIP_TOKENS:
                continue

            # Keep token type and string, discard position info
            # This makes the hash position-independent
            tokens.append((token.type, token.string))

    except tokenize.TokenError:
        # Fall back to source hash if tokenization fails
        return digest_bytes(src.encode('utf-8'))

    # Create deterministic JSON representation
    payload = canonical_json(tokens).encode('utf-8')
    return digest_bytes(payload)


def file_hash(path: Path) -> str:
    """Hash file contents with SHA256.

    ANY byte change produces a different hash.
    Used for data files where every bit matters.

    Args:
        path: Path to file

    Returns:
        64-character hex hash string
    """
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def code_sig(file_records: Iterable[Tuple[str, str]]) -> str:
    """Create signature from multiple file hashes.

    Combines multiple file hashes into a single deterministic signature
    that represents the state of a code collection.

    Args:
        file_records: Iterable of (file_path, file_hash) pairs

    Returns:
        64-character hex hash string

    Example:
        >>> records = [
        ...     ("src/model.py", "abc123..."),
        ...     ("src/utils.py", "def456..."),
        ... ]
        >>> sig = code_sig(records)
    """
    # Sort by path for deterministic ordering
    sorted_records = sorted(file_records)

    # Combine path and hash for each file with domain separation
    parts = []
    for path, hash_val in sorted_records:
        parts.append(f"{path}::{hash_val}")

    combined = "|".join(parts)

    # Hash the combination with namespace
    namespaced = f"bundle:code_sig:v1|{combined}"
    return digest_bytes(namespaced.encode('utf-8'))


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization.

    Always produces the same string for the same object by:
    - Sorting dictionary keys
    - Using compact separators
    - Ensuring stable output

    Args:
        obj: Object to serialize

    Returns:
        Canonical JSON string
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )


def compute_composite_digest(
    components: list[tuple[str, str, str]],
    env_digest: str
) -> str:
    """Compute composite digest with proper domain separation.

    This creates a single digest from all bundle components using
    cryptographic domain separation to prevent ambiguity.

    Args:
        components: List of (type, path, digest) tuples where:
            - type: "MODEL_CODE", "DATA", "CODE_DEP"
            - path: Relative file path
            - digest: File content hash
        env_digest: Environment digest string

    Returns:
        64-character hex bundle digest

    Example:
        >>> components = [
        ...     ("MODEL_CODE", "src/model.py", "abc123..."),
        ...     ("DATA", "data/pop.csv", "def456..."),
        ... ]
        >>> bundle_digest = compute_composite_digest(components, env_digest)
    """
    # Sort for deterministic ordering
    sorted_components = sorted(components)

    # Build digest with domain separation
    h = hashlib.blake2b(digest_size=32)

    # Include environment digest first
    h.update(b'\x00ENV\x00')
    h.update(env_digest.encode('utf-8'))
    h.update(b'\x00')

    # Add each component with proper separation
    for kind, path, digest in sorted_components:
        h.update(b'\x00')
        h.update(kind.encode('utf-8'))
        h.update(b'\x00')
        h.update(str(path).encode('utf-8'))
        h.update(b'\x00')
        h.update(digest.encode('utf-8'))
        h.update(b'\x00')

    return h.hexdigest()


__all__ = [
    "token_hash",
    "file_hash",
    "code_sig",
    "canonical_json",
    "compute_composite_digest",
    "SKIP_TOKENS",
]