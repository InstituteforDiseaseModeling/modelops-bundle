"""AST-based model discovery without executing imports.

Scans Python files to find model classes using only
the Abstract Syntax Tree, avoiding any code execution.
"""

import ast
from pathlib import Path
from typing import List, Dict, Any, Set, Optional
import logging

logger = logging.getLogger(__name__)


class ModelDiscoveryVisitor(ast.NodeVisitor):
    """AST visitor that finds model classes with simulate/parameters methods."""

    def __init__(self, module_path: str, file_path: Optional[Path] = None):
        self.module_path = module_path
        self.file_path = file_path
        self.models: List[Dict[str, Any]] = []
        self.imports: Dict[str, str] = {}  # alias -> full_name
        self.from_imports: Dict[str, str] = {}  # name -> module

    def visit_Import(self, node: ast.Import) -> None:
        """Track import statements."""
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            self.imports[name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Track from-import statements."""
        if node.module:
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                self.from_imports[name] = node.module
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Check if class is likely a model (has simulate/parameters methods)."""
        # Extract method names
        method_names = set()
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                method_names.add(item.name)
        
        # Check if it's likely a model class based on methods
        has_simulate = 'simulate' in method_names
        has_parameters = 'parameters' in method_names
        
        # Also check class name patterns
        is_likely_model = self._is_likely_model_class(node.name, method_names)
        
        # If it looks like a model, record it
        if has_simulate or has_parameters or is_likely_model:
            # Extract base class names
            base_class_names = []
            for base in node.bases:
                base_name = self._get_base_name(base)
                if base_name:
                    base_class_names.append(base_name)
            
            # Extract methods info
            methods_info = self._extract_methods(node)
            
            model_info = {
                "class_name": node.name,
                "module_path": self.module_path,
                "full_path": f"{self.module_path}:{node.name}",
                "file_path": str(self.file_path) if self.file_path else self.module_path.replace('.', '/') + '.py',
                "line_number": node.lineno,
                "base_classes": base_class_names,
                "methods": methods_info,
                "has_simulate": has_simulate,
                "has_parameters": has_parameters
            }
            self.models.append(model_info)
        
        self.generic_visit(node)

    def _get_base_name(self, base: ast.AST) -> Optional[str]:
        """Extract the name of a base class."""
        if isinstance(base, ast.Name):
            return base.id
        elif isinstance(base, ast.Attribute):
            return self._get_attribute_name(base)
        return None

    def _is_likely_model_class(self, class_name: str, method_names: Set[str]) -> bool:
        """Check if class is likely a model based on naming patterns."""
        class_lower = class_name.lower()
        
        # Check class name patterns
        model_patterns = [
            'model', 'simulation', 'dynamics', 'system',
            'sir', 'seir', 'seis', 'sird', 'compartment',
            'epidemic', 'agent', 'network'
        ]
        
        for pattern in model_patterns:
            if pattern in class_lower:
                return True
        
        # Check for model-like methods
        model_methods = {
            'simulate', 'run', 'step', 'advance',
            'parameters', 'params', 'get_parameters',
            'initialize', 'reset', 'setup'
        }
        
        if method_names & model_methods:
            return True
        
        return False

    def _extract_methods(self, node: ast.ClassDef) -> Dict[str, List[str]]:
        """Extract method information from class."""
        methods = {
            "all_methods": [],
            "decorated_methods": [],
            "property_methods": []
        }
        
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                methods["all_methods"].append(item.name)
                
                # Check for decorators
                if item.decorator_list:
                    methods["decorated_methods"].append(item.name)
                    
                    # Check specifically for @property
                    for dec in item.decorator_list:
                        if isinstance(dec, ast.Name) and dec.id == 'property':
                            methods["property_methods"].append(item.name)
                            break
        
        return methods

    def _get_attribute_name(self, attr: ast.Attribute) -> str:
        """Get full attribute name like 'module.attr'."""
        parts = []
        current = attr
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))


def discover_models_in_file(file_path: Path, base_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Discover model classes in a single Python file.

    Args:
        file_path: Path to Python file to scan
        base_path: Base path for relative module path calculation (defaults to current directory)

    Returns:
        List of model info dictionaries
    """
    try:
        content = file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        logger.warning(f"Skipping {file_path}: not valid UTF-8")
        return []

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError as e:
        logger.warning(f"Skipping {file_path}: syntax error at line {e.lineno}: {e.msg}")
        return []

    # Convert file path to module path
    if base_path is None:
        base_path = Path.cwd()

    try:
        # Try to make it relative to base path
        relative_path = file_path.relative_to(base_path)
        module_parts = relative_path.with_suffix('').parts
    except ValueError:
        # If file is not under base path, use file name only
        module_parts = (file_path.stem,)

    # Handle __init__.py files
    if module_parts[-1] == '__init__':
        module_parts = module_parts[:-1]

    # Remove 'src' prefix if present
    if module_parts and module_parts[0] == 'src':
        module_parts = module_parts[1:]

    module_path = '.'.join(module_parts)

    # Calculate file path relative to base_path for display
    try:
        relative_file_path = file_path.relative_to(base_path or Path.cwd())
    except ValueError:
        relative_file_path = file_path.name

    visitor = ModelDiscoveryVisitor(module_path, relative_file_path)
    visitor.visit(tree)

    return visitor.models


def discover_models_in_directory(
    directory: Path,
    patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Discover model classes in a directory tree.

    Args:
        directory: Root directory to scan
        patterns: Optional list of glob patterns to include files
                  (defaults to ["**/*.py"])
        exclude_patterns: Optional list of glob patterns to exclude

    Returns:
        List of all discovered models
    """
    if patterns is None:
        patterns = ["**/*.py"]
    
    if exclude_patterns is None:
        exclude_patterns = [
            "**/test_*.py",
            "**/*_test.py",
            "**/tests/**/*.py",
            "**/__pycache__/**/*.py",
            "**/.*/**/*.py",  # Hidden directories
            "**/venv/**/*.py",
            "**/env/**/*.py",
            "**/.venv/**/*.py"
        ]
    
    models = []
    scanned_files = set()
    excluded_files = set()
    
    # First collect files to exclude
    for pattern in exclude_patterns:
        for file_path in directory.glob(pattern):
            excluded_files.add(file_path)
    
    # Then scan included files
    for pattern in patterns:
        for file_path in directory.glob(pattern):
            if file_path in scanned_files or file_path in excluded_files:
                continue
            scanned_files.add(file_path)
            
            if file_path.is_file() and file_path.suffix == '.py':
                try:
                    file_models = discover_models_in_file(file_path, directory)
                    models.extend(file_models)
                except Exception as e:
                    logger.warning(f"Error scanning {file_path}: {e}")
    
    return models


def discover_models(root_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Discover all model classes in the project.

    Args:
        root_path: Project root path (defaults to current directory)

    Returns:
        List of discovered model information

    Example:
        >>> models = discover_models()
        >>> for model in models:
        ...     print(f"Found: {model['full_path']}")
        Found: models.sir:SIRModel
        Found: models.seir:SEIRModel
    """
    if root_path is None:
        root_path = Path.cwd()
    
    # Common patterns for Python projects
    search_patterns = [
        "src/**/*.py",
        "models/**/*.py",
        "*.py"  # Top-level Python files
    ]
    
    # Collect unique models
    all_models = []
    for pattern in search_patterns:
        try:
            models = discover_models_in_directory(root_path, [pattern])
            all_models.extend(models)
        except Exception as e:
            logger.debug(f"Pattern {pattern} failed: {e}")
    
    # Deduplicate by full_path
    seen = set()
    unique_models = []
    for model in all_models:
        key = model['full_path']
        if key not in seen:
            seen.add(key)
            unique_models.append(model)
    
    return unique_models
