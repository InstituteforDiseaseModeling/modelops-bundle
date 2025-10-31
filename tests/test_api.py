"""Test the stable API module used by external tools.

This ensures the API module is importable and functional, particularly
for modelops job submission which uses push_dir() for auto-push.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from modelops_bundle.context import ProjectContext
from modelops_bundle.core import TrackedFiles, SyncState
from modelops_bundle.ops import save_config, save_tracked, save_state, BundleConfig


def test_api_module_importable():
    """Test that the api module can be imported.

    This would have caught the missing api.py file issue where the module
    existed locally but wasn't committed to git, causing ImportError
    for users installing from GitHub.
    """
    # This import will fail if api.py doesn't exist
    from modelops_bundle import api

    # Verify the key function exists
    assert hasattr(api, 'push_dir')
    assert callable(api.push_dir)


def test_push_dir_function_signature():
    """Test that push_dir has the expected signature."""
    from modelops_bundle.api import push_dir
    import inspect

    sig = inspect.signature(push_dir)
    params = list(sig.parameters.keys())

    # Should have path and tag parameters
    assert 'path' in params
    assert 'tag' in params

    # path should default to "."
    assert sig.parameters['path'].default == "."

    # tag should be Optional (None default)
    assert sig.parameters['tag'].default is None


def test_push_dir_basic_flow(tmp_path, monkeypatch):
    """Test basic flow of push_dir without actual registry interaction."""
    from modelops_bundle.api import push_dir

    # Setup test project
    monkeypatch.chdir(tmp_path)

    # Initialize bundle project structure
    ctx = ProjectContext.init(tmp_path)

    # Create minimal config
    config = BundleConfig(
        registry_ref="localhost:5555/test-project",
        default_tag="latest"
    )
    save_config(config, ctx)

    # Create some tracked files
    test_file = tmp_path / "model.py"
    test_file.write_text("# test model")

    tracked = TrackedFiles()
    tracked.add(Path("model.py"))
    save_tracked(tracked, ctx)

    # Create empty sync state
    save_state(SyncState(), ctx)

    # Mock load_env_for_command to avoid credential setup
    with patch('modelops_bundle.env_manager.load_env_for_command'):
        # Mock the actual push operation - need to patch where it's imported
        with patch('modelops_bundle.api.ops_push') as mock_push:
            mock_push.return_value = "sha256:abc123"

            # Call the API function
            digest = push_dir(".")

    # Verify it returned a digest
    assert digest == "sha256:abc123"

    # Verify push was called with correct arguments
    mock_push.assert_called_once()
    call_args = mock_push.call_args

    # Check the config and tracked files were passed
    assert call_args[0][0].registry_ref == "localhost:5555/test-project"
    assert "model.py" in call_args[0][1].files


def test_push_dir_with_custom_tag(tmp_path, monkeypatch):
    """Test push_dir with custom tag."""
    from modelops_bundle.api import push_dir

    monkeypatch.chdir(tmp_path)
    ctx = ProjectContext.init(tmp_path)

    config = BundleConfig(registry_ref="localhost:5555/test-project")
    save_config(config, ctx)
    save_tracked(TrackedFiles(), ctx)
    save_state(SyncState(), ctx)

    with patch('modelops_bundle.env_manager.load_env_for_command'):
        with patch('modelops_bundle.api.ops_push') as mock_push:
            mock_push.return_value = "sha256:def456"
            # Call with custom tag
            digest = push_dir(".", tag="v1.0.0")

    # Verify tag was passed through
    assert mock_push.call_args.kwargs.get('tag') == "v1.0.0"


def test_push_dir_handles_missing_bundle_dir(tmp_path, monkeypatch):
    """Test push_dir raises appropriate error when not in bundle project."""
    from modelops_bundle.api import push_dir

    monkeypatch.chdir(tmp_path)

    # Don't initialize bundle project - just empty directory
    with pytest.raises(ValueError, match="Not inside a modelops-bundle project"):
        push_dir(".")


def test_api_used_by_modelops():
    """Verify the API matches what modelops expects.

    This documents the contract between modelops and modelops-bundle.
    """
    # This is what modelops/cli/jobs.py does:
    # from modelops_bundle.api import push_dir
    # digest = push_dir(".")

    from modelops_bundle.api import push_dir

    # The function should:
    # 1. Accept a path (default ".")
    # 2. Accept an optional tag
    # 3. Return a string digest
    # 4. Raise FileNotFoundError if not a bundle project

    # Check the docstring documents this usage
    assert "modelops job submission" in push_dir.__doc__
    assert "sha256:" in push_dir.__doc__
    assert "FileNotFoundError" in push_dir.__doc__