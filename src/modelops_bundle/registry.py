"""Model and target registry for provenance tracking.

This module provides the registry system for tracking models and their
dependencies. The registry is the foundation of the provenance system,
allowing explicit declaration of what files affect model behavior.
"""

from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
import yaml

from .hashing import token_hash, file_hash


class ModelEntry(BaseModel):
    """Registry entry for a model and its dependencies.

    Attributes:
        path: Path to the Python file containing the model
        class_name: Name of the model class
        outputs: List of output names this model produces
        data: List of data file dependencies
        code: List of code file dependencies
        model_digest: Token-based hash of the model file
    """
    path: Path
    class_name: str
    outputs: List[str] = Field(default_factory=list)
    data: List[Path] = Field(default_factory=list)
    code: List[Path] = Field(default_factory=list)
    model_digest: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}

    def compute_digest(self) -> str:
        """Compute the semantic hash of the model code."""
        if self.path.exists():
            return token_hash(self.path)
        return ""

    def validate_dependencies(self) -> List[str]:
        """Validate that all declared dependencies exist.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        if not self.path.exists():
            errors.append(f"Model file not found: {self.path}")

        for data_file in self.data:
            if not data_file.exists():
                errors.append(f"Data dependency not found: {data_file}")

        for code_file in self.code:
            if not code_file.exists():
                errors.append(f"Code dependency not found: {code_file}")

        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "path": str(self.path),
            "class_name": self.class_name,
            "outputs": self.outputs,
            "data": [str(p) for p in self.data],
            "code": [str(p) for p in self.code],
            "model_digest": self.model_digest or self.compute_digest()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelEntry":
        """Create from dictionary (YAML deserialization)."""
        return cls(
            path=Path(data["path"]),
            class_name=data["class_name"],
            outputs=data.get("outputs", []),
            data=[Path(p) for p in data.get("data", [])],
            code=[Path(p) for p in data.get("code", [])],
            model_digest=data.get("model_digest")
        )


class TargetEntry(BaseModel):
    """Registry entry for a calibration target.

    Attributes:
        path: Path to the Python file containing the target
        model_output: Name of the model output this target uses
        observation: Path to the observation data file
        target_digest: Token-based hash of the target file
    """
    path: Path
    model_output: str
    observation: Path
    target_digest: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}

    def compute_digest(self) -> str:
        """Compute the semantic hash of the target code."""
        if self.path.exists():
            return token_hash(self.path)
        return ""

    def compute_observation_digest(self) -> str:
        """Compute the hash of the observation data."""
        if self.observation.exists():
            return file_hash(self.observation)
        return ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "path": str(self.path),
            "model_output": self.model_output,
            "observation": str(self.observation),
            "target_digest": self.target_digest or self.compute_digest()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TargetEntry":
        """Create from dictionary (YAML deserialization)."""
        return cls(
            path=Path(data["path"]),
            model_output=data["model_output"],
            observation=Path(data["observation"]),
            target_digest=data.get("target_digest")
        )


class BundleRegistry(BaseModel):
    """Registry of all models and targets in a bundle.

    This is the central registry that tracks all models, targets,
    and their dependencies for provenance tracking.

    Attributes:
        models: Dictionary of model ID to ModelEntry
        targets: Dictionary of target ID to TargetEntry
        version: Registry format version
    """
    models: Dict[str, ModelEntry] = Field(default_factory=dict)
    targets: Dict[str, TargetEntry] = Field(default_factory=dict)
    version: str = "1.0"

    def add_model(
        self,
        model_id: str,
        path: Path,
        class_name: str,
        data: List[Path] = None,
        code: List[Path] = None,
        outputs: List[str] = None
    ) -> ModelEntry:
        """Add a model to the registry.

        Args:
            model_id: Unique identifier for the model
            path: Path to model file
            class_name: Name of model class
            data: List of data dependencies
            code: List of code dependencies
            outputs: List of output names

        Returns:
            The created ModelEntry
        """
        entry = ModelEntry(
            path=path,
            class_name=class_name,
            data=data or [],
            code=code or [],
            outputs=outputs or []
        )
        self.models[model_id] = entry
        return entry

    def add_target(
        self,
        target_id: str,
        path: Path,
        model_output: str,
        observation: Path
    ) -> TargetEntry:
        """Add a target to the registry.

        Args:
            target_id: Unique identifier for the target
            path: Path to target file
            model_output: Name of model output to use
            observation: Path to observation data

        Returns:
            The created TargetEntry
        """
        entry = TargetEntry(
            path=path,
            model_output=model_output,
            observation=observation
        )
        self.targets[target_id] = entry
        return entry

    def validate(self) -> List[str]:
        """Validate all entries in the registry.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        for model_id, model in self.models.items():
            model_errors = model.validate_dependencies()
            for error in model_errors:
                errors.append(f"Model '{model_id}': {error}")

        for target_id, target in self.targets.items():
            if not target.path.exists():
                errors.append(f"Target '{target_id}': file not found: {target.path}")
            if not target.observation.exists():
                errors.append(f"Target '{target_id}': observation not found: {target.observation}")

        return errors

    def save(self, path: Path) -> None:
        """Save registry to YAML file.

        Args:
            path: Path to save registry.yaml
        """
        data = {
            "version": self.version,
            "models": {
                model_id: model.to_dict()
                for model_id, model in self.models.items()
            },
            "targets": {
                target_id: target.to_dict()
                for target_id, target in self.targets.items()
            }
        }

        with path.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> "BundleRegistry":
        """Load registry from YAML file.

        Args:
            path: Path to registry.yaml

        Returns:
            Loaded BundleRegistry

        Raises:
            FileNotFoundError: If registry file doesn't exist
        """
        if not path.exists():
            raise FileNotFoundError(f"Registry not found: {path}")

        with path.open() as f:
            data = yaml.safe_load(f)

        registry = cls(version=data.get("version", "1.0"))

        # Load models
        for model_id, model_data in data.get("models", {}).items():
            registry.models[model_id] = ModelEntry.from_dict(model_data)

        # Load targets
        for target_id, target_data in data.get("targets", {}).items():
            registry.targets[target_id] = TargetEntry.from_dict(target_data)

        return registry

    def compute_all_digests(self) -> None:
        """Compute and update all digests in the registry."""
        for model in self.models.values():
            model.model_digest = model.compute_digest()

        for target in self.targets.values():
            target.target_digest = target.compute_digest()


__all__ = [
    "ModelEntry",
    "TargetEntry",
    "BundleRegistry",
]