"""Storage-related data models for external blob storage support.

This module contains data models for the BundleIndex (stored as OCI manifest config)
and related storage types for hybrid OCI/blob storage patterns.
"""

import json
import urllib.parse
from enum import Enum
from pathlib import Path
from typing import Dict

from pydantic import BaseModel, Field, field_validator


class StorageType(str, Enum):
    """Storage location for a file."""
    OCI = "oci"    # Stored as OCI layer in registry
    BLOB = "blob"  # Stored in external blob storage


def canonicalize_uri(uri: str) -> str:
    """
    Canonicalize blob URI:
    - No query/fragment (forbids SAS tokens in index)
    - No double slashes
    - Proper percent encoding
    - Format: <provider>://<container>/<key>
    """
    parsed = urllib.parse.urlparse(uri)
    if parsed.query or parsed.fragment:
        raise ValueError("URI cannot have query or fragment (no SAS tokens in index)")
    
    # Remove double slashes, encode spaces
    path = urllib.parse.quote(parsed.path.replace("//", "/"))
    return f"{parsed.scheme}://{parsed.netloc}{path}"


class BlobReference(BaseModel):
    """Reference to a blob in external storage."""
    uri: str                       # "azure://container/prefix/ab/cd/<sha256>"
    etag: str | None = None       # Provider-specific version tag
    
    @field_validator('uri')
    @classmethod
    def validate_uri(cls, v: str) -> str:
        """Validate and canonicalize the URI."""
        return canonicalize_uri(v)


class BundleFileEntry(BaseModel):
    """Entry for a single file in the bundle index."""
    path: str                              # Relative file path
    digest: str                            # sha256:...
    size: int                              # File size in bytes
    storage: StorageType                   # Where the file is stored
    mediaType: str | None = None          # Optional media type
    blobRef: BlobReference | None = None  # Required when storage==BLOB
    
    def __init__(self, **data):
        """Validate blob reference requirement."""
        super().__init__(**data)
        if self.storage == StorageType.BLOB and not self.blobRef:
            raise ValueError(f"blobRef required for blob storage: {self.path}")


class BundleIndex(BaseModel):
    """
    Bundle index stored as OCI manifest config.
    
    This is the authoritative source of truth for all files in a bundle,
    including their storage locations (OCI layers vs external blob storage).
    """
    version: str = "1.0"
    created: str                                              # ISO 8601 timestamp
    tool: Dict[str, str] = Field(default_factory=dict)       # Tool metadata
    files: Dict[str, BundleFileEntry] = Field(default_factory=dict)  # Path -> Entry
    metadata: Dict[str, str] = Field(default_factory=dict)   # Optional metadata
    
    def to_json_deterministic(self, **kwargs) -> str:
        """
        Deterministic serialization for reproducible manifests.
        
        Pydantic v2 doesn't accept sort_keys in model_dump_json,
        so we use json.dumps with sort_keys=True.
        """
        return json.dumps(self.model_dump(), sort_keys=True, **kwargs)
