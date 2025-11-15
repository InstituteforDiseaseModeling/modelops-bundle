"""Stable API for modelops-bundle operations.

This module provides a minimal, stable API surface for external tools
(particularly modelops job submission) to interact with bundle operations
without depending on internal implementation details.

The primary consumer is `mops jobs submit --auto` which needs to push
bundles programmatically without requiring users to manually manage
bundle references.

Keeping this API minimal and stable allows modelops to optionally depend
on modelops-bundle without tight coupling to internal implementation details.
"""

from pathlib import Path
from typing import Optional

from .context import ProjectContext
from .env_manager import load_env_for_command
from .ops import load_config, load_tracked, push as ops_push


def push_dir(path: str = ".", tag: Optional[str] = None) -> str:
    """Push directory as bundle and return manifest digest.

    This is the primary API for modelops job submission to auto-push
    bundles before submitting jobs to the cluster.

    Args:
        path: Directory containing .modelops-bundle (defaults to current dir)
        tag: Optional tag to apply (defaults to 'latest' from config)

    Returns:
        Manifest digest in format 'sha256:...'

    Raises:
        FileNotFoundError: If path doesn't contain .modelops-bundle
        RuntimeError: If preflight validation fails with blocking errors
        Various registry/network errors on push failure

    Example:
        >>> # From modelops CLI when user runs: mops jobs submit --auto
        >>> from modelops_bundle.api import push_dir
        >>> digest = push_dir(".")  # Push current directory
        >>> print(f"Pushed bundle: {digest}")
        sha256:abc123...
    """
    ctx = ProjectContext(Path(path))

    # Run preflight validation (same check as CLI push command)
    from modelops_contracts import BundleRegistry
    from .preflight import PreflightValidator

    registry_path = ctx.storage_dir / "registry.yaml"
    if not registry_path.exists():
        raise FileNotFoundError(
            f"No registry found at {registry_path}. "
            "Register models with: mops-bundle register-model <path>"
        )

    registry = BundleRegistry.load(registry_path)
    validator = PreflightValidator(ctx, registry)
    result = validator.validate_all()

    if result.has_blocking_errors:
        error_messages = [issue.message for issue in result.errors]
        raise RuntimeError(
            f"Preflight validation failed with blocking errors:\n" + "\n".join(f"  • {msg}" for msg in error_messages)
        )

    # Load bundle configuration and tracked files
    config = load_config(ctx)
    tracked = load_tracked(ctx)

    # Load environment for storage credentials
    load_env_for_command(ctx.storage_dir, require_storage=True)

    # Push and return the digest
    return ops_push(config, tracked, tag=tag, ctx=ctx)