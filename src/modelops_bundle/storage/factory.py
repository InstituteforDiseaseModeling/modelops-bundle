"""Factory for creating blob storage instances."""

import os
from pathlib import Path
from typing import Optional

from ..policy import StoragePolicy
from .azure import AzureBlobStore
from .base import BlobStore
from .fs import FilesystemBlobStore


def validate_azure_config(policy: StoragePolicy) -> None:
    """
    Early validation of Azure configuration.
    
    Args:
        policy: Storage policy to validate
        
    Raises:
        ValueError: If configuration is invalid
    """
    if not policy.container:
        raise ValueError("storage.container required for Azure blob storage")
    
    if "AZURE_STORAGE_CONNECTION_STRING" not in os.environ:
        raise ValueError(
            "Set AZURE_STORAGE_CONNECTION_STRING and storage.container "
            "for Azure blob storage"
        )


def make_blob_store(policy: StoragePolicy) -> Optional[BlobStore]:
    """
    Create blob store instance based on policy.
    
    Args:
        policy: Storage policy configuration
        
    Returns:
        BlobStore instance or None for oci-inline mode or no provider
        
    Raises:
        ValueError: If configuration is invalid
        NotImplementedError: If provider is not supported
    """
    # No blob store needed for OCI-only modes
    if policy.mode == "oci-inline" or not policy.provider:
        return None
    
    if policy.provider == "azure":
        validate_azure_config(policy)
        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        return AzureBlobStore(conn_str, policy.container, policy.prefix)
    
    elif policy.provider == "fs":
        if not policy.container:
            raise ValueError("storage.container (directory path) required for filesystem storage")
        return FilesystemBlobStore(Path(policy.container))
    
    else:
        raise NotImplementedError(f"Provider {policy.provider} not supported")