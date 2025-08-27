"""Storage policy for file classification in hybrid storage mode."""

import fnmatch
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

from .storage_models import StorageType


class StoragePolicy(BaseModel):
    """
    Policy for classifying files between OCI layers and external blob storage.
    
    Default mode is 'auto' (hybrid) which provides optimal balance:
    - Small files (< threshold) go to OCI layers for fast access
    - Large files go to blob storage for efficiency
    
    Patterns can override the threshold-based decision.
    """
    # Default: hybrid mode (auto) for optimal balance
    enabled: bool = True
    mode: str = "auto"              # "blob-only" | "oci-inline" | "auto" (default)
    threshold_bytes: int = 50 * 1024 * 1024  # 50MB default threshold
    force_blob_patterns: List[str] = Field(default_factory=list)  # Force to blob
    force_oci_patterns: List[str] = Field(default_factory=list)   # Force to OCI
    
    # Blob backend configuration (optional - None means OCI-only)
    provider: str = ""              # "azure" | "s3" | "gcs" | "fs" | "" (empty = OCI-only)
    container: str = ""             # Azure container name / S3 bucket
    prefix: str = ""                # Optional key prefix
    
    def classify(self, path: Path, size: int) -> StorageType:
        """
        Classify a file based on policy rules.
        
        Args:
            path: File path to classify
            size: File size in bytes
            
        Returns:
            StorageType.OCI or StorageType.BLOB
        """
        p = path.as_posix()
        
        if self.mode == "blob-only":
            return StorageType.BLOB
        if self.mode == "oci-inline":
            return StorageType.OCI
        
        # mode == "auto" (default): patterns override threshold
        for pattern in self.force_oci_patterns:
            if fnmatch.fnmatch(p, pattern):
                return StorageType.OCI
                
        for pattern in self.force_blob_patterns:
            if fnmatch.fnmatch(p, pattern):
                return StorageType.BLOB
                
        # Default to threshold-based decision
        return StorageType.BLOB if size >= self.threshold_bytes else StorageType.OCI