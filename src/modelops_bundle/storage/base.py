"""Base protocol for blob storage implementations."""

from pathlib import Path
from typing import Protocol

from ..storage_models import BlobReference


class BlobStore(Protocol):
    """
    Protocol for blob storage implementations.
    
    All implementations must provide put/get/exists operations.
    Digest verification is the caller's responsibility, not the store's.
    """
    
    def put(self, digest: str, path: Path) -> BlobReference:
        """
        Upload content to blob storage.
        
        Args:
            digest: Content digest (sha256:...)
            path: Local file path to upload
            
        Returns:
            BlobReference with canonical URI
        """
        ...
    
    def get(self, ref: BlobReference, dest: Path) -> None:
        """
        Download content from blob storage.
        
        Note: Digest verification is the caller's responsibility.
        
        Args:
            ref: Blob reference with URI
            dest: Local destination path
        """
        ...
    
    def exists(self, ref: BlobReference) -> bool:
        """
        Check if blob exists in storage.
        
        Used for idempotent put operations.
        
        Args:
            ref: Blob reference to check
            
        Returns:
            True if blob exists
        """
        ...