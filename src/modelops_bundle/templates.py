"""Project template generation for mops-bundle init.

This module provides functions to create starter files when initializing
a new ModelOps bundle project, similar to how uv init creates templates.
"""

from pathlib import Path
from typing import Optional


def create_pyproject_toml(project_name: str) -> str:
    """Generate pyproject.toml content for a new project.

    Args:
        project_name: Name of the project

    Returns:
        Content for pyproject.toml file
    """
    return f'''[project]
name = "{project_name}"
version = "0.1.0"
description = "A ModelOps bundle"
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[tool.modelops-bundle]
# Models will be added here by 'mops-bundle discover --save'

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
'''


def create_readme(project_name: str) -> str:
    """Generate README.md content for a new project.

    Args:
        project_name: Name of the project

    Returns:
        Content for README.md file
    """
    return f'''# {project_name}

A ModelOps bundle project.

## Quick Start

```bash
# Discover models in the codebase
mops-bundle discover

# Add models interactively
mops-bundle discover --interactive --save

# Generate manifest
mops-bundle manifest

# Push to registry
mops-bundle push
```

## Project Structure

```
{project_name}/
├── models/           # Model implementations
│   └── example.py    # Example model
├── pyproject.toml    # Project configuration
├── README.md         # This file
└── .modelopsignore   # Patterns to exclude from bundle
```

## Next Steps

1. Add your model implementations to the `models/` directory
2. Use `mops-bundle discover` to find and register your models
3. Create a manifest with `mops-bundle manifest`
4. Push to your registry with `mops-bundle push`
'''


def create_example_model() -> str:
    """Generate example model code.

    Returns:
        Content for models/example.py file
    """
    return '''"""Example model for ModelOps."""


class ExampleModel:
    """A simple example model demonstrating the expected interface."""

    def __init__(self):
        """Initialize the model."""
        self.name = "Example"
        self.version = "0.1.0"

    def parameters(self):
        """Return default parameters for the model.

        Returns:
            Dict of parameter names to default values
        """
        return {
            "rate": 0.5,
            "scale": 1.0,
            "threshold": 0.8
        }

    def simulate(self, params, seed=None):
        """Run simulation with given parameters.

        Args:
            params: Dictionary of parameters
            seed: Optional random seed for reproducibility

        Returns:
            Dictionary of simulation results
        """
        # Your simulation logic here
        rate = params.get("rate", 0.5)
        scale = params.get("scale", 1.0)

        # Example computation
        result = rate * scale

        return {
            "result": result,
            "status": "success",
            "seed": seed
        }
'''


def create_modelopsignore() -> str:
    """Generate .modelopsignore content.

    Returns:
        Content for .modelopsignore file
    """
    return '''# Ignore patterns for mops-bundle

# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
*.egg
.pytest_cache/
.coverage
htmlcov/
.tox/
.hypothesis/

# Virtual environments
.venv/
venv/
env/
ENV/

# IDE and editors
.vscode/
.idea/
*.swp
*.swo
*~
.project
.pydevproject
.settings/

# OS files
.DS_Store
Thumbs.db
*.log

# Environment and secrets
.env
.env.*
*.key
*.pem
*.crt

# Testing
test_output/
tmp/
temp/

# Documentation builds
docs/_build/
site/
'''


def create_gitignore_entry() -> str:
    """Generate content to append to .gitignore.

    Returns:
        Lines to append to .gitignore
    """
    return '''
# ModelOps Bundle
.modelops-bundle/
'''


def create_project_templates(project_path: Path, project_name: str) -> None:
    """Create all template files for a new project.

    This creates the standard set of files for a new ModelOps bundle project:
    - pyproject.toml
    - README.md
    - models/example.py
    - .modelopsignore
    - Updates .gitignore

    Args:
        project_path: Path to the project directory
        project_name: Name of the project
    """
    # Create pyproject.toml
    pyproject_path = project_path / "pyproject.toml"
    if not pyproject_path.exists():
        pyproject_path.write_text(create_pyproject_toml(project_name))

    # Create README.md
    readme_path = project_path / "README.md"
    if not readme_path.exists():
        readme_path.write_text(create_readme(project_name))

    # Create models directory and example
    models_dir = project_path / "models"
    models_dir.mkdir(exist_ok=True)

    example_path = models_dir / "example.py"
    if not example_path.exists():
        example_path.write_text(create_example_model())

    # Create models __init__.py
    models_init = models_dir / "__init__.py"
    if not models_init.exists():
        models_init.write_text('"""Models package."""\n')

    # Create .modelopsignore
    ignore_path = project_path / ".modelopsignore"
    if not ignore_path.exists():
        ignore_path.write_text(create_modelopsignore())

    # Update .gitignore
    gitignore_path = project_path / ".gitignore"
    if gitignore_path.exists():
        # Check if already has modelops-bundle entry
        content = gitignore_path.read_text()
        if ".modelops-bundle/" not in content:
            # Append to existing
            with gitignore_path.open("a") as f:
                f.write(create_gitignore_entry())
    else:
        # Create new .gitignore
        gitignore_path.write_text(create_gitignore_entry().strip() + "\n")