"""Test that registry is correctly resolved from pinned environment.

This test ensures that when switching environments via 'dev switch',
the registry URL is dynamically resolved from the new environment,
not stuck on the value baked into config.yaml at init time.
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, Mock
from modelops_contracts import BundleEnvironment, RegistryConfig, StorageConfig
from modelops_bundle.context import ProjectContext
from modelops_bundle.env_manager import pin_env, read_pinned_env
from modelops_bundle.ops import load_config, save_config
from modelops_bundle.core import BundleConfig, StoragePolicy


@pytest.fixture
def mock_environments():
    """Create mock environments for local and dev."""
    local_env = BundleEnvironment(
        environment="local",
        registry=RegistryConfig(
            provider="docker",
            login_server="localhost:5555",
            requires_auth=False
        ),
        storage=StorageConfig(
            provider="azure",
            container="local-container",
            connection_string="local-connection"
        )
    )

    dev_env = BundleEnvironment(
        environment="dev",
        registry=RegistryConfig(
            provider="acr",
            login_server="modelopsdevacr.azurecr.io",
            requires_auth=True
        ),
        storage=StorageConfig(
            provider="azure",
            container="dev-container",
            connection_string="dev-connection"
        )
    )

    return {"local": local_env, "dev": dev_env}


class TestRegistrySwitching:
    """Test that registry URL is dynamically resolved from pinned environment."""

    def test_registry_updates_on_environment_switch(self, tmp_path, mock_environments):
        """Test that switching environments updates the registry URL."""
        # Setup project
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()
        storage_dir = project_dir / ".modelops-bundle"
        storage_dir.mkdir()

        # Create initial config with local registry
        initial_config = BundleConfig(
            registry_ref="localhost:5555/test_project",
            default_tag="latest",
            storage=StoragePolicy()
        )

        # Save config and pin to local
        ctx = ProjectContext(start_path=project_dir)
        save_config(initial_config, ctx)
        pin_env(storage_dir, "local")

        # Mock BundleEnvironment.load to return our test environments
        def mock_load(env_name):
            if env_name in mock_environments:
                return mock_environments[env_name]
            raise FileNotFoundError(f"Environment {env_name} not found")

        with patch.object(BundleEnvironment, 'load', side_effect=mock_load):
            # Load config - should still show local registry
            config = load_config(ctx)
            assert config.registry_ref == "localhost:5555/test_project", \
                "Initial config should use local registry"

            # Switch to dev environment
            pin_env(storage_dir, "dev")

            # Load config again - should now show dev registry
            config = load_config(ctx)
            assert config.registry_ref == "modelopsdevacr.azurecr.io/test_project", \
                f"After switching to dev, registry should be dev registry, but got {config.registry_ref}"

            # Verify the config file itself wasn't modified (still has original)
            with open(ctx.config_path) as f:
                saved_data = yaml.safe_load(f)
            assert saved_data["registry_ref"] == "localhost:5555/test_project", \
                "Config file should not be modified, only runtime resolution"

    def test_registry_fallback_when_no_pin_file(self, tmp_path):
        """Test that registry falls back to config.yaml when no pin file exists."""
        # Setup project without pin file
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()
        storage_dir = project_dir / ".modelops-bundle"
        storage_dir.mkdir()

        # Create config
        config = BundleConfig(
            registry_ref="fallback.registry.io/test_project",
            default_tag="latest",
            storage=StoragePolicy()
        )

        ctx = ProjectContext(start_path=project_dir)
        save_config(config, ctx)

        # Load config without pin file - should use value from config.yaml
        loaded = load_config(ctx)
        assert loaded.registry_ref == "fallback.registry.io/test_project", \
            "Should use registry from config.yaml when no pin file exists"

    def test_registry_with_missing_environment_file(self, tmp_path):
        """Test graceful handling when environment file doesn't exist."""
        # Setup project
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()
        storage_dir = project_dir / ".modelops-bundle"
        storage_dir.mkdir()

        # Create config and pin to non-existent environment
        config = BundleConfig(
            registry_ref="original.registry.io/test_project",
            default_tag="latest",
            storage=StoragePolicy()
        )

        ctx = ProjectContext(start_path=project_dir)
        save_config(config, ctx)
        pin_env(storage_dir, "nonexistent")

        # Load config - should fall back to config.yaml value
        with patch.object(BundleEnvironment, 'load', side_effect=FileNotFoundError("Not found")):
            loaded = load_config(ctx)
            assert loaded.registry_ref == "original.registry.io/test_project", \
                "Should fall back to config.yaml when environment file missing"