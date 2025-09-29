"""Centralized environment management for modelops-bundle.

This module handles all environment selection and credential loading,
implementing a pin-based system similar to .python-version or .nvmrc.
"""
import os
from pathlib import Path
from typing import Optional
from modelops_contracts import BundleEnvironment

ENV_PIN_FILE = "env"  # .modelops-bundle/env
DEFAULT_ENV = "dev"   # Default to dev environment
ENV_DIR = Path.home() / ".modelops" / "bundle-env"  # ~/.modelops/bundle-env/

def pin_env(project_storage_dir: Path, name: str) -> None:
    """Pin the environment for this project.

    Args:
        project_storage_dir: The .modelops-bundle directory
        name: Environment name to pin (e.g., 'local', 'dev')
    """
    (project_storage_dir / ENV_PIN_FILE).write_text(name.strip() + "\n")

def read_pinned_env(project_storage_dir: Path) -> str:
    """Read the pinned environment name.

    Args:
        project_storage_dir: The .modelops-bundle directory

    Returns:
        The pinned environment name

    Raises:
        FileNotFoundError: If no environment is pinned
    """
    p = project_storage_dir / ENV_PIN_FILE
    if not p.exists():
        raise FileNotFoundError(f"No environment pinned in {project_storage_dir}")
    return p.read_text().strip()

def load_env_for_command(
    project_storage_dir: Path,
    cli_env: Optional[str] = None,
    require_storage: bool = False
) -> Optional[BundleEnvironment]:
    """Load environment for the current command.

    This is the main entry point for commands that need environment configuration.
    It handles the resolution order: CLI override > pinned env > error.

    Args:
        project_storage_dir: The .modelops-bundle directory
        cli_env: Override from CLI (e.g., --env flag on init)
        require_storage: Whether storage is required for this command

    Returns:
        BundleEnvironment or None if not required

    Raises:
        FileNotFoundError: If env is required but not found
    """
    # CLI override takes precedence, otherwise use pinned or default
    if cli_env:
        env_name = cli_env
    else:
        try:
            env_name = read_pinned_env(project_storage_dir)
        except FileNotFoundError:
            # No pin file - use default
            env_name = DEFAULT_ENV

    # Load the environment file
    try:
        env = BundleEnvironment.load(env_name)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Environment '{env_name}' not found in ~/.modelops/bundle-env/\n"
            f"Available environments are created by:\n"
            f"  - 'mops infra up' (creates 'dev')\n"
            f"  - 'make start' (creates 'local')"
        )

    # Validate storage if required
    if require_storage and not env.storage:
        raise ValueError(f"Environment '{env_name}' has no storage configured")

    # Apply credentials to process environment
    _apply_env_credentials(env)
    return env

def _apply_env_credentials(env: BundleEnvironment) -> None:
    """Apply environment credentials to process env vars.

    This sets up the necessary environment variables for storage access
    based on the provider type.

    Args:
        env: The loaded BundleEnvironment
    """
    setup_storage_credentials(env)


def setup_storage_credentials(env: BundleEnvironment) -> None:
    """Set up storage credentials in environment variables.

    This is a public function for setting up storage credentials
    from a BundleEnvironment. Used by tests and can be called directly.

    Args:
        env: The BundleEnvironment with storage configuration
    """
    if env.storage:
        s = env.storage
        # Azure/Azurite storage
        if s.provider in ("azure", "azurite") and s.connection_string:
            os.environ["AZURE_STORAGE_CONNECTION_STRING"] = s.connection_string
        # Future: Add AWS, GCP credential handling here