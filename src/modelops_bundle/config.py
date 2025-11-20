"""Bundle configuration helpers."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class AutoCodeConfig:
    """Configuration controlling automatic code dependency discovery."""

    code_roots: List[str] = field(default_factory=lambda: ["src"])
    import_mode: str = "package"  # "package" or "files"
    ignore: List[str] = field(default_factory=list)


def load_bundle_config(root: Path) -> AutoCodeConfig:
    """Load bundle configuration from .modelops-bundle/config.yaml if present."""

    cfg_path = root / ".modelops-bundle" / "config.yaml"
    if not cfg_path.exists():
        return AutoCodeConfig()

    try:
        data = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return AutoCodeConfig()

    autocode = data.get("auto_code", data)
    return AutoCodeConfig(
        code_roots=autocode.get("code_roots", ["src"]),
        import_mode=autocode.get("import_mode", "package"),
        ignore=autocode.get("ignore", []),
    )
