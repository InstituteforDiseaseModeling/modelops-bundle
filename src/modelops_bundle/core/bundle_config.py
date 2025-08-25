# bundle_config.py
from __future__ import annotations
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field, model_validator
import re

_LAYER_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")

class BundleConfig(BaseModel):
    """
    A configuration for the current project's bundle.
    """
    schema_version: Literal[1] = 1

    name: str = Field(..., min_length=1)
    # repository ONLY (no :tag or @digest)
    registry_ref: str = Field(..., examples=["ghcr.io/<org>/epi_models", "localhost:5555/epi_models"])

    # Optional on purpose; CLI must error if both this and --tag are absent
    default_tag: Optional[str] = Field(None, description="Default tag (if omitted, CLI must require --tag)")

    # Optional knobs; keep if you want typed artifacts, otherwise drop them.
    artifact_type: Optional[str] = Field(
        None, description="Manifest artifactType; set if you want a custom type"
    )
    config_media_type: Optional[str] = Field(
        None, description="Media type for bundle.json if you publish a config descriptor"
    )
    annotations_namespace: str = Field(
        "io.modelops", description="Namespace for custom descriptor annotations"
    )

    # Your bundle-layer vocabulary (empty by default)
    layers: List[str] = Field(default_factory=list, description="Allowed bundle-layer names")

    # role → allowed layers (empty by default)
    roles: Dict[str, List[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self):
        # 1) registry_ref must not include :tag or @digest (allow ports in host)
        last = self.registry_ref.rsplit("/", 1)[-1]
        if ":" in last or "@" in last:
            raise ValueError("registry_ref must be a repository ONLY (no :tag or @digest)")

        # 2) layers: unique and simple if provided
        if len(set(self.layers)) != len(self.layers):
            raise ValueError("Duplicate layer names in 'layers'.")
        bad = [x for x in self.layers if not _LAYER_NAME.match(x)]
        if bad:
            raise ValueError(f"Invalid layer names: {bad} (must match {_LAYER_NAME.pattern})")

        # 3) roles may only reference declared layers
        layer_set = set(self.layers)
        unknown = sorted({ly for lst in self.roles.values() for ly in lst if ly not in layer_set})
        if unknown:
            raise ValueError(f"'roles' references unknown layers: {unknown}")
        return self

    # convenience
    def role_layers(self, role: str) -> set[str]:
        return set(self.roles.get(role, []))

    def is_allowed(self, role: str, layer: str) -> bool:
        return layer in self.roles.get(role, [])

