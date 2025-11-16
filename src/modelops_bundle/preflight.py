"""Preflight validation system for bundle integrity checks.

Validates that bundles are ready for job submission by checking:
- Target/model output pairing
- File existence
- Entrypoint validity
- Dependency health
"""

import ast
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Set

from modelops_contracts import BundleRegistry

from .context import ProjectContext


class CheckSeverity(Enum):
    """Severity level for validation issues."""
    ERROR = "error"      # Blocks submission
    WARNING = "warning"  # Shows in status with ⚠
    INFO = "info"        # Informational only


@dataclass
class ValidationIssue:
    """Single validation issue."""
    severity: CheckSeverity
    category: str  # e.g., "output_pairing", "missing_file"
    entity_type: str  # "model", "target", "registry"
    entity_id: Optional[str]  # model_id or target_id
    message: str
    suggestion: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of preflight validation."""
    passed: bool  # True if no errors
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ValidationIssue]:
        """Get only error-level issues."""
        return [i for i in self.issues if i.severity == CheckSeverity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        """Get only warning-level issues."""
        return [i for i in self.issues if i.severity == CheckSeverity.WARNING]

    @property
    def infos(self) -> List[ValidationIssue]:
        """Get only info-level issues."""
        return [i for i in self.issues if i.severity == CheckSeverity.INFO]

    @property
    def has_blocking_errors(self) -> bool:
        """Check if there are any errors that would block submission."""
        return len(self.errors) > 0


class PreflightValidator:
    """Validates bundle readiness for job submission."""

    def __init__(self, ctx: ProjectContext, registry: BundleRegistry):
        """Initialize validator.

        Args:
            ctx: Project context
            registry: Bundle registry to validate
        """
        self.ctx = ctx
        self.registry = registry

    def validate_all(self) -> ValidationResult:
        """Run all preflight checks.

        Returns:
            ValidationResult with all issues found
        """
        issues = []

        # Critical checks (errors)
        issues.extend(self._check_output_pairing())
        issues.extend(self._check_missing_files())
        issues.extend(self._check_entrypoints())

        # Warning checks
        issues.extend(self._check_empty_outputs())
        issues.extend(self._check_untracked_files())
        issues.extend(self._check_package_structure())

        # Info checks
        issues.extend(self._check_unused_outputs())

        return ValidationResult(
            passed=not any(i.severity == CheckSeverity.ERROR for i in issues),
            issues=issues
        )

    def _check_output_pairing(self) -> List[ValidationIssue]:
        """Check that all targets have matching model outputs.

        This is the primary check to prevent KeyError at runtime when
        a target tries to extract a model output that doesn't exist.

        Returns:
            List of validation issues
        """
        issues = []

        # Build set of all model outputs
        available_outputs: Set[str] = set()
        for model in self.registry.models.values():
            available_outputs.update(model.outputs)

        # Check each target
        for target_id, target in self.registry.targets.items():
            if target.model_output not in available_outputs:
                if available_outputs:
                    suggestion = (
                        f"Register a model with output '{target.model_output}' or update target "
                        f"to use available outputs: {', '.join(sorted(available_outputs))}"
                    )
                else:
                    suggestion = (
                        f"Register a model with output '{target.model_output}' - no models have "
                        f"outputs registered yet"
                    )

                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="output_pairing",
                    entity_type="target",
                    entity_id=target_id,
                    message=f"Target expects model output '{target.model_output}' but no model provides it",
                    suggestion=suggestion
                ))

        return issues

    def _check_missing_files(self) -> List[ValidationIssue]:
        """Check all referenced files exist.

        Returns:
            List of validation issues
        """
        issues = []

        # Check model files
        for model_id, model in self.registry.models.items():
            if not (self.ctx.absolute(model.path)).exists():
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="missing_file",
                    entity_type="model",
                    entity_id=model_id,
                    message=f"Model file not found: {model.path}",
                    suggestion="Ensure file exists or update registry"
                ))

            # Check data dependencies
            for data_file in model.data:
                if not (self.ctx.absolute(data_file)).exists():
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.ERROR,
                        category="missing_file",
                        entity_type="model",
                        entity_id=model_id,
                        message=f"Data dependency not found: {data_file}",
                        suggestion="Add missing file or remove from dependencies"
                    ))

            # Check code dependencies
            for code_file in model.code:
                if not (self.ctx.absolute(code_file)).exists():
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.ERROR,
                        category="missing_file",
                        entity_type="model",
                        entity_id=model_id,
                        message=f"Code dependency not found: {code_file}",
                        suggestion="Add missing file or remove from dependencies"
                    ))

        # Check target files
        for target_id, target in self.registry.targets.items():
            if not (self.ctx.absolute(target.path)).exists():
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="missing_file",
                    entity_type="target",
                    entity_id=target_id,
                    message=f"Target file not found: {target.path}",
                    suggestion="Ensure file exists or update registry"
                ))

            # Check observation data
            for data_file in target.data:
                if not (self.ctx.absolute(data_file)).exists():
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.ERROR,
                        category="missing_file",
                        entity_type="target",
                        entity_id=target_id,
                        message=f"Observation data not found: {data_file}",
                        suggestion="Add missing file or remove from dependencies"
                    ))

        return issues

    def _module_to_file(self, module_path: str) -> Optional[Path]:
        """Convert module path to file path.

        Args:
            module_path: Module path like 'models.sir' or 'targets.incidence'

        Returns:
            Absolute path to the file, or None if not found
        """
        # Convert 'models.sir' -> 'models/sir.py'
        parts = module_path.split('.')
        rel_path = Path(*parts).with_suffix('.py')
        abs_path = self.ctx.root / rel_path

        return abs_path if abs_path.exists() else None

    def _parse_file_ast(self, file_path: Path) -> tuple[Optional[ast.Module], Optional[Exception]]:
        """Parse Python file using AST without executing code.

        Args:
            file_path: Path to Python file

        Returns:
            Tuple of (AST module, error). If successful, (module, None). If failed, (None, error).
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source, filename=str(file_path))
            return tree, None
        except SyntaxError as e:
            return None, e
        except Exception as e:
            return None, e

    def _symbol_in_ast(self, tree: ast.Module, name: str, kind: str) -> bool:
        """Check if a class or function exists in the AST.

        Args:
            tree: Parsed AST module
            name: Name of the symbol to find
            kind: Either "class" or "function"

        Returns:
            True if symbol exists, False otherwise
        """
        if kind == "class":
            return any(
                isinstance(node, ast.ClassDef) and node.name == name
                for node in ast.walk(tree)
            )
        elif kind == "function":
            return any(
                isinstance(node, ast.FunctionDef) and node.name == name
                for node in ast.walk(tree)
            )
        return False

    def _check_entrypoints(self) -> List[ValidationIssue]:
        """Validate entrypoints using AST parsing (no code execution).

        This validates that entrypoints are structurally correct without
        requiring bundle dependencies to be installed. Checks:
        1. Format is 'module.path:ClassName' or 'module.path:function_name'
        2. File exists for the module path
        3. File has valid Python syntax
        4. Class/function name exists in the AST

        Returns:
            List of validation issues
        """
        issues = []

        # Check model entrypoints
        for model_id, model in self.registry.models.items():
            # Basic validation: entrypoint has module:class format
            if ':' not in model.entrypoint:
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="invalid_entrypoint",
                    entity_type="model",
                    entity_id=model_id,
                    message=f"Invalid entrypoint format: {model.entrypoint}",
                    suggestion="Use format 'module.path:ClassName'"
                ))
                continue

            module_path, class_name = model.entrypoint.rsplit(':', 1)

            # Step 1: Check file exists
            file_path = self._module_to_file(module_path)
            if not file_path:
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="missing_entrypoint_file",
                    entity_type="model",
                    entity_id=model_id,
                    message=f"Module file not found for entrypoint '{model.entrypoint}'",
                    suggestion=f"Expected file: {module_path.replace('.', '/')}.py"
                ))
                continue

            # Step 2: Parse file with AST (no execution)
            tree, error = self._parse_file_ast(file_path)
            if error:
                if isinstance(error, SyntaxError):
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.ERROR,
                        category="syntax_error",
                        entity_type="model",
                        entity_id=model_id,
                        message=f"Syntax error in {file_path.name} line {error.lineno}: {error.msg}",
                        suggestion=f"Fix syntax error in {file_path}"
                    ))
                else:
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.ERROR,
                        category="parse_error",
                        entity_type="model",
                        entity_id=model_id,
                        message=f"Cannot parse {file_path.name}: {error}",
                        suggestion=f"Check file encoding and syntax in {file_path}"
                    ))
                continue

            # Step 3: Check class exists in AST
            if not self._symbol_in_ast(tree, class_name, "class"):
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="missing_entrypoint_symbol",
                    entity_type="model",
                    entity_id=model_id,
                    message=f"Class '{class_name}' not found in {file_path.name}",
                    suggestion=f"Define class '{class_name}' in {file_path} or update entrypoint"
                ))

        # Check target entrypoints
        for target_id, target in self.registry.targets.items():
            # Basic validation: entrypoint has module:function format
            if ':' not in target.entrypoint:
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="invalid_entrypoint",
                    entity_type="target",
                    entity_id=target_id,
                    message=f"Invalid entrypoint format: {target.entrypoint}",
                    suggestion="Use format 'module.path:function_name'"
                ))
                continue

            module_path, function_name = target.entrypoint.rsplit(':', 1)

            # Step 1: Check file exists
            file_path = self._module_to_file(module_path)
            if not file_path:
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="missing_entrypoint_file",
                    entity_type="target",
                    entity_id=target_id,
                    message=f"Module file not found for entrypoint '{target.entrypoint}'",
                    suggestion=f"Expected file: {module_path.replace('.', '/')}.py"
                ))
                continue

            # Step 2: Parse file with AST (no execution)
            tree, error = self._parse_file_ast(file_path)
            if error:
                if isinstance(error, SyntaxError):
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.ERROR,
                        category="syntax_error",
                        entity_type="target",
                        entity_id=target_id,
                        message=f"Syntax error in {file_path.name} line {error.lineno}: {error.msg}",
                        suggestion=f"Fix syntax error in {file_path}"
                    ))
                else:
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.ERROR,
                        category="parse_error",
                        entity_type="target",
                        entity_id=target_id,
                        message=f"Cannot parse {file_path.name}: {error}",
                        suggestion=f"Check file encoding and syntax in {file_path}"
                    ))
                continue

            # Step 3: Check function exists in AST
            if not self._symbol_in_ast(tree, function_name, "function"):
                issues.append(ValidationIssue(
                    severity=CheckSeverity.ERROR,
                    category="missing_entrypoint_symbol",
                    entity_type="target",
                    entity_id=target_id,
                    message=f"Function '{function_name}' not found in {file_path.name}",
                    suggestion=f"Define function '{function_name}' in {file_path} or update entrypoint"
                ))

        return issues

    def _check_empty_outputs(self) -> List[ValidationIssue]:
        """Warn about models with no outputs.

        Models without outputs cannot be used with calibration targets.

        Returns:
            List of validation issues
        """
        issues = []

        for model_id, model in self.registry.models.items():
            if not model.outputs:
                issues.append(ValidationIssue(
                    severity=CheckSeverity.WARNING,
                    category="empty_outputs",
                    entity_type="model",
                    entity_id=model_id,
                    message=f"Model '{model_id}' has no outputs registered",
                    suggestion="Models without outputs cannot be used with calibration targets"
                ))

        return issues

    def _check_untracked_files(self) -> List[ValidationIssue]:
        """Warn about registry files not in tracking.

        Returns:
            List of validation issues
        """
        from .ops import load_tracked

        issues = []
        tracked = load_tracked(self.ctx)
        tracked_set = set(tracked.files)

        all_deps = self.registry.get_all_dependencies()
        for dep in all_deps:
            rel_path = str(self.ctx.to_project_relative(dep))
            if rel_path not in tracked_set:
                issues.append(ValidationIssue(
                    severity=CheckSeverity.WARNING,
                    category="untracked_file",
                    entity_type="registry",
                    entity_id=None,
                    message=f"Registry references untracked file: {rel_path}",
                    suggestion="Run 'mops-bundle add' to track this file"
                ))

        return issues

    def _check_unused_outputs(self) -> List[ValidationIssue]:
        """Check for model outputs without corresponding targets (informational).

        This is purely informational - it's OK for models to have outputs
        that aren't used by any target. But we inform the user in case they
        want to add calibration targets for those outputs.

        Returns:
            List of validation issues
        """
        issues = []

        # Build set of outputs used by targets
        used_outputs: Set[str] = set()
        for target in self.registry.targets.values():
            used_outputs.add(target.model_output)

        # Check each model's outputs
        for model_id, model in self.registry.models.items():
            for output in model.outputs:
                if output not in used_outputs:
                    issues.append(ValidationIssue(
                        severity=CheckSeverity.INFO,
                        category="unused_output",
                        entity_type="model",
                        entity_id=model_id,
                        message=f"Model '{model_id}' produces output '{output}' with no corresponding target",
                        suggestion="Add a target if you want to calibrate against this output"
                    ))

        return issues

    def _check_package_structure(self) -> List[ValidationIssue]:
        """Check that directories in import paths have __init__.py files.

        This validates that any directory that needs to be a Python package
        (i.e., is part of an entrypoint module path) has an __init__.py file.
        Without these files, Python cannot import modules from the directory.

        Examples:
        - entrypoint "models.sir:SIRModel" requires models/__init__.py
        - entrypoint "targets.incidence:target_fn" requires targets/__init__.py

        Returns:
            List of validation issues (warnings for missing __init__.py)
        """
        issues = []
        # Track which directories need __init__.py and why
        needed_dirs: Dict[Path, Set[str]] = {}

        # Check model entrypoints
        for model_id, model in self.registry.models.items():
            if ':' not in model.entrypoint:
                continue

            module_path, _ = model.entrypoint.rsplit(':', 1)
            parts = module_path.split('.')

            # Check each parent directory in the module path
            # e.g., "models.submodule.sir" checks "models/" and "models/submodule/"
            for i in range(1, len(parts) + 1):
                dir_parts = parts[:i]
                dir_path = self.ctx.root / Path(*dir_parts)

                # Skip if not a directory or is a hidden/build directory
                if not dir_path.is_dir():
                    continue
                if any(p.startswith('.') or p in ('__pycache__', 'build', 'dist', 'egg-info')
                       for p in dir_parts):
                    continue

                # Record that this directory needs __init__.py
                if dir_path not in needed_dirs:
                    needed_dirs[dir_path] = set()
                needed_dirs[dir_path].add(f"model '{model_id}'")

        # Check target entrypoints
        for target_id, target in self.registry.targets.items():
            if ':' not in target.entrypoint:
                continue

            module_path, _ = target.entrypoint.rsplit(':', 1)
            parts = module_path.split('.')

            for i in range(1, len(parts) + 1):
                dir_parts = parts[:i]
                dir_path = self.ctx.root / Path(*dir_parts)

                if not dir_path.is_dir():
                    continue
                if any(p.startswith('.') or p in ('__pycache__', 'build', 'dist', 'egg-info')
                       for p in dir_parts):
                    continue

                if dir_path not in needed_dirs:
                    needed_dirs[dir_path] = set()
                needed_dirs[dir_path].add(f"target '{target_id}'")

        # Check each directory for __init__.py
        for dir_path, entities in needed_dirs.items():
            init_file = dir_path / "__init__.py"
            if not init_file.exists():
                rel_dir = self.ctx.to_project_relative(dir_path)
                entities_list = sorted(entities)

                issues.append(ValidationIssue(
                    severity=CheckSeverity.WARNING,
                    category="missing_init_file",
                    entity_type="registry",
                    entity_id=None,
                    message=f"Directory '{rel_dir}' is a Python package but missing __init__.py (needed by {', '.join(entities_list[:3])})",
                    suggestion=f"Create the file: touch {rel_dir}/__init__.py"
                ))

        return issues
