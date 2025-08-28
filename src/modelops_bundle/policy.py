"""Storage policy for file classification in hybrid storage mode."""

import fnmatch
from pathlib import Path
from typing import List, Tuple

from pydantic import BaseModel, Field, model_validator

from .storage_models import StorageType
from .errors import InvalidStorageModeError


class StoragePolicy(BaseModel):
    """
    Policy for determining WHERE files are stored (OCI vs blob).
    
    All bundles use BundleIndex. This policy only controls storage location.
    
    Modes:
    - "auto" (default): Use threshold and patterns to decide
    - "oci-only": Everything in OCI registry layers
    - "blob-only": Everything in external blob storage (requires provider)
    """
    mode: str = "auto"              # "blob-only" | "oci-only" | "auto"
    threshold_bytes: int = 50 * 1024 * 1024  # 50MB default threshold
    force_blob_patterns: List[str] = Field(default_factory=list)  # Force to blob
    force_oci_patterns: List[str] = Field(default_factory=list)   # Force to OCI
    
    # Blob backend configuration (empty = OCI-only)
    provider: str = ""              # "azure" | "s3" | "gcs" | "fs" | ""
    container: str = ""             # Container/bucket name or fs path
    prefix: str = ""                # Optional key prefix for organization
    
    @model_validator(mode='after')
    def validate_blob_only_mode(self):
        """Ensure blob-only mode has a provider configured."""
        if self.mode == "blob-only" and not self.provider:
            raise InvalidStorageModeError(self.mode, self.provider)
        return self
    
    @property
    def uses_blob_storage(self) -> bool:
        """Check if any blob storage is configured."""
        return bool(self.provider)
    
    def classify(self, path: Path, size: int) -> Tuple[StorageType, bool]:
        """
        Classify where to store a file.
        
        Args:
            path: File path to classify
            size: File size in bytes
            
        Returns:
            Tuple of (StorageType, should_warn)
            should_warn is True when file would go to blob but no provider configured
        """
        p = path.as_posix()
        should_warn = False
        
        # Mode overrides everything
        if self.mode == "oci-only":
            return StorageType.OCI, False
        
        if self.mode == "blob-only":
            # Validator ensures provider exists for blob-only
            return StorageType.BLOB, False
        
        # mode == "auto": check patterns then threshold
        
        # Explicit patterns override threshold
        for pattern in self.force_oci_patterns:
            if fnmatch.fnmatch(p, pattern):
                return StorageType.OCI, False
                
        for pattern in self.force_blob_patterns:
            if fnmatch.fnmatch(p, pattern):
                if not self.provider:
                    # Would use blob but can't - fall back to OCI with warning
                    should_warn = True
                    return StorageType.OCI, True
                return StorageType.BLOB, False
        
        # Threshold-based decision
        if size >= self.threshold_bytes:
            if not self.provider:
                # Large file but no blob storage - warn
                should_warn = True
                return StorageType.OCI, True
            return StorageType.BLOB, False
        
        return StorageType.OCI, False
    
    def check_files_for_blob_requirement(self, files: List[Tuple[Path, int]]) -> List[str]:
        """
        Check if any files require blob storage when no provider configured.
        
        Returns list of file paths that need blob storage.
        """
        if self.provider or self.mode == "oci-only":
            return []
        
        needs_blob = []
        for path, size in files:
            storage_type, should_warn = self.classify(path, size)
            if should_warn:
                needs_blob.append(str(path))
        
        return needs_blob