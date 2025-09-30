"""Manifest generation for model bundles.

Generates manifest.json describing the bundle contents
without executing model code, including environment tracking
and deterministic composite digests for provenance.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging
import tomllib

try:
    from modelops_contracts import EnvironmentDigest
except ImportError:
    # Fallback for development
    EnvironmentDigest = None

from .hashing import compute_file_digest, compute_composite_digest

logger = logging.getLogger(__name__)


def capture_environment() -> Optional[Dict[str, Any]]:
    """Capture current execution environment.

    Returns:
        Environment dictionary with digest, or None if not available
    """
    if EnvironmentDigest is None:
        return None

    # Capture basic environment
    env = EnvironmentDigest.capture_current()

    # Try to get key package versions
    packages = {}
    try:
        import importlib.metadata
        for pkg in ["numpy", "scipy", "pandas", "polars", "dask"]:
            try:
                version = importlib.metadata.version(pkg)
                packages[pkg] = version
            except importlib.metadata.PackageNotFoundError:
                pass
    except ImportError:
        pass

    # Add packages if found
    if packages:
        env = env.with_dependencies(packages)

    return env.to_json()


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file.

    Args:
        file_path: Path to file

    Returns:
        Hex-encoded SHA256 hash
    """
    # Use simple file hashing for all files now (no token hashing)
    return compute_file_digest(file_path)


def read_pyproject_config() -> Optional[Dict[str, Any]]:
    """Read modelops-bundle configuration from pyproject.toml.
    
    Returns:
        Dictionary of configuration or None if not found
    """
    pyproject_path = Path.cwd() / "pyproject.toml"
    if not pyproject_path.exists():
        return None
    
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    
    # Look for [tool.modelops-bundle] section
    return data.get("tool", {}).get("modelops-bundle", {})


def resolve_file_patterns(patterns: List[str], base_path: Path = None) -> List[Path]:
    """Resolve glob patterns to actual files.
    
    Args:
        patterns: List of glob patterns
        base_path: Base directory for patterns (defaults to cwd)
    
    Returns:
        List of resolved file paths
    """
    if base_path is None:
        base_path = Path.cwd()
    
    files = set()
    for pattern in patterns:
        # Handle absolute patterns
        if pattern.startswith("/"):
            pattern_path = Path(pattern)
            if pattern_path.exists():
                files.add(pattern_path)
        else:
            # Relative patterns
            for match in base_path.glob(pattern):
                if match.is_file():
                    files.add(match)
    
    return sorted(files)


def build_manifest(
    models: Optional[List[Dict[str, Any]]] = None,
    output_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Build a manifest describing the bundle contents.

    Includes environment tracking and composite digest with
    proper domain separation for provenance.

    Args:
        models: Optional list of model configurations.
                If not provided, will auto-discover.
        output_path: Optional path to write manifest.json

    Returns:
        Manifest dictionary with environment and composite digest
    """
    manifest = {
        "schema_version": "1.1",  # Updated for environment tracking
        "models": {},
        "files": {},
        "bundle_digest": "",
        "environment": None
    }
    
    # Read configuration from pyproject.toml if it exists
    config = read_pyproject_config()
    if config:
        manifest["config"] = config
    
    # If models not provided, try to discover them
    if models is None:
        from .discovery import discover_models # TODO fix
        discovered = discover_models()
        models = []
        for model_info in discovered:
            models.append({
                "id": model_info["full_path"],
                "class": model_info["full_path"],
                "file": model_info["file_path"],
                "has_simulate": model_info.get("has_simulate", False),
                "has_parameters": model_info.get("has_parameters", False)
            })
    
    # Process models from config if available
    if config and "models" in config:
        # Override with configured models
        models = config["models"]
    
    # Build model entries
    all_files = set()
    for model in models:
        model_id = model.get("id", model["class"])
        
        # Resolve files for this model
        if "files" in model:
            model_files = resolve_file_patterns(model["files"])
        elif "file" in model:
            model_files = [Path(model["file"])]
        else:
            # Default: just the file containing the class
            class_path = model["class"]
            if ":" in class_path:
                module_path = class_path.split(":")[0]
                file_path = Path(module_path.replace(".", "/") + ".py")
                if file_path.exists():
                    model_files = [file_path]
                else:
                    model_files = []
            else:
                model_files = []
        
        # Add to manifest
        manifest["models"][model_id] = {
            "class": model["class"],
            "files": [str(f) for f in model_files]
        }
        
        # Track all files
        all_files.update(model_files)
    
    # Capture environment
    env_data = capture_environment()
    if env_data:
        manifest["environment"] = env_data

    # Compute file hashes and build components for composite digest
    components = []
    for file_path in sorted(all_files):
        if file_path.exists():
            file_hash_val = compute_file_hash(file_path)
            manifest["files"][str(file_path)] = {
                "sha256": file_hash_val,
                "size": file_path.stat().st_size
            }

            # Classify file type for composite digest
            if str(file_path).startswith("src/models/"):
                component_type = "MODEL_CODE"
            elif file_path.suffix in [".csv", ".json", ".parquet", ".yaml", ".yml"]:
                component_type = "DATA"
            else:
                component_type = "CODE_DEP"

            components.append((component_type, str(file_path), file_hash_val))

    # Compute composite bundle digest with domain separation
    if components:
        env_digest = env_data["digest"] if env_data else "no-env"
        bundle_hash = compute_composite_digest(components, env_digest)
        manifest["bundle_digest"] = f"bundle:{bundle_hash}"
    
    # Write manifest if output path provided
    if output_path:
        with open(output_path, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        logger.info(f"Wrote manifest to {output_path}")
    
    return manifest


def load_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load manifest from file.
    
    Args:
        path: Path to manifest.json (defaults to cwd/manifest.json)
    
    Returns:
        Manifest dictionary
    
    Raises:
        FileNotFoundError: If manifest not found
    """
    if path is None:
        path = Path.cwd() / "manifest.json"
    
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    
    with open(path, "r") as f:
        return json.load(f)
