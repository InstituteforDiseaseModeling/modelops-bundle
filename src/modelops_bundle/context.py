"""Project context for managing paths and project discovery."""

from pathlib import Path
from typing import Optional, Union

from modelops_contracts import BundleEnvironment, DEFAULT_ENVIRONMENT
from .constants import MODELOPS_BUNDLE_DIR
from .ignore import IgnoreSpec


class ProjectContext:
    """Manages project root discovery and path resolution."""

    def __init__(self, start_path: Optional[Path] = None, env: Optional[str] = None):
        """Initialize context by finding project root and optionally loading environment.

        Args:
            start_path: Path to start searching for project root
            env: Environment name to load (e.g., 'dev', 'staging', 'prod')
        """
        self.root = self._find_root(start_path or Path.cwd())
        if not self.root:
            raise ValueError(f"Not inside a modelops-bundle project (no {MODELOPS_BUNDLE_DIR} found)")
        self._ignore_spec: Optional[IgnoreSpec] = None
        self._environment: Optional[BundleEnvironment] = None
        self._env_name: Optional[str] = env
    
    @classmethod
    def is_initialized(cls, path: Optional[Path] = None) -> bool:
        """Check if a specific directory is initialized (without traversing up)."""
        target = path or Path.cwd()
        return (target / MODELOPS_BUNDLE_DIR).exists()
    
    @classmethod
    def init(cls, path: Optional[Path] = None) -> 'ProjectContext':
        """Initialize a new project at the given path."""
        target = path or Path.cwd()
        marker = target / MODELOPS_BUNDLE_DIR
        marker.mkdir(exist_ok=True)
        return cls(target)
    
    def _find_root(self, start: Path) -> Optional[Path]:
        """Walk up directory tree to find project root."""
        current = start.resolve()
        
        while current != current.parent:
            if (current / MODELOPS_BUNDLE_DIR).exists():
                return current
            current = current.parent
        
        # Check root directory
        if (current / MODELOPS_BUNDLE_DIR).exists():
            return current
        return None
    
    def resolve(self, path: Union[str, Path]) -> Path:
        """Convert any path to project-relative path."""
        p = Path(path)
        
        if p.is_absolute():
            # Already absolute, make relative to root if inside project
            try:
                return p.relative_to(self.root)
            except ValueError:
                raise ValueError(f"Path {p} is outside project")
        else:
            # Relative path from CWD, convert to project-relative
            absolute = (Path.cwd() / p).resolve()
            try:
                return absolute.relative_to(self.root)
            except ValueError:
                raise ValueError(f"Path {p} is outside project")
    
    def absolute(self, project_path: Union[str, Path]) -> Path:
        """Get absolute path from project-relative path."""
        return self.root / project_path
    
    @property
    def storage_dir(self) -> Path:
        """Get the project storage directory."""
        return self.root / MODELOPS_BUNDLE_DIR
    
    @property
    def config_path(self) -> Path:
        """Get path to config file."""
        from .constants import CONFIG_FILE
        return self.storage_dir / CONFIG_FILE
    
    @property
    def tracked_path(self) -> Path:
        """Get path to tracked files list."""
        from .constants import TRACKED_FILE
        return self.storage_dir / TRACKED_FILE
    
    @property
    def state_path(self) -> Path:
        """Get path to sync state file."""
        from .constants import STATE_FILE
        return self.storage_dir / STATE_FILE
    
    def get_ignore_spec(self) -> IgnoreSpec:
        """Get the ignore specification (memoized)."""
        if self._ignore_spec is None:
            self._ignore_spec = IgnoreSpec(self.root)
        return self._ignore_spec
    
    def should_ignore(self, relpath: Union[str, Path]) -> bool:
        """Check if a path should be ignored.

        Args:
            relpath: Either a project-relative POSIX string or a Path

        Returns:
            True if the path matches ignore patterns
        """
        ignore_spec = self.get_ignore_spec()

        # Convert Path to POSIX string if needed
        if isinstance(relpath, Path):
            relpath = relpath.as_posix()

        return ignore_spec.is_ignored(relpath)

    def get_environment(self, require_storage: bool = False) -> BundleEnvironment:
        """Load and validate environment configuration.

        Args:
            require_storage: Whether storage configuration is required

        Returns:
            BundleEnvironment from ~/.modelops/bundle-env/

        Raises:
            FileNotFoundError: If environment doesn't exist
            ValueError: If environment is missing required components
        """
        if self._environment is None:
            env_name = self._env_name or DEFAULT_ENVIRONMENT

            # This will raise FileNotFoundError if not found
            self._environment = BundleEnvironment.load(env_name)

            # Store the actual environment name we loaded
            self._env_name = env_name

            # Set up storage credentials - this is PART of loading an environment!
            if self._environment.storage:
                self._setup_storage_credentials(self._environment.storage)

            # Validate based on requirements
            if require_storage and not self._environment.storage:
                raise ValueError(f"Environment '{env_name}' has no storage configured")

        return self._environment

    def _setup_storage_credentials(self, storage) -> None:
        """Set environment variables from storage configuration.

        This is automatically called when loading an environment to ensure
        storage credentials are always available when needed.

        Args:
            storage: StorageConfig from the loaded environment
        """
        import os

        # Azure/Azurite storage
        if storage.provider in ("azure", "azurite"):
            if storage.connection_string:
                os.environ["AZURE_STORAGE_CONNECTION_STRING"] = storage.connection_string

        # Future: Add S3/GCS support here
        # elif storage.provider in ("s3", "minio"):
        #     if storage.access_key:
        #         os.environ["AWS_ACCESS_KEY_ID"] = storage.access_key
        #     if storage.secret_key:
        #         os.environ["AWS_SECRET_ACCESS_KEY"] = storage.secret_key

    @property
    def env_name(self) -> Optional[str]:
        """Get the environment name if loaded."""
        return self._env_name