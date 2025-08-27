"""Filesystem blob storage implementation for testing."""

import shutil
from pathlib import Path

from ..storage_models import BlobReference


class FilesystemBlobStore:
    """
    Local filesystem store for unit tests (avoids Azurite dependency).
    
    Files are stored with sharding: base_dir/ab/cd/<full_sha256>
    """
    
    def __init__(self, base_dir: Path):
        """
        Initialize filesystem store.
        
        Args:
            base_dir: Base directory for blob storage
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def put(self, digest: str, path: Path) -> BlobReference:
        """
        Copy file to filesystem store with sharding.
        
        Args:
            digest: Content digest (sha256:...)
            path: Source file path
            
        Returns:
            BlobReference with fs:// URI
        """
        # Shard: ab/cd/full_sha256
        clean_digest = digest.replace("sha256:", "")
        dest = self.base_dir / clean_digest[:2] / clean_digest[2:4] / clean_digest
        
        # Check if already exists (idempotent)
        if dest.exists():
            return BlobReference(uri=f"fs://{dest.absolute()}")
        
        # Create parent directories and copy
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        
        return BlobReference(uri=f"fs://{dest.absolute()}")
    
    def get(self, ref: BlobReference, dest: Path) -> None:
        """
        Copy file from filesystem store.
        
        Args:
            ref: Blob reference with fs:// URI
            dest: Destination file path
        """
        src = self._parse_uri(ref.uri)
        if not src.exists():
            raise FileNotFoundError(f"Blob not found: {ref.uri}")
        
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    
    def exists(self, ref: BlobReference) -> bool:
        """
        Check if file exists in filesystem store.
        
        Args:
            ref: Blob reference to check
            
        Returns:
            True if file exists
        """
        try:
            path = self._parse_uri(ref.uri)
            return path.exists()
        except ValueError:
            return False
    
    def _parse_uri(self, uri: str) -> Path:
        """
        Parse fs:// URI to get file path.
        
        Args:
            uri: Filesystem URI (fs://...)
            
        Returns:
            Absolute file path
            
        Raises:
            ValueError: If not a fs:// URI
        """
        if not uri.startswith("fs://"):
            raise ValueError(f"Expected fs:// URI, got {uri}")
        return Path(uri[5:])