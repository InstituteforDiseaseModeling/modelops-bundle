"""Integration tests for preflight validation system with CLI commands."""

from pathlib import Path

import pytest
from rich.console import Console

from modelops_bundle.context import ProjectContext
from modelops_bundle.ops import save_config, save_tracked
from modelops_bundle.core import BundleConfig, TrackedFiles
from modelops_bundle.status_display import display_preflight_issues
from modelops_bundle.model_state import ModelStatusSnapshot
from modelops_bundle.preflight import PreflightValidator
from modelops_contracts import BundleRegistry, ModelEntry, TargetEntry
from datetime import datetime


def create_minimal_pyproject(tmp_path: Path) -> Path:
    """Create minimal pyproject.toml for tests.

    Args:
        tmp_path: Directory to create pyproject.toml in

    Returns:
        Path to created pyproject.toml
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]
name = "test-bundle"
version = "0.1.0"
dependencies = ["modelops-calabaria"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")
    return pyproject


class TestPreflightCLIIntegration:
    """Test preflight validation in the context of CLI commands."""

    def test_status_command_displays_errors(self, tmp_path, monkeypatch, capsys):
        """Test that status command displays preflight errors."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create registry with output pairing error
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence"],  # Only has incidence
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:target_fn",
                    model_output="prevalence",  # ERROR: wants prevalence!
                    data=[],
                    target_digest=None,
                )
            },
        )

        # Save registry
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Create minimal status snapshot
        snapshot = ModelStatusSnapshot(
            timestamp=datetime.now(),
            models={},
            targets={},
            bundle_ref="localhost:5555/test",
            bundle_tag="dev",
            tracked_files=set(),
            cloud_manifest_digest=None,
            cloud_file_digests={},
            cloud_timestamp=None,
        )

        # Display preflight issues
        console = Console()
        display_preflight_issues(snapshot, console)

        # Verify output was rendered (actual Rich rendering tested elsewhere)
        # Just verify no exceptions were raised

    def test_status_command_displays_warnings(self, tmp_path, monkeypatch):
        """Test that status command displays preflight warnings."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create model file
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "__init__.py").write_text("")
        (models_dir / "test.py").write_text("class TestModel: pass")

        # Create pyproject.toml and track files
        create_minimal_pyproject(tmp_path)
        tracked = TrackedFiles()
        tracked.add(Path("models/__init__.py"))
        tracked.add(Path("models/test.py"))
        tracked.add(Path("pyproject.toml"))
        save_tracked(tracked, ctx)

        # Create registry with empty outputs warning
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=[],  # WARNING: empty outputs
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={},
        )

        # Save registry
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Run validation
        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Should have warnings but pass validation (no errors)
        assert result.passed
        assert len(result.warnings) > 0
        assert len(result.errors) == 0

    def test_status_command_displays_info(self, tmp_path, monkeypatch):
        """Test that status command displays preflight info messages."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create model and target files
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "__init__.py").write_text("")
        (models_dir / "test.py").write_text("class TestModel: pass")

        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "__init__.py").write_text("")
        (targets_dir / "test.py").write_text("def target_fn(): pass")

        # Create pyproject.toml and track files
        create_minimal_pyproject(tmp_path)
        tracked = TrackedFiles()
        tracked.add(Path("models/__init__.py"))
        tracked.add(Path("models/test.py"))
        tracked.add(Path("targets/__init__.py"))
        tracked.add(Path("targets/test.py"))
        tracked.add(Path("pyproject.toml"))
        save_tracked(tracked, ctx)

        # Create registry with unused outputs
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence", "prevalence", "mortality"],  # 3 outputs
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:target_fn",
                    model_output="incidence",  # Only uses one
                    data=[],
                    target_digest=None,
                )
            },
        )

        # Save registry
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Run validation
        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Should have info messages
        assert result.passed
        assert len(result.infos) == 2  # prevalence and mortality unused
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_preflight_with_valid_registry(self, tmp_path, monkeypatch):
        """Test that valid registry passes all checks."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create model and target files
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "__init__.py").write_text("")
        (models_dir / "test.py").write_text("class TestModel: pass")

        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "__init__.py").write_text("")
        (targets_dir / "test.py").write_text("def target_fn(): pass")

        # Create pyproject.toml and track files
        create_minimal_pyproject(tmp_path)
        tracked = TrackedFiles()
        tracked.add(Path("models/__init__.py"))
        tracked.add(Path("models/test.py"))
        tracked.add(Path("targets/__init__.py"))
        tracked.add(Path("targets/test.py"))
        tracked.add(Path("pyproject.toml"))
        save_tracked(tracked, ctx)

        # Create valid registry
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence"],
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:target_fn",
                    model_output="incidence",  # Matches model output
                    data=[],
                    target_digest=None,
                )
            },
        )

        # Save registry
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Run validation
        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Should pass with no issues
        assert result.passed
        assert len(result.errors) == 0
        assert len(result.warnings) == 0
        assert len(result.infos) == 0

    def test_preflight_with_complex_errors(self, tmp_path, monkeypatch):
        """Test preflight with multiple error types."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create partial file structure (some files missing)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "working.py").write_text("class WorkingModel: pass")
        # Note: missing.py intentionally not created

        # Create registry with multiple errors
        registry = BundleRegistry(
            version="1.0",
            models={
                "working_model": ModelEntry(
                    entrypoint="models.working:WorkingModel",
                    path="models/working.py",
                    class_name="WorkingModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence"],
                    data=["data/missing.csv"],  # ERROR: missing file
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                ),
                "broken_model": ModelEntry(
                    entrypoint="models.missing.BrokenModel",  # ERROR: invalid entrypoint
                    path="models/missing.py",  # ERROR: missing file
                    class_name="BrokenModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["prevalence"],
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:def456",
                ),
            },
            targets={
                "target1": TargetEntry(
                    path="targets/missing.py",  # ERROR: missing file
                    entrypoint="targets.missing:target_fn",
                    model_output="mortality",  # ERROR: output doesn't exist
                    data=[],
                    target_digest=None,
                )
            },
        )

        # Save registry
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Run validation
        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Should fail with multiple errors
        assert not result.passed
        assert result.has_blocking_errors
        assert len(result.errors) >= 4  # Multiple errors present

        # Verify error categories
        error_categories = {issue.category for issue in result.errors}
        assert "output_pairing" in error_categories
        assert "missing_file" in error_categories
        assert "invalid_entrypoint" in error_categories

    def test_preflight_handles_missing_registry_gracefully(self, tmp_path, monkeypatch):
        """Test that preflight handles missing registry gracefully."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Don't create registry file

        # Create minimal status snapshot
        snapshot = ModelStatusSnapshot(
            timestamp=datetime.now(),
            models={},
            targets={},
            bundle_ref="localhost:5555/test",
            bundle_tag="dev",
            tracked_files=set(),
            cloud_manifest_digest=None,
            cloud_file_digests={},
            cloud_timestamp=None,
        )

        # Display preflight issues should not crash
        console = Console()
        display_preflight_issues(snapshot, console)

        # Should complete without exception

    def test_preflight_severity_ordering(self, tmp_path, monkeypatch):
        """Test that issues are properly categorized by severity."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create model file
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "test.py").write_text("class TestModel: pass")

        # Create registry with all severity levels
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence", "prevalence"],  # INFO: prevalence unused
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                ),
                "model2": ModelEntry(
                    entrypoint="models.empty:EmptyModel",
                    path="models/empty.py",
                    class_name="EmptyModel",
                    scenarios=[],
                    parameters=[],
                    outputs=[],  # WARNING: empty outputs
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:def456",
                ),
            },
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:target_fn",
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                ),
                "target2": TargetEntry(
                    path="targets/broken.py",
                    entrypoint="targets.broken:broken_fn",
                    model_output="mortality",  # ERROR: doesn't exist
                    data=[],
                    target_digest=None,
                ),
            },
        )

        # Save registry
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Run validation
        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Should have issues at all severity levels
        assert not result.passed  # Fails due to errors
        assert len(result.errors) >= 3  # model2 path missing, target2 path missing, mortality output missing
        assert len(result.warnings) >= 1  # model2 empty outputs
        assert len(result.infos) >= 1  # prevalence unused

    def test_error_messages_have_suggestions(self, tmp_path, monkeypatch):
        """Test that error messages include helpful suggestions."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create registry with output pairing error
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence"],
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:target_fn",
                    model_output="prevalence",  # Doesn't match
                    data=[],
                    target_digest=None,
                )
            },
        )

        # Save registry
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Run validation
        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Check that errors have suggestions
        for error in result.errors:
            assert error.suggestion is not None
            assert len(error.suggestion) > 0
