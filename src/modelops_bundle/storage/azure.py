"""Azure blob storage implementation."""

import urllib.parse
from pathlib import Path
from typing import Tuple

from ..storage_models import BlobReference


class AzureBlobStore:
    """
    Azure Blob Storage implementation.
    
    Files are stored with sharding: prefix/ab/cd/<full_sha256>
    """
    
    def __init__(self, connection_string: str, container: str, prefix: str = ""):
        """
        Initialize Azure blob store.
        
        Args:
            connection_string: Azure Storage connection string
            container: Container name
            prefix: Optional key prefix
        """
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            raise ImportError(
                "azure-storage-blob required for Azure blob storage. "
                "Install with: pip install azure-storage-blob"
            )
        
        self.client = BlobServiceClient.from_connection_string(connection_string)
        self.container = container
        self.prefix = prefix.rstrip("/") if prefix else ""
        
        # Ensure container exists
        container_client = self.client.get_container_client(container)
        if not container_client.exists():
            container_client.create_container()
    
    def put(self, digest: str, path: Path) -> BlobReference:
        """
        Upload file to Azure blob storage with sharding.
        
        Args:
            digest: Content digest (sha256:...)
            path: Source file path
            
        Returns:
            BlobReference with azure:// URI
        """
        clean_digest = digest.replace("sha256:", "")
        
        # Build key with sharding: prefix/ab/cd/full_sha256
        key_parts = []
        if self.prefix:
            key_parts.append(self.prefix)
        key_parts.extend([clean_digest[:2], clean_digest[2:4], clean_digest])
        key = "/".join(key_parts)
        
        # Check if already exists (idempotent)
        blob_client = self.client.get_blob_client(
            container=self.container,
            blob=key
        )
        
        if blob_client.exists():
            props = blob_client.get_blob_properties()
            return BlobReference(
                uri=f"azure://{self.container}/{key}",
                etag=props.get("etag")
            )
        
        # Upload file
        with open(path, "rb") as f:
            blob_client.upload_blob(f, overwrite=False)
        
        # Get etag from uploaded blob
        props = blob_client.get_blob_properties()
        return BlobReference(
            uri=f"azure://{self.container}/{key}",
            etag=props.get("etag")
        )
    
    def get(self, ref: BlobReference, dest: Path) -> None:
        """
        Download file from Azure blob storage.
        
        Args:
            ref: Blob reference with azure:// URI
            dest: Destination file path
        """
        container, key = self._parse_uri(ref.uri)
        
        # Verify container matches
        if container != self.container:
            raise ValueError(
                f"Container mismatch: expected {self.container}, got {container}"
            )
        
        blob_client = self.client.get_blob_client(
            container=container,
            blob=key
        )
        
        if not blob_client.exists():
            raise FileNotFoundError(f"Blob not found: {ref.uri}")
        
        # Download to destination
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            blob_data = blob_client.download_blob()
            blob_data.readinto(f)
    
    def exists(self, ref: BlobReference) -> bool:
        """
        Check if blob exists in Azure storage.
        
        Args:
            ref: Blob reference to check
            
        Returns:
            True if blob exists
        """
        try:
            container, key = self._parse_uri(ref.uri)
            if container != self.container:
                return False
            
            blob_client = self.client.get_blob_client(
                container=container,
                blob=key
            )
            return blob_client.exists()
        except (ValueError, Exception):
            return False
    
    def _parse_uri(self, uri: str) -> Tuple[str, str]:
        """
        Parse azure://container/key format robustly.
        
        Args:
            uri: Azure blob URI
            
        Returns:
            Tuple of (container, key)
            
        Raises:
            ValueError: If not an azure:// URI
        """
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme != "azure":
            raise ValueError(f"Expected azure:// URI, got {uri}")
        
        container = parsed.netloc
        key = parsed.path.lstrip("/")
        
        if not container or not key:
            raise ValueError(f"Invalid azure:// URI: {uri}")
        
        return container, key