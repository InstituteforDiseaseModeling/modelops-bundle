"""Test environment credential setup functionality with env_manager."""

import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from modelops_contracts import BundleEnvironment, RegistryConfig, StorageConfig
from modelops_bundle.context import ProjectContext
from modelops_bundle.env_manager import (
    pin_env,
    read_pinned_env,
    load_env_for_command,
    setup_storage_credentials
)


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project directory."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    (project_dir / ".modelops-bundle").mkdir()
    return project_dir


@pytest.fixture
def mock_environment():
    """Create a mock BundleEnvironment with storage config."""
    return BundleEnvironment(
        environment="dev",
        registry=RegistryConfig(
            provider="acr",
            login_server="test.registry.com",
            username="testuser",
            password="testpass"
        ),
        storage=StorageConfig(
            provider="azure",
            container="test-container",
            connection_string="DefaultEndpointsProtocol=https;AccountName=testaccount;AccountKey=testkey;EndpointSuffix=core.windows.net"
        )
    )


class TestEnvManagerPinning:
    """Test env_manager pin functionality."""

    def test_pin_and_read_env(self, temp_project):
        """Test pinning and reading environment."""
        storage_dir = temp_project / ".modelops-bundle"

        # Pin an environment
        pin_env(storage_dir, "dev")

        # Read it back
        env_name = read_pinned_env(storage_dir)
        assert env_name == "dev"

        # Check file exists and contents
        env_file = storage_dir / "env"
        assert env_file.exists()
        assert env_file.read_text().strip() == "dev"

    def test_pin_overwrites_existing(self, temp_project):
        """Test that pinning overwrites existing environment."""
        storage_dir = temp_project / ".modelops-bundle"

        # Pin first environment
        pin_env(storage_dir, "dev")
        assert read_pinned_env(storage_dir) == "dev"

        # Pin different environment
        pin_env(storage_dir, "prod")
        assert read_pinned_env(storage_dir) == "prod"

    def test_read_missing_pin_raises(self, temp_project):
        """Test that reading missing pin file raises FileNotFoundError."""
        storage_dir = temp_project / ".modelops-bundle"

        with pytest.raises(FileNotFoundError):
            read_pinned_env(storage_dir)


class TestEnvManagerCredentials:
    """Test that env_manager properly sets up storage credentials."""

    def test_setup_storage_credentials_azure(self, mock_environment):
        """Test setting up Azure storage credentials."""
        # Clear any existing env var
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        # Setup credentials
        setup_storage_credentials(mock_environment)

        # Check that Azure connection string was set
        assert os.environ.get("AZURE_STORAGE_CONNECTION_STRING") == mock_environment.storage.connection_string

    def test_setup_storage_credentials_azurite(self):
        """Test setting up Azurite storage credentials."""
        # Clear any existing env var
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        azurite_env = BundleEnvironment(
            environment="local",
            registry=RegistryConfig(
                provider="docker",
                login_server="localhost:5555",
                username="",
                password=""
            ),
            storage=StorageConfig(
                provider="azurite",
                container="test-container",
                connection_string="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=key1;BlobEndpoint=http://localhost:10000/devstoreaccount1;"
            )
        )

        # Setup credentials
        setup_storage_credentials(azurite_env)

        # Check that Azure connection string was set (Azurite uses same env var)
        assert os.environ.get("AZURE_STORAGE_CONNECTION_STRING") == azurite_env.storage.connection_string

    def test_setup_storage_credentials_no_connection_string(self):
        """Test that missing connection string doesn't set env var."""
        # Clear any existing env var
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        no_conn_env = BundleEnvironment(
            environment="dev",
            registry=RegistryConfig(
                provider="acr",
                login_server="test.registry.com",
                username="testuser",
                password="testpass"
            ),
            storage=StorageConfig(
                provider="azure",
                container="test-container",
                connection_string=None
            )
        )

        # Setup credentials (should not crash)
        setup_storage_credentials(no_conn_env)

        # Check that no connection string was set
        assert os.environ.get("AZURE_STORAGE_CONNECTION_STRING") is None

    def test_setup_storage_credentials_no_storage(self):
        """Test that missing storage config doesn't crash."""
        # Clear any existing env var
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        # Mock an environment without storage field
        no_storage_env = Mock(spec=BundleEnvironment)
        no_storage_env.storage = None
        no_storage_env.environment = "dev"

        # Setup credentials (should not crash)
        setup_storage_credentials(no_storage_env)

        # Check that no connection string was set
        assert os.environ.get("AZURE_STORAGE_CONNECTION_STRING") is None


class TestEnvManagerIntegration:
    """Test env_manager integration with commands."""

    def test_load_env_for_command_sets_credentials(self, temp_project, mock_environment):
        """Test that load_env_for_command sets up storage credentials."""
        # Clear any existing env var
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        # Pin environment
        storage_dir = temp_project / ".modelops-bundle"
        pin_env(storage_dir, "test")

        with patch.object(BundleEnvironment, 'load', return_value=mock_environment):
            # Load environment for command (like push/pull would)
            env = load_env_for_command(storage_dir, require_storage=True)

            # Should have loaded environment
            assert env == mock_environment

            # Should have set up credentials
            assert os.environ.get("AZURE_STORAGE_CONNECTION_STRING") == mock_environment.storage.connection_string

    def test_load_env_for_command_without_pin_uses_default(self, temp_project):
        """Test that missing pin file uses default environment."""
        # Clear any existing env var
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        storage_dir = temp_project / ".modelops-bundle"

        # Mock the default environment load (without storage)
        dev_env = Mock(spec=BundleEnvironment)
        dev_env.environment = "dev"
        dev_env.storage = None
        dev_env.registry = Mock(spec=RegistryConfig)

        with patch.object(BundleEnvironment, 'load', return_value=dev_env) as mock_load:
            # Load environment (should use default "dev")
            env = load_env_for_command(storage_dir)

            # Should have loaded "dev" environment
            mock_load.assert_called_once_with("dev")
            assert env == dev_env

    def test_load_env_for_command_require_storage_validates(self, temp_project):
        """Test that require_storage validates storage configuration."""
        storage_dir = temp_project / ".modelops-bundle"
        pin_env(storage_dir, "test")

        # Mock environment without storage
        no_storage_env = Mock(spec=BundleEnvironment)
        no_storage_env.environment = "test"
        no_storage_env.storage = None
        no_storage_env.registry = Mock(spec=RegistryConfig)

        with patch.object(BundleEnvironment, 'load', return_value=no_storage_env):
            # Should raise when storage is required but missing
            with pytest.raises(ValueError, match="Environment 'test' has no storage configured"):
                load_env_for_command(storage_dir, require_storage=True)

            # Should work when storage is not required
            env = load_env_for_command(storage_dir, require_storage=False)
            assert env == no_storage_env


class TestBundleServiceWithEnvManager:
    """Test that BundleService works with env_manager system."""

    def test_bundle_service_init_with_pinned_env(self, temp_project, mock_environment, monkeypatch):
        """Test BundleService initialization with pinned environment."""
        from modelops_bundle.bundle_service import BundleService
        from modelops_bundle.core import BundleConfig
        from modelops_bundle.ops import save_config

        # Change to temp project directory
        monkeypatch.chdir(temp_project)

        # Setup project with pinned environment
        ctx = ProjectContext(temp_project)
        pin_env(ctx.storage_dir, "test")

        # Save a config
        config = BundleConfig(
            registry_ref="test.registry.com/repo",
            default_tag="latest"
        )
        save_config(config, ctx)

        with patch.object(BundleEnvironment, 'load', return_value=mock_environment):
            # Initialize service (should load pinned environment for auth)
            service = BundleService()

            # Service should have loaded config with dynamically resolved registry
            # (registry_ref is now built from environment's login_server + project name)
            assert service.config.registry_ref == "test.registry.com/test_project"

    def test_bundle_service_operations_load_credentials(self, temp_project, mock_environment, monkeypatch):
        """Test that service operations can access storage credentials."""
        from modelops_bundle.bundle_service import BundleService
        from modelops_bundle.core import BundleConfig, TrackedFiles
        from modelops_bundle.ops import save_config, save_tracked

        # Clear any existing env var
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        # Change to temp project directory
        monkeypatch.chdir(temp_project)

        # Setup project
        ctx = ProjectContext(temp_project)
        pin_env(ctx.storage_dir, "test")

        config = BundleConfig(
            registry_ref="test.registry.com/repo",
            default_tag="latest"
        )
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)

        # For operations that need storage, credentials should be available
        # This would be set by the CLI commands that call load_env_for_command
        with patch.object(BundleEnvironment, 'load', return_value=mock_environment):
            # Simulate what CLI does before calling service
            load_env_for_command(ctx.storage_dir, require_storage=True)

            # Now credentials should be available for service operations
            assert os.environ.get("AZURE_STORAGE_CONNECTION_STRING") == mock_environment.storage.connection_string

            # Service can be created and would have access to credentials
            service = BundleService()
            # Registry ref is now dynamically resolved from environment
            assert service.config.registry_ref == "test.registry.com/test_project"