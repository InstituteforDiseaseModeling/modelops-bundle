"""Custom exceptions for modelops-bundle.

This module defines typed exceptions for better error handling and clearer
error messages throughout the application.
"""


class BundleError(RuntimeError):
    """Base class for all bundle-related errors."""
    pass


# Registry Errors
class RegistryError(BundleError):
    """Base class for registry communication errors."""
    pass


class NetworkError(RegistryError):
    """Network connectivity issue with registry."""
    pass


class AuthError(RegistryError):
    """Authentication or authorization failed (401/403)."""
    pass


class NotFoundError(RegistryError):
    """Resource not found in registry (404)."""
    pass


class TagMovedError(RegistryError):
    """Tag moved during operation (race condition)."""
    
    def __init__(self, registry_ref: str, tag: str, expected: str, actual: str):
        self.registry_ref = registry_ref
        self.tag = tag
        self.expected = expected
        self.actual = actual
        actual_display = actual[:12] + "..." if actual else "(not found)"
        super().__init__(
            f"Tag '{tag}' moved during operation. "
            f"Expected: {expected[:12]}..., Got: {actual_display} "
            f"Your content is accessible at: {registry_ref}@{expected}"
        )


# Bundle Format Errors
class IncompatibleBundleError(BundleError):
    """Bundle format not compatible with this version."""
    pass


class MissingIndexError(IncompatibleBundleError):
    """BundleIndex required but not found."""
    
    def __init__(self, reference: str):
        super().__init__(
            f"Bundle at '{reference}' is missing required BundleIndex. "
            f"This artifact was not created by modelops-bundle or uses an incompatible version."
        )


class UnsupportedArtifactError(IncompatibleBundleError):
    """Artifact type not supported (e.g., manifest list/index)."""
    
    def __init__(self, reference: str, media_type: str):
        super().__init__(
            f"'{reference}' points to {media_type}, not a single artifact. "
            f"Multi-platform images are not yet supported."
        )


# Storage Errors
class StorageError(BundleError):
    """Base class for storage-related errors."""
    pass


class BlobStorageRequiredError(StorageError):
    """Blob storage needed but not configured."""
    
    def __init__(self, files: list):
        self.files = files
        file_list = ", ".join(files[:3])
        if len(files) > 3:
            file_list += f" and {len(files) - 3} more"
        super().__init__(
            f"External blob storage is required for large files: {file_list}. "
            f"Configure storage.provider (azure, s3, gcs, or fs) to enable blob storage."
        )


class BlobProviderMissingError(StorageError):
    """Blob provider required but not configured."""
    
    def __init__(self):
        super().__init__(
            "Bundle contains blob storage files but no blob provider is configured. "
            "Set storage.provider to pull this bundle."
        )


# Integrity Errors
class IntegrityError(BundleError):
    """Base class for data integrity errors."""
    pass


class DigestMismatchError(IntegrityError):
    """File digest doesn't match expected value."""
    
    def __init__(self, path: str, expected: str, actual: str):
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Digest verification failed for {path}\n"
            f"  Expected: {expected}\n"
            f"  Got:      {actual}\n"
            f"The file may be corrupted or tampered with."
        )


# Configuration Errors
class ConfigError(BundleError):
    """Base class for configuration errors."""
    pass


class InvalidStorageModeError(ConfigError):
    """Invalid storage mode configuration."""
    
    def __init__(self, mode: str, provider: str):
        super().__init__(
            f"Storage mode '{mode}' requires blob provider but none configured. "
            f"Either set storage.provider or use mode='oci-only'."
        )