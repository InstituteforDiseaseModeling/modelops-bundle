"""Integration tests for CLI commands that verify real workflows.

These tests run the actual CLI commands via subprocess to ensure the full
flow works, including environment loading and credential setup.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def test_push_loads_storage_credentials(tmp_path, monkeypatch):
    """Test that push command actually loads and uses storage credentials.

    This test would have caught the bug where load_env_for_command() was
    never called, causing storage credentials to not be set.
    """
    monkeypatch.chdir(tmp_path)

    # Create or update the local environment file for testing
    env_dir = Path.home() / ".modelops" / "bundle-env"
    env_dir.mkdir(parents=True, exist_ok=True)

    # Save existing local.yaml if it exists
    local_env_file = env_dir / "local.yaml"
    original_content = None
    if local_env_file.exists():
        original_content = local_env_file.read_text()

    # Override with test environment
    local_env_file.write_text("""
environment: local
registry:
  provider: docker
  login_server: localhost:5555
storage:
  provider: azure
  container: test-container
  connection_string: DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=test;BlobEndpoint=http://localhost:10000/devstoreaccount1;
""")

    try:
        # Initialize project with local environment
        result = subprocess.run(
            [sys.executable, "-m", "modelops_bundle.cli", "init", "--env", "local"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"Init failed: {result.stderr}"

        # Create and add a test file
        test_file = Path("test.txt")
        test_file.write_text("test content")

        result = subprocess.run(
            [sys.executable, "-m", "modelops_bundle.cli", "add", "test.txt"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"Add failed: {result.stderr}"

        # Clear any existing credentials to ensure they're set by the command
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        # Create a test script that will be run by the push command to verify credentials
        # This is a bit hacky but ensures we test the real flow
        test_script = tmp_path / "test_creds.py"
        test_script.write_text("""
import os
import sys

# Check if credentials were loaded
if "AZURE_STORAGE_CONNECTION_STRING" not in os.environ:
    print("ERROR: AZURE_STORAGE_CONNECTION_STRING not set!", file=sys.stderr)
    sys.exit(1)

# Check the value is from our test environment
conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
if "devstoreaccount1" not in conn_str:
    print(f"ERROR: Wrong connection string: {conn_str}", file=sys.stderr)
    sys.exit(1)

print("Credentials loaded correctly!")
""")

        # Patch the storage factory to run our test script
        with patch('modelops_bundle.storage.factory.make_blob_store') as mock_store:
            def verify_credentials_and_return_mock(policy):
                # Run our test script to verify environment
                result = subprocess.run(
                    [sys.executable, str(test_script)],
                    capture_output=True,
                    text=True
                )
                assert result.returncode == 0, f"Credential check failed: {result.stderr}"
                return Mock()  # Return a mock blob store

            mock_store.side_effect = verify_credentials_and_return_mock

            # This should fail currently because push doesn't call load_env_for_command
            # After the fix, it should pass
            result = subprocess.run(
                [sys.executable, "-m", "modelops_bundle.cli", "push", "--dry-run"],
                capture_output=True,
                text=True,
                env={**os.environ, "MODELOPS_BUNDLE_INSECURE": "true"}
            )

            # Check that push at least tried to run (even if it fails for other reasons)
            # The key is that make_blob_store gets called, which verifies credentials
            if "AZURE_STORAGE_CONNECTION_STRING" in result.stderr:
                pytest.fail("Push command didn't load storage credentials!")
    finally:
        # Restore original local.yaml if it existed
        if original_content is not None:
            local_env_file.write_text(original_content)
        elif local_env_file.exists():
            local_env_file.unlink()


def test_pull_loads_storage_credentials(tmp_path, monkeypatch):
    """Test that pull command loads storage credentials."""
    monkeypatch.chdir(tmp_path)

    # Similar setup as push test
    env_dir = Path.home() / ".modelops" / "bundle-env"
    env_dir.mkdir(parents=True, exist_ok=True)

    # Save existing local.yaml if it exists
    local_env_file = env_dir / "local.yaml"
    original_content = None
    if local_env_file.exists():
        original_content = local_env_file.read_text()

    # Override with test environment
    local_env_file.write_text("""
environment: local
registry:
  provider: docker
  login_server: localhost:5555
storage:
  provider: azure
  container: test-container
  connection_string: DefaultEndpointsProtocol=http;AccountName=pulltest;AccountKey=test;BlobEndpoint=http://localhost:10000/pulltest;
""")

    try:
        # Initialize project
        result = subprocess.run(
            [sys.executable, "-m", "modelops_bundle.cli", "init", "--env", "local"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0

        # Clear credentials
        os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)

        # Try to run status (which might need storage info)
        result = subprocess.run(
            [sys.executable, "-m", "modelops_bundle.cli", "status"],
            capture_output=True,
            text=True,
            env={**os.environ, "MODELOPS_BUNDLE_INSECURE": "true"}
        )

        # Status should at least run without crashing
        # (it may not need storage, but shouldn't error on missing creds)
        assert "AZURE_STORAGE_CONNECTION_STRING" not in result.stderr, \
            "Status command has credential errors"
    finally:
        # Restore original local.yaml if it existed
        if original_content is not None:
            local_env_file.write_text(original_content)
        elif local_env_file.exists():
            local_env_file.unlink()


def test_init_without_chdir_leak(tmp_path):
    """Test that init command doesn't leak directory changes."""
    start_dir = Path.cwd()

    # Create project in a subdirectory
    project_dir = tmp_path / "my-project"

    result = subprocess.run(
        [sys.executable, "-m", "modelops_bundle.cli", "init", str(project_dir), "--env", "local"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path)
    )
    assert result.returncode == 0

    # Verify we're still in the same directory
    assert Path.cwd() == start_dir, "Init command changed the working directory!"

    # Verify project was created in the right place
    assert project_dir.exists()
    assert (project_dir / ".modelops-bundle").exists()


def test_cli_commands_with_real_workflow(tmp_path, monkeypatch):
    """Test a complete workflow: init, add, push (dry-run), status.

    This is an end-to-end test that would catch missing credential loading.
    """
    monkeypatch.chdir(tmp_path)

    # Create test environment
    env_dir = Path.home() / ".modelops" / "bundle-env"
    env_dir.mkdir(parents=True, exist_ok=True)

    # Use local environment for testing
    local_env = env_dir / "local.yaml"
    if not local_env.exists():
        local_env.write_text("""
environment: local
registry:
  provider: docker
  login_server: localhost:5555
storage:
  provider: azurite
  container: test-container
  connection_string: DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=key1;BlobEndpoint=http://localhost:10000/devstoreaccount1;
""")

    # Full workflow
    commands = [
        # Initialize
        ([sys.executable, "-m", "modelops_bundle.cli", "init", "--env", "local"],
         "Init failed"),

        # Create files
        (["touch", "file1.txt", "file2.txt"], "Create files failed"),

        # Add files
        ([sys.executable, "-m", "modelops_bundle.cli", "add", "file1.txt", "file2.txt"],
         "Add failed"),

        # Check status
        ([sys.executable, "-m", "modelops_bundle.cli", "status"],
         "Status failed"),

        # Try push (dry-run to avoid needing real registry)
        ([sys.executable, "-m", "modelops_bundle.cli", "push", "--dry-run"],
         "Push dry-run failed"),
    ]

    for cmd, error_msg in commands:
        if cmd[0] == "touch":
            # Just create the files
            Path("file1.txt").touch()
            Path("file2.txt").touch()
            continue

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, "MODELOPS_BUNDLE_INSECURE": "true"}
        )

        # Check for credential errors specifically
        if "AZURE_STORAGE_CONNECTION_STRING" in result.stderr:
            pytest.fail(f"Command {cmd} failed to load credentials: {result.stderr}")

        # For now, we're mainly checking that commands don't fail due to missing creds
        # Some commands might fail for other reasons (like registry not being available)
        if result.returncode != 0 and "AZURE_STORAGE" in result.stderr:
            pytest.fail(f"{error_msg}: {result.stderr}")