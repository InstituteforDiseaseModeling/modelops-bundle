"""Manifest generation for model bundles.

Generates manifest.json describing the bundle contents
without executing model code.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging
import tomllib


logger = logging.getLogger(__name__)


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file.
    
    Args:
        file_path: Path to file
    
    Returns:
        Hex-encoded SHA256 hash
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


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
    
    Args:
        models: Optional list of model configurations.
                If not provided, will auto-discover.
        output_path: Optional path to write manifest.json
    
    Returns:
        Manifest dictionary
    """
    manifest = {
        "schema_version": "1.0",
        "models": {},
        "files": {},
        "bundle_digest": ""
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
    
    # Compute file hashes
    for file_path in sorted(all_files):
        if file_path.exists():
            file_hash = compute_file_hash(file_path)
            manifest["files"][str(file_path)] = {
                "sha256": file_hash,
                "size": file_path.stat().st_size
            }
    
    # Compute bundle digest from file hashes
    if manifest["files"]:
        # Sort files and concatenate hashes
        digest_input = "|".join(
            f"{path}:{info['sha256']}"
            for path, info in sorted(manifest["files"].items())
        )
        bundle_hash = hashlib.sha256(digest_input.encode()).hexdigest()
        manifest["bundle_digest"] = f"sha256:{bundle_hash}"
    
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
