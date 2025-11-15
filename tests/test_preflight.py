"""Tests for preflight validation system."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from modelops_bundle.context import ProjectContext
from modelops_bundle.preflight import (
    CheckSeverity,
    PreflightValidator,
    ValidationIssue,
    ValidationResult,
)
from modelops_contracts import BundleRegistry, ModelEntry, TargetEntry


class TestValidationResult:
    """Test ValidationResult class."""

    def test_errors_property(self):
        """Test that errors property filters ERROR-level issues."""
        issues = [
            ValidationIssue(
                severity=CheckSeverity.ERROR,
                category="test",
                entity_type="model",
                entity_id="m1",
                message="Error message",
            ),
            ValidationIssue(
                severity=CheckSeverity.WARNING,
                category="test",
                entity_type="model",
                entity_id="m2",
                message="Warning message",
            ),
            ValidationIssue(
                severity=CheckSeverity.ERROR,
                category="test",
                entity_type="target",
                entity_id="t1",
                message="Another error",
            ),
        ]
        result = ValidationResult(passed=False, issues=issues)

        assert len(result.errors) == 2
        assert all(i.severity == CheckSeverity.ERROR for i in result.errors)

    def test_warnings_property(self):
        """Test that warnings property filters WARNING-level issues."""
        issues = [
            ValidationIssue(
                severity=CheckSeverity.ERROR,
                category="test",
                entity_type="model",
                entity_id="m1",
                message="Error message",
            ),
            ValidationIssue(
                severity=CheckSeverity.WARNING,
                category="test",
                entity_type="model",
                entity_id="m2",
                message="Warning message",
            ),
            ValidationIssue(
                severity=CheckSeverity.WARNING,
                category="test",
                entity_type="target",
                entity_id="t1",
                message="Another warning",
            ),
        ]
        result = ValidationResult(passed=False, issues=issues)

        assert len(result.warnings) == 2
        assert all(i.severity == CheckSeverity.WARNING for i in result.warnings)

    def test_infos_property(self):
        """Test that infos property filters INFO-level issues."""
        issues = [
            ValidationIssue(
                severity=CheckSeverity.ERROR,
                category="test",
                entity_type="model",
                entity_id="m1",
                message="Error message",
            ),
            ValidationIssue(
                severity=CheckSeverity.INFO,
                category="test",
                entity_type="model",
                entity_id="m2",
                message="Info message",
            ),
            ValidationIssue(
                severity=CheckSeverity.INFO,
                category="test",
                entity_type="model",
                entity_id="m3",
                message="Another info",
            ),
        ]
        result = ValidationResult(passed=True, issues=issues)

        assert len(result.infos) == 2
        assert all(i.severity == CheckSeverity.INFO for i in result.infos)

    def test_has_blocking_errors(self):
        """Test that has_blocking_errors detects ERROR-level issues."""
        # No errors
        result1 = ValidationResult(
            passed=True,
            issues=[
                ValidationIssue(
                    severity=CheckSeverity.WARNING,
                    category="test",
                    entity_type="model",
                    entity_id="m1",
                    message="Warning",
                )
            ],
        )
        assert not result1.has_blocking_errors

        # Has errors
        result2 = ValidationResult(
            passed=False,
            issues=[
                ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="test",
                    entity_type="model",
                    entity_id="m1",
                    message="Error",
                )
            ],
        )
        assert result2.has_blocking_errors


class TestPreflightValidator:
    """Test PreflightValidator class."""

    def test_check_output_pairing_valid(self, tmp_path, monkeypatch):
        """Test that valid output pairing passes."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create registry with matching output/target
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence", "prevalence"],
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
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_output_pairing()

        assert len(issues) == 0

    def test_check_output_pairing_missing(self, tmp_path, monkeypatch):
        """Test that missing output is detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create registry with mismatched output
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
                    model_output="prevalence",  # Wants prevalence (missing!)
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_output_pairing()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "output_pairing"
        assert "prevalence" in issues[0].message
        assert issues[0].suggestion is not None

    def test_check_output_pairing_no_models(self, tmp_path, monkeypatch):
        """Test error when no models have outputs."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create registry with target but no models
        registry = BundleRegistry(
            version="1.0",
            models={},
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:target_fn",
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_output_pairing()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert "no models have outputs registered yet" in issues[0].suggestion

    def test_check_missing_files_model_file(self, tmp_path, monkeypatch):
        """Test that missing model file is detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/missing.py",  # Doesn't exist
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
            targets={},
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_missing_files()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "missing_file"
        assert "models/missing.py" in issues[0].message

    def test_check_missing_files_data_dependency(self, tmp_path, monkeypatch):
        """Test that missing data dependency is detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create model file but not data file
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "test.py").write_text("class TestModel: pass")

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
                    data=["data/missing.csv"],  # Missing!
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={},
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_missing_files()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "missing_file"
        assert "data/missing.csv" in issues[0].message
        assert issues[0].entity_type == "model"

    def test_check_missing_files_target_data(self, tmp_path, monkeypatch):
        """Test that missing target observation data is detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create target file but not observation data
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "test.py").write_text("def target_fn(): pass")

        registry = BundleRegistry(
            version="1.0",
            models={},
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:target_fn",
                    model_output="incidence",
                    data=["data/observations.csv"],  # Missing!
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_missing_files()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "missing_file"
        assert "data/observations.csv" in issues[0].message
        assert issues[0].entity_type == "target"

    def test_check_entrypoints_valid(self, tmp_path, monkeypatch):
        """Test that valid entrypoints pass."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create actual files so imports work
        model_dir = tmp_path / "models"
        model_dir.mkdir()
        (model_dir / "__init__.py").write_text("")
        (model_dir / "test.py").write_text("class TestModel:\n    pass\n")

        target_dir = tmp_path / "targets"
        target_dir.mkdir()
        (target_dir / "__init__.py").write_text("")
        (target_dir / "test.py").write_text("def target_fn(data_paths):\n    pass\n")

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
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_entrypoints()

        assert len(issues) == 0

    def test_check_entrypoints_invalid_model(self, tmp_path, monkeypatch):
        """Test that invalid model entrypoint is detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test.TestModel",  # Missing colon!
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
            targets={},
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_entrypoints()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "invalid_entrypoint"
        assert "module.path:ClassName" in issues[0].suggestion

    def test_check_entrypoints_invalid_target(self, tmp_path, monkeypatch):
        """Test that invalid target entrypoint is detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        registry = BundleRegistry(
            version="1.0",
            models={},
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test.target_fn",  # Missing colon!
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_entrypoints()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "invalid_entrypoint"
        assert "module.path:function_name" in issues[0].suggestion

    def test_check_empty_outputs(self, tmp_path, monkeypatch):
        """Test that models with no outputs generate warning."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=[],  # No outputs!
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={},
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_empty_outputs()

        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.WARNING
        assert issues[0].category == "empty_outputs"
        assert "no outputs registered" in issues[0].message

    def test_check_unused_outputs(self, tmp_path, monkeypatch):
        """Test that unused model outputs generate info message."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

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
                    model_output="incidence",  # Only uses one output
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_unused_outputs()

        # Should have 2 unused outputs: prevalence and mortality
        assert len(issues) == 2
        assert all(i.severity == CheckSeverity.INFO for i in issues)
        assert all(i.category == "unused_output" for i in issues)

        unused_outputs = {i.message.split("'")[3] for i in issues}
        assert unused_outputs == {"prevalence", "mortality"}

    def test_check_unused_outputs_all_used(self, tmp_path, monkeypatch):
        """Test that no info when all outputs are used."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/test.py",
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence", "prevalence"],
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
                    entrypoint="targets.test:incidence_fn",
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                ),
                "target2": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:prevalence_fn",
                    model_output="prevalence",
                    data=[],
                    target_digest=None,
                ),
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_unused_outputs()

        assert len(issues) == 0

    def test_validate_all_integration(self, tmp_path, monkeypatch):
        """Test that validate_all runs all checks and combines results."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create a registry with multiple issues
        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:TestModel",
                    path="models/missing.py",  # ERROR: missing file
                    class_name="TestModel",
                    scenarios=[],
                    parameters=[],
                    outputs=[],  # WARNING: empty outputs
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                ),
                "model2": ModelEntry(
                    entrypoint="models.other:OtherModel",
                    path="models/other.py",
                    class_name="OtherModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["incidence", "mortality"],  # INFO: mortality unused
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
                )
            },
        )

        # Create model2 file so it doesn't trigger missing file error
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "other.py").write_text("class OtherModel: pass")

        # Create target file
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "test.py").write_text("def target_fn(): pass")

        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Should have failures due to errors
        assert not result.passed
        assert result.has_blocking_errors

        # Check we have issues of each severity
        assert len(result.errors) >= 1  # Missing model1 file
        assert len(result.warnings) >= 1  # Empty outputs for model1
        assert len(result.infos) >= 1  # Unused mortality output

        # Verify specific issues
        error_categories = {i.category for i in result.errors}
        warning_categories = {i.category for i in result.warnings}
        info_categories = {i.category for i in result.infos}

        assert "missing_file" in error_categories
        assert "empty_outputs" in warning_categories
        assert "unused_output" in info_categories

    def test_check_entrypoints_not_importable_target(self, tmp_path, monkeypatch):
        """Test that target entrypoints that can't be imported are detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create a target file with a different function name
        target_dir = tmp_path / "targets"
        target_dir.mkdir()
        target_file = target_dir / "test.py"
        target_file.write_text("""
def actual_target_function(data_paths):
    return None
""")

        # Create __init__.py for importability
        (target_dir / "__init__.py").write_text("")

        registry = BundleRegistry(
            version="1.0",
            models={},
            targets={
                "target1": TargetEntry(
                    path="targets/test.py",
                    entrypoint="targets.test:wrong_function_name",  # Wrong name!
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_entrypoints()

        # Should detect that wrong_function_name doesn't exist
        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "missing_entrypoint_symbol"
        assert "wrong_function_name" in issues[0].message

    def test_check_entrypoints_not_importable_model(self, tmp_path, monkeypatch):
        """Test that model entrypoints that can't be imported are detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create a model file with a different class name
        model_dir = tmp_path / "models"
        model_dir.mkdir()
        model_file = model_dir / "test.py"
        model_file.write_text("""
class ActualModelClass:
    pass
""")

        # Create __init__.py for importability
        (model_dir / "__init__.py").write_text("")

        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.test:WrongClassName",  # Wrong class!
                    path="models/test.py",
                    class_name="WrongClassName",
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
            targets={},
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_entrypoints()

        # Should detect that WrongClassName doesn't exist
        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "missing_entrypoint_symbol"
        assert "WrongClassName" in issues[0].message

    def test_check_entrypoints_module_not_found(self, tmp_path, monkeypatch):
        """Test that missing entrypoint files are detected as errors.

        AST-based validation can detect when the entrypoint file itself
        doesn't exist, which is a structural error that should be caught.
        """
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        registry = BundleRegistry(
            version="1.0",
            models={},
            targets={
                "target1": TargetEntry(
                    path="targets/nonexistent.py",
                    entrypoint="targets.nonexistent:target_fn",  # Module doesn't exist!
                    model_output="incidence",
                    data=[],
                    target_digest=None,
                )
            },
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_entrypoints()

        # Should report error for missing entrypoint file
        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "missing_entrypoint_file"
        assert "target1" in issues[0].entity_id

    def test_check_entrypoints_syntax_error(self, tmp_path, monkeypatch):
        """Test that syntax errors in entrypoint files are detected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()

        # Create model directory with syntax error
        model_dir = tmp_path / "models"
        model_dir.mkdir()
        (model_dir / "bad_syntax.py").write_text("""
# This file has a syntax error
def my_function(
    print("missing closing paren")
""")

        registry = BundleRegistry(
            version="1.0",
            models={
                "model1": ModelEntry(
                    entrypoint="models.bad_syntax:MyModel",
                    path="models/bad_syntax.py",
                    class_name="MyModel",
                    scenarios=[],
                    parameters=[],
                    outputs=["result"],
                    data=[],
                    data_digests={},
                    code=[],
                    code_digests={},
                    model_digest="sha256:abc123",
                )
            },
            targets={},
        )

        validator = PreflightValidator(ctx, registry)
        issues = validator._check_entrypoints()

        # Should detect syntax error
        assert len(issues) == 1
        assert issues[0].severity == CheckSeverity.ERROR
        assert issues[0].category == "syntax_error"
        assert "line" in issues[0].message.lower()
        assert "model1" in issues[0].entity_id
