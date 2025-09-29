"""CLI for modelops-bundle."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.console import Console
from rich.table import Table
from .context import ProjectContext
from .core import (
    BundleConfig,
    ChangeType,
    TrackedFiles,
)
from .utils import humanize_size, humanize_date, format_iso_date, format_storage_display
from .ops import (
    load_config,
    load_state,
    load_tracked,
    pull as ops_pull,
    push as ops_push,
    save_config,
    save_tracked,
)
from .oras import OrasAdapter
from .working_state import TrackedWorkingState
from .core import RemoteStatus, RemoteState
from .errors import MissingIndexError, NetworkError, AuthError, NotFoundError, UnsupportedArtifactError, TagMovedError
from .manifest import build_manifest, load_manifest


app = typer.Typer(help="ModelOps-Bundle - OCI artifact-based model bundle synchronization")
console = Console()


def _validate_environment_for_registry(registry_ref: str) -> None:
    """Validate environment settings to prevent localhost vs cloud registry confusion.

    Args:
        registry_ref: Full registry reference (e.g., "myacr.azurecr.io/repo")

    Raises:
        typer.Exit: If environment is misconfigured
    """
    import os

    registry_host = registry_ref.split('/')[0]
    insecure_env = os.environ.get("MODELOPS_BUNDLE_INSECURE", "false").lower()
    is_insecure = insecure_env in ("true", "1", "yes")

    is_cloud_registry = (
        '.azurecr.io' in registry_host or
        '.gcr.io' in registry_host or
        'public.ecr.aws' in registry_host or
        'registry.hub.docker.com' in registry_host
    )

    if is_cloud_registry and is_insecure:
        console.print(f"[red]🚨 CONFIGURATION ERROR[/red]")
        console.print(f"   Insecure mode is enabled for cloud registry: [bold]{registry_host}[/bold]")
        console.print(f"   This causes HTTP (not HTTPS) connections and authentication failures.")
        console.print()
        console.print(f"[green]SOLUTION:[/green]")
        console.print(f"   unset MODELOPS_BUNDLE_INSECURE")
        console.print(f"   # or")
        console.print(f"   export MODELOPS_BUNDLE_INSECURE=false")
        console.print()
        console.print(f"[dim]💡 Insecure mode should only be used for localhost registries[/dim]")
        raise typer.Exit(1)


def _get_oras_adapter(config: BundleConfig, ctx: ProjectContext) -> OrasAdapter:
    """Create OrasAdapter with authentication based on environment configuration."""
    from .auth import get_auth_provider

    # Validate environment settings before creating adapter
    _validate_environment_for_registry(config.registry_ref)

    auth_provider = get_auth_provider(config.registry_ref)
    return OrasAdapter(auth_provider=auth_provider, registry_ref=config.registry_ref)


def require_project_context(env: Optional[str] = None, require_storage: bool = False) -> ProjectContext:
    """Ensure project is initialized and return context with optional environment.

    Args:
        env: Environment name to load (defaults to 'dev')
        require_storage: Whether storage must be configured

    Returns:
        ProjectContext with environment loaded if requested
    """
    try:
        ctx = ProjectContext(env=env)

        # If env was specified or storage is required, validate environment
        if env is not None or require_storage:
            try:
                environment = ctx.get_environment(require_storage=require_storage)
                # Print which environment we're using
                env_name = ctx.env_name or "dev"
                console.print(f"[green]✓[/green] Using environment '{env_name}'")
            except FileNotFoundError:
                env_name = env or "dev"
                console.print(f"[red]✗[/red] Environment '{env_name}' not found")
                console.print("Available environments can be created with 'mops infra up'")
                raise typer.Exit(1)
            except ValueError as e:
                console.print(f"[red]✗[/red] {e}")
                raise typer.Exit(1)

        return ctx
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        console.print()
        console.print("[dim]Hint: Check you're in the right directory[/dim]")
        console.print()
        console.print("To initialize a new project, run:")
        console.print("  [cyan]REGISTRY_URL=<registry> uv run modelops-bundle init[/cyan]")
        raise typer.Exit(1)


def display_remote_status(status: "RemoteStatus", registry_ref: str, reference: str = "latest") -> None:
    """Display remote status with appropriate messaging."""
    from .core import RemoteStatus
    
    if status == RemoteStatus.AVAILABLE:
        # Don't display anything for available status (normal case)
        return
    elif status == RemoteStatus.EMPTY:
        console.print(f"[yellow]Remote registry has no content at {registry_ref}:{reference}[/yellow]")
    elif status == RemoteStatus.UNREACHABLE:
        console.print(f"[red]Cannot connect to registry at {registry_ref}[/red]")
        console.print("[dim]Check your network connection and registry URL[/dim]")
    elif status == RemoteStatus.AUTH_FAILED:
        console.print(f"[red]Authentication failed for {registry_ref}[/red]")
        console.print("[dim]Check your credentials or use 'docker login'[/dim]")
    elif status == RemoteStatus.UNKNOWN_ERROR:
        console.print(f"[red]Error accessing registry at {registry_ref}:{reference}[/red]")
        console.print("[dim]Run with DEBUG=1 for more details[/dim]")


def get_remote_state_with_status(
    oras: "OrasAdapter",
    registry_ref: str, 
    reference: str = "latest"
) -> Tuple[Optional["RemoteState"], "RemoteStatus"]:
    """Get remote state and status, handling all error cases cleanly."""
    from .core import RemoteStatus
    
    try:
        remote_state = oras.get_remote_state(registry_ref, reference)
        return remote_state, RemoteStatus.AVAILABLE
    except NotFoundError:
        return None, RemoteStatus.EMPTY
    except AuthError:
        return None, RemoteStatus.AUTH_FAILED
    except NetworkError:
        return None, RemoteStatus.UNREACHABLE
    except UnsupportedArtifactError:
        return None, RemoteStatus.UNKNOWN_ERROR
    except Exception as e:
        import os
        if os.environ.get("DEBUG"):
            print(f"DEBUG: Unexpected exception in get_remote_state_with_status: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
        return None, RemoteStatus.UNKNOWN_ERROR


def require_remote(
    oras: "OrasAdapter",
    registry_ref: str,
    reference: str = "latest"
) -> "RemoteState":
    """Require remote state to be available, exit with helpful message if not."""
    from .core import RemoteStatus
    
    remote_state, status = get_remote_state_with_status(oras, registry_ref, reference)
    
    if status == RemoteStatus.AVAILABLE and remote_state:
        return remote_state
    
    # Display the error
    display_remote_status(status, registry_ref, reference)
    
    # Add helpful hints based on status
    if status == RemoteStatus.EMPTY:
        console.print()
        console.print(f"[dim]Hint: Did you mistype the registry name or tag?")
        console.print()
        console.print("To push initial content, run:")
        console.print(f"  [cyan]uv run modelops-bundle push[/cyan]")
    elif status == RemoteStatus.UNREACHABLE:
        console.print()
        console.print("If using a local registry, ensure it's running:")
        console.print("  [cyan]make up[/cyan]")
    
    raise typer.Exit(1)


@app.command()
def init(
    path: Optional[str] = typer.Argument(None, help="Directory to initialize (default: current directory)"),
    env: str = typer.Option("local", "--env", "-e", help="Environment to use (local, dev, staging, prod)"),
    tag: str = typer.Option("latest", help="Default tag"),
    threshold_mb: int = typer.Option(50, "--threshold", help="Size threshold in MB for blob storage"),
):
    """Initialize a bundle project.

    Similar to 'uv init', this command can:
    - Initialize the current directory (no path argument)
    - Create and initialize a new directory (with path argument)

    The environment determines both registry and storage configuration,
    loaded from ~/.modelops/bundle-env/{env}.yaml

    Examples:
        # Initialize with local environment (localhost + Azurite)
        mops-bundle init my-model --env local

        # Initialize with dev environment (Azure ACR + blob storage)
        mops-bundle init my-model --env dev

        # Initialize current directory
        mops-bundle init --env local
    """
    import os
    from .bundle_service import initialize_bundle
    from .templates import create_project_templates

    # Determine target directory and project name
    if path:
        # Path provided - create new directory
        target_dir = Path(path).resolve()
        project_name = target_dir.name

        if target_dir.exists():
            # Check if already initialized
            if (target_dir / "pyproject.toml").exists():
                console.print(f"[red]error:[/red] Project is already initialized in `{target_dir}` (`pyproject.toml` file exists)")
                raise typer.Exit(1)
            if (target_dir / ".modelops-bundle").exists():
                console.print(f"[red]error:[/red] Project is already initialized in `{target_dir}` (`.modelops-bundle` directory exists)")
                raise typer.Exit(1)
        else:
            # Create the directory
            target_dir.mkdir(parents=True)
            console.print(f"[green]✓[/green] Created project directory: {target_dir}")

        create_templates = True
        # Change to the new directory for initialization
        original_dir = Path.cwd()
        os.chdir(target_dir)
    else:
        # No path - use current directory
        target_dir = Path.cwd()
        project_name = target_dir.name
        create_templates = False

        # Check if already initialized
        if (target_dir / "pyproject.toml").exists():
            console.print(f"[red]error:[/red] Project is already initialized in `{target_dir}` (`pyproject.toml` file exists)")
            raise typer.Exit(1)
        if ProjectContext.is_initialized():
            console.print(f"[red]error:[/red] Project is already initialized in `{target_dir}` (`.modelops-bundle` directory exists)")
            raise typer.Exit(1)

    try:
        # ALL business logic is in the service layer - loads from environment
        config = initialize_bundle(
            project_name=project_name,
            env_name=env,
            tag=tag,
            threshold_mb=threshold_mb,
        )

        # Initialize project context and save config
        ctx = ProjectContext.init()
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)

        # Create project templates if new directory
        if create_templates:
            create_project_templates(target_dir, project_name)
            console.print(f"[green]✓[/green] Created project templates")

        console.print(f"[green]✓[/green] Initialized project `{project_name}` with environment '{env}'")
        console.print(f"[dim]Registry: {config.registry_ref}[/dim]")

        # Show storage info if configured
        if config.storage and config.storage.provider:
            console.print(f"[dim]Storage: {config.storage.provider} ({config.storage.container})[/dim]")

        # Show next steps for new projects
        if create_templates:
            console.print("\n[dim]To get started:[/dim]")
            console.print(f"  cd {path}")
            console.print("  mops-bundle discover --interactive --save")
            console.print("  mops-bundle manifest")

    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    finally:
        # Change back to original directory if we changed it
        if path and 'original_dir' in locals():
            os.chdir(original_dir)


@app.command()
def discover(
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive mode to select models"),
    save: bool = typer.Option(False, "--save", help="Save discovered models to pyproject.toml"),
    all: bool = typer.Option(False, "--all", help="Show all classes, not just models"),
):
    """Discover models in the current project."""
    # Discover models
    console.print("[bold]Scanning for models...[/bold]")
    models = discover_models()

    if not models:
        console.print("[yellow]No models found[/yellow]")
        console.print("[dim]Models should have 'simulate' or 'parameters' methods[/dim]")
        return

    # Filter to actual models unless --all
    if not all:
        models = [m for m in models if m.get("has_simulate") or m.get("has_parameters")]

    if not models:
        console.print("[yellow]No models with simulate/parameters methods found[/yellow]")
        console.print("[dim]Use --all to see all classes[/dim]")
        return

    if interactive:
        # Interactive selection
        from rich.prompt import Confirm
        console.print(f"\n[bold]Found {len(models)} models:[/bold]")

        selected_models = []
        for model in models:
            console.print(f"\n[cyan]{model['full_path']}[/cyan]")
            console.print(f"  File: {model['file_path']}")
            console.print(f"  Line: {model['line_number']}")
            if model.get('has_simulate'):
                console.print("  ✓ Has simulate method")
            if model.get('has_parameters'):
                console.print("  ✓ Has parameters method")

            if Confirm.ask("  Include this model?", default=True):
                selected_models.append(model)

        models = selected_models
        if not models:
            console.print("[yellow]No models selected[/yellow]")
            return
    else:
        # Non-interactive: show table
        table = Table(title=f"Discovered Models ({len(models)})")
        table.add_column("Model", style="cyan")
        table.add_column("File")
        table.add_column("Line", justify="right")
        table.add_column("Methods")

        for model in models:
            methods = []
            if model.get('has_simulate'):
                methods.append("simulate")
            if model.get('has_parameters'):
                methods.append("parameters")

            table.add_row(
                model['full_path'],
                str(model['file_path']),
                str(model['line_number']),
                ", ".join(methods) if methods else "[dim]none[/dim]"
            )

        console.print(table)

    # Save to pyproject.toml if requested
    if save:
        pyproject_path = Path("pyproject.toml")
        if not pyproject_path.exists():
            console.print("[red]✗[/red] No pyproject.toml found")
            console.print("[dim]Run 'modelops-bundle init --with-template' to create one[/dim]")
            return

        # Read existing content
        import tomllib
        import toml

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        # Ensure tool.modelops-bundle section exists
        if "tool" not in data:
            data["tool"] = {}
        if "modelops-bundle" not in data["tool"]:
            data["tool"]["modelops-bundle"] = {}

        # Add models
        if "models" not in data["tool"]["modelops-bundle"]:
            data["tool"]["modelops-bundle"]["models"] = []

        existing_ids = {m.get("id", m["class"]) for m in data["tool"]["modelops-bundle"]["models"]}

        added = 0
        for model in models:
            model_id = model['full_path']
            if model_id not in existing_ids:
                # Determine files pattern for this model
                file_path = Path(model['file_path'])
                if file_path.parent == Path("."):
                    files_pattern = str(file_path)
                else:
                    # Use directory pattern
                    files_pattern = f"{file_path.parent}/**/*.py"

                model_entry = {
                    "id": model_id,
                    "class": model['full_path'],
                    "files": [files_pattern]
                }
                data["tool"]["modelops-bundle"]["models"].append(model_entry)
                added += 1

        if added > 0:
            # Write back (we need toml library for writing)
            try:
                import toml
                with open(pyproject_path, "w") as f:
                    toml.dump(data, f)
                console.print(f"[green]✓[/green] Added {added} models to pyproject.toml")
            except ImportError:
                console.print("[yellow]Warning: Could not save (toml package not installed)[/yellow]")
                console.print("[dim]Install with: pip install toml[/dim]")
        else:
            console.print("[dim]All models already in pyproject.toml[/dim]")


# TODO: Remove this commented-out manifest generation command entirely
# This was causing CLI conflicts with the registry inspection manifest command.
# In the future, manifest.json should be generated automatically during push operations,
# not as a manual CLI command. The push command should examine all registered/added models
# and create the manifest automatically.

# @app.command()
# def manifest(
#     output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (default: manifest.json)"),
#     check: bool = typer.Option(False, "--check", help="Check if manifest is up-to-date"),
# ):
#     """Generate manifest.json for the bundle."""
#     output_path = output or Path("manifest.json")
#
#     # Build manifest
#     console.print("[bold]Generating manifest...[/bold]")
#     try:
#         manifest_data = build_manifest(output_path=output_path if not check else None)
#
#         if check:
#             # Check if existing manifest matches
#             if output_path.exists():
#                 existing = load_manifest(output_path)
#                 if existing.get("bundle_digest") == manifest_data.get("bundle_digest"):
#                     console.print("[green]✓[/green] Manifest is up-to-date")
#                 else:
#                     console.print("[yellow]⚠[/yellow] Manifest is out of date")
#                     console.print(f"  Current: {existing.get('bundle_digest', 'none')}")
#                     console.print(f"  Expected: {manifest_data.get('bundle_digest', 'none')}")
#                     raise typer.Exit(1)
#             else:
#                 console.print("[yellow]⚠[/yellow] No manifest.json found")
#                 raise typer.Exit(1)
#         else:
#             # Display summary
#             console.print(f"[green]✓[/green] Generated manifest.json")
#             console.print(f"  Bundle digest: {manifest_data.get('bundle_digest', 'none')[:16]}...")
#             console.print(f"  Models: {len(manifest_data.get('models', {}))}")
#             console.print(f"  Files: {len(manifest_data.get('files', {}))}")
#
#     except Exception as e:
#         console.print(f"[red]✗[/red] Failed to generate manifest: {e}")
#         raise typer.Exit(1)


@app.command()
def add(
    files: List[Path] = typer.Argument(..., help="Files to track"),
    force: bool = typer.Option(False, "--force", help="Add ignored files anyway"),
):
    """Add files to tracking."""
    ctx = require_project_context()
    
    # Load tracked files
    tracked = load_tracked(ctx)
    
    # Add files
    added = []
    skipped_ignored = []
    
    for file in files:
        # Check if file exists (handles both absolute and relative paths)
        file_path = Path(file)
        if not file_path.exists():
            console.print(f"[red]✗[/red] File not found: {file}")
            continue

        # Handle directories - expand to all files within
        if file_path.is_dir():
            # Find all files in directory recursively
            dir_files = []
            for item in file_path.rglob("*"):
                if item.is_file():
                    # Store as project-relative path
                    try:
                        rel_path = ctx.resolve(item)
                        # Check if file is ignored (unless --force is used)
                        if not force and ctx.should_ignore(rel_path):
                            skipped_ignored.append(rel_path)
                            continue
                        tracked.add(rel_path)
                        added.append(rel_path)
                        dir_files.append(rel_path)
                    except ValueError:
                        # File outside project, skip
                        continue

            if not dir_files:
                console.print(f"[yellow]⚠[/yellow] No files found in directory: {file}")
            continue

        # Handle regular files
        # Store as project-relative path
        rel_path = ctx.resolve(file)

        # Check if file is ignored (unless --force is used)
        if not force and ctx.should_ignore(rel_path):
            console.print(f"[yellow]⚠[/yellow] The following path is ignored by .modelopsignore:")
            console.print(f"  {rel_path}")
            skipped_ignored.append(rel_path)
            continue

        tracked.add(rel_path)
        added.append(rel_path)
    
    # Save
    save_tracked(tracked, ctx)
    
    # Display results
    if added:
        console.print(f"[green]✓[/green] Tracking {len(added)} files:")
        for file in added:
            console.print(f"  [green]+[/green] {file}")
    
    if skipped_ignored:
        console.print(f"\n[dim]Hint: Use --force to add ignored files anyway.[/dim]")
    
    if not added and not skipped_ignored:
        console.print("[yellow]No files added[/yellow]")


@app.command()
def remove(
    files: List[Path] = typer.Argument(..., help="Files to untrack"),
    rm: bool = typer.Option(False, "--rm", help="Also delete the files from disk"),
):
    """Remove files from tracking."""
    ctx = require_project_context()
    
    # Load tracked files
    tracked = load_tracked(ctx)
    
    # Remove files
    removed = []
    deleted = []
    not_tracked = []
    
    for file in files:
        # Convert to project-relative path
        try:
            rel_path = ctx.resolve(file)
            if str(rel_path) in tracked.files:
                tracked.remove(rel_path)
                removed.append(rel_path)
                
                # Delete file if --rm flag is set
                if rm:
                    abs_path = ctx.absolute(rel_path)
                    if abs_path.exists():
                        abs_path.unlink()
                        deleted.append(rel_path)
            else:
                # File not tracked - collect for error message
                not_tracked.append(file)
        except ValueError:
            # File outside project
            not_tracked.append(file)
    
    # Error if any files weren't tracked (match git behavior)
    if not_tracked:
        console.print(f"[red]✗[/red] pathspec '{not_tracked[0]}' did not match any tracked files")
        raise typer.Exit(1)
    
    # Save
    save_tracked(tracked, ctx)
    
    # Display results
    if removed:
        if rm and deleted:
            console.print(f"[green]✓[/green] Untracked and deleted {len(deleted)} files:")
            for file in deleted:
                console.print(f"  [red]✗[/red] {file} (deleted)")
            # Show files that were untracked but not deleted (didn't exist)
            not_deleted = set(removed) - set(deleted)
            if not_deleted:
                for file in not_deleted:
                    console.print(f"  [red]-[/red] {file} (untracked, file didn't exist)")
        else:
            console.print(f"[green]✓[/green] Untracked {len(removed)} files:")
            for file in removed:
                console.print(f"  [red]-[/red] {file}")


@app.command()
def status(
    untracked: bool = typer.Option(False, "-u", "--untracked", help="Show untracked files"),
    untracked_only: bool = typer.Option(False, "--untracked-only", help="Show only untracked files"),
    include_ignored: bool = typer.Option(False, "--include-ignored", help="Include ignored files"),
):
    """Show bundle status."""
    ctx = require_project_context()
    
    try:
        config = load_config(ctx)
        tracked = load_tracked(ctx)
        state = load_state(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    # Display bundle info (unless --untracked-only)
    if not untracked_only:
        console.print(f"[bold]Bundle:[/bold] {config.registry_ref}:{config.default_tag}")
        console.print(f"[bold]Tracked files:[/bold] {len(tracked.files)}")
    
    if not tracked.files and not untracked_only:
        console.print("\n[yellow]No tracked files. Use 'add' to track files.[/yellow]")
        # Still show untracked if requested
        if not untracked:
            return
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Try to get remote state
    adapter = _get_oras_adapter(config, ctx)
    remote, remote_status = get_remote_state_with_status(adapter, config.registry_ref, config.default_tag)
    
    # Display remote status if not available (but be gentle for status command)
    if not untracked_only:
        from .core import RemoteStatus
        if remote_status == RemoteStatus.UNREACHABLE:
            # For status, just note that remote check is skipped
            console.print("[yellow]Remote status unavailable (working offline)[/yellow]")
        elif remote_status == RemoteStatus.EMPTY:
            # Nothing pushed yet - this is normal for new projects
            console.print("[dim]No bundles pushed to registry yet[/dim]")
        elif remote_status == RemoteStatus.AUTH_FAILED:
            # Authentication issue - worth mentioning but not alarming
            console.print(f"[yellow]Registry authentication required for {config.registry_ref}[/yellow]")
            console.print("[dim]Use 'docker login' if you need to access the registry[/dim]")
        elif remote_status != RemoteStatus.AVAILABLE:
            # Other errors - show but less alarmingly for status command
            console.print(f"[dim]Registry not accessible yet ({config.registry_ref})[/dim]")
            console.print("[dim]This is normal before your first push[/dim]")
    
    # Get status summary
    summary = working_state.get_status(remote, state)
    
    # Show tracked files table (unless --untracked-only)
    if not untracked_only and remote and summary:
        
        # Create status table
        table = Table(title="File Status")
        table.add_column("File", style="cyan")
        table.add_column("Status")
        table.add_column("Size", justify="right")
        # Show storage column if using blob storage or explicit mode
        if config.storage.uses_blob_storage or config.storage.mode != "auto":
            table.add_column("Storage", style="dim")
        
        # Use summary for a cleaner display
        status_map = {
            ChangeType.UNCHANGED: "[green]✓[/green] unchanged",
            ChangeType.ADDED_LOCAL: "[green]+[/green] new",
            ChangeType.ADDED_REMOTE: "[blue]↓[/blue] remote only (untracked)",
            ChangeType.MODIFIED_LOCAL: "[yellow]Δ[/yellow] modified locally",
            ChangeType.MODIFIED_REMOTE: "[blue]↓[/blue] modified remotely",
            ChangeType.DELETED_LOCAL: "[red]−[/red] deleted locally",
            ChangeType.DELETED_REMOTE: "[blue]×[/blue] deleted remotely",
            ChangeType.CONFLICT: "[red]⚠[/red] conflict",
        }
        
        # Build display from summary's changed_files and other lists
        all_items = []
        
        # Add local-only files
        for file_info in summary.local_only_files:
            all_items.append((file_info.path, ChangeType.ADDED_LOCAL, file_info))
        
        # Add remote-only files  
        for file_info in summary.remote_only_files:
            all_items.append((file_info.path, ChangeType.ADDED_REMOTE, file_info))
        
        # Add changed files
        for row in summary.changed_files:
            all_items.append((row.path, row.change, row.local or row.remote))
        
        # Add unchanged files (if not too many)
        if summary.unchanged <= 10:
            diff = working_state.compute_diff(remote, state)
            for change in diff.changes:
                if change.change_type == ChangeType.UNCHANGED:
                    all_items.append((change.path, ChangeType.UNCHANGED, change.local))
        
        # Try to get storage info from remote if available
        storage_info = {}
        if remote:
            try:
                # Try to get index for storage info  
                latest_digest = adapter.resolve_tag_to_digest(config.registry_ref, config.default_tag)
                from .storage_models import BundleIndex
                index = adapter.get_index(config.registry_ref, latest_digest)
                for file_path, entry in index.files.items():
                    storage_info[file_path] = format_storage_display(
                        entry.storage, 
                        config=config,
                        entry=entry
                    )
            except:
                pass
        
        # Determine storage for local files based on policy
        from .storage_models import StorageType
        
        for path, change_type, file_info in sorted(all_items):
            # Determine storage location
            storage = "-"
            if change_type not in [ChangeType.DELETED_LOCAL, ChangeType.DELETED_REMOTE]:
                if path in storage_info:
                    # Use remote storage info if available
                    storage = storage_info[path]
                elif file_info:
                    # For local files, classify based on policy
                    storage_type, _ = config.storage.classify(Path(path), file_info.size)
                    storage = format_storage_display(storage_type, config=config)
            
            # Build row data
            row_data = [
                path,
                status_map.get(change_type, str(change_type)),
                humanize_size(file_info.size) if file_info else "-"
            ]
            if config.storage.uses_blob_storage or config.storage.mode != "auto":
                row_data.append(storage)
            
            # Always add row, even for deleted files where file_info is None
            table.add_row(*row_data)
        
        console.print("\n", table)
        
        # Show hint about remote-only files
        if summary.added_remote > 0:
            console.print(f"\n[dim]Tip: Push will prune {summary.added_remote} remote-only (untracked) file{'s' if summary.added_remote != 1 else ''} from the manifest[/dim]")
        
        # Show summary line
        if summary.unchanged > 10:
            console.print(f"\n[dim]Plus {summary.unchanged} unchanged files[/dim]")
    elif not untracked_only and not remote:
        # No remote, but we can still compare against last synced state
        if state and state.last_synced_files:
            console.print("\n[bold]Local changes (compared to last sync):[/bold]")

            # Compare current files against last synced state
            changes_found = False
            for path, file_info in working_state.files.items():
                last_digest = state.last_synced_files.get(path)
                if not last_digest:
                    # New file since last sync
                    console.print(f"  [green]+[/green] {path} ({humanize_size(file_info.size)}) - new")
                    changes_found = True
                elif file_info.digest != last_digest:
                    # Modified since last sync
                    console.print(f"  [yellow]Δ[/yellow] {path} ({humanize_size(file_info.size)}) - modified")
                    changes_found = True
                else:
                    # Unchanged - show in dim
                    console.print(f"  [dim]{path} ({humanize_size(file_info.size)})[/dim]")

            # Check for deleted files
            for path, digest in state.last_synced_files.items():
                if path not in working_state.files:
                    console.print(f"  [red]−[/red] {path} - deleted")
                    changes_found = True

            if not changes_found:
                console.print("[dim]  No changes since last sync[/dim]")
        else:
            # No sync history, just show local files
            console.print("\n[bold]Local files:[/bold]")
            for path, file_info in working_state.files.items():
                console.print(f"  {path} ({humanize_size(file_info.size)})")
            if working_state.has_deletions():
                console.print(f"\n[red]Deleted locally ({len(working_state.missing)} files):[/red]")
                for path in sorted(working_state.missing):
                    console.print(f"  [red]−[/red] {path}")
    
    # Show untracked files if requested (or if include_ignored is set)
    if untracked or untracked_only or include_ignored:
        from .working_state import scan_untracked
        
        untracked_files = scan_untracked(ctx, tracked, include_ignored=include_ignored)
        
        if untracked_files:
            # Create untracked table
            untracked_table = Table(title="Untracked files")
            untracked_table.add_column("File", style="cyan")
            untracked_table.add_column("Status")
            untracked_table.add_column("Size", justify="right")
            
            # Show max 200 files
            display_files = untracked_files[:200]
            for file in display_files:
                status = "[dim](ignored)[/dim]" if file.ignored else "[yellow]?[/yellow] untracked"
                untracked_table.add_row(
                    file.path,
                    status,
                    humanize_size(file.size)
                )
            
            console.print("\n", untracked_table)
            
            if len(untracked_files) > 200:
                console.print(f"\n[dim]... and {len(untracked_files) - 200} more files[/dim]")
            
            console.print("\n[dim]Add files with: modelops-bundle add <path>[/dim]")
        else:
            if untracked_only:
                console.print("[dim]No untracked files found[/dim]")
            else:
                console.print("\n[dim]No untracked files found[/dim]")


@app.command()
def push(
    tag: Optional[str] = typer.Option(None, help="Tag to push"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pushed"),
    force: bool = typer.Option(False, "--force", help="Push even if tag has moved (bypass race protection)"),
):
    """Push tracked files to registry."""
    ctx = require_project_context(require_storage=True)

    try:
        config = load_config(ctx)
        tracked = load_tracked(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    if not tracked.files:
        console.print("[yellow]No tracked files to push[/yellow]")
        return
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Warn about missing tracked files
    if working_state.missing:
        console.print("\n[yellow]Warning: Tracked files not found locally:[/yellow]")
        for path in sorted(working_state.missing):
            console.print(f"  [yellow]![/yellow] {path}")
    
    # Get remote state (optional for push)
    adapter = _get_oras_adapter(config, ctx)
    remote, remote_status = get_remote_state_with_status(adapter, config.registry_ref, tag or config.default_tag)
    
    # Only show status if it's an error other than EMPTY (EMPTY is normal for first push)
    from .core import RemoteStatus
    if remote_status not in (RemoteStatus.AVAILABLE, RemoteStatus.EMPTY):
        display_remote_status(remote_status, config.registry_ref, tag or config.default_tag)
    
    # Compute diff with automatic deletion handling
    state = load_state(ctx)
    if remote:
        diff = working_state.compute_diff(remote, state)
    else:
        # No remote - compute against empty remote
        from .core import RemoteState, SyncState
        diff = working_state.compute_diff(
            RemoteState(manifest_digest="", files={}),
            SyncState()
        )
    
    # Create plan
    plan = diff.to_push_plan()
    
    # Display plan
    console.print("[bold]Analyzing changes...[/bold]")
    console.print(plan.summary())
    
    if plan.files_to_upload:
        console.print("\n[yellow]Changes to push:[/yellow]")
        for file in plan.files_to_upload:
            # Determine storage destination
            storage_display = ""
            if config.storage.uses_blob_storage:
                from .storage_models import StorageType
                storage_type, _ = config.storage.classify(Path(file.path), file.size)
                storage_display = " " + format_storage_display(storage_type, config=config, direction="→")
            console.print(f"  [green]↑[/green] {file.path} ({humanize_size(file.size)}){storage_display}")
    
    if plan.deletes:
        console.print("\n[red]Files removed from manifest:[/red]")
        for path in plan.deletes:
            console.print(f"  [red]-[/red] {path}")
    
    # Check if manifest differs (remote has extra files we don't track)
    manifest_differs = False
    remote_only_paths = []
    if remote:
        local_manifest = {(f.path, f.digest) for f in plan.manifest_files}
        remote_manifest = {(p, fi.digest) for p, fi in remote.files.items()}
        manifest_differs = (local_manifest != remote_manifest)
        if manifest_differs:
            local_paths = {f.path for f in plan.manifest_files}
            remote_only_paths = sorted(set(remote.files.keys()) - local_paths)
    
    # If dry-run, show what would happen (including prunes), but don't push
    if dry_run:
        if remote_only_paths:
            console.print("\n[red]Remote-only files that would be pruned:[/red]")
            for p in remote_only_paths:
                console.print(f"  [red]-[/red] {p}")
        console.print("\n[dim]Dry run - no changes made[/dim]")
        return
    
    # Only skip if there is truly nothing to do:
    if not plan.files_to_upload and not plan.deletes and not manifest_differs:
        console.print("\n[green]✓[/green] Everything up to date")
        return
    
    # No confirmation by default - push directly
    target = f"{config.registry_ref}:{tag or config.default_tag}"
    
    # Execute push
    console.print("\n[bold]Pushing files...[/bold]")
    try:
        manifest_digest = ops_push(config, tracked, tag=tag, ctx=ctx, force=force)
        console.print(f"[green]✓[/green] Pushed successfully")
        console.print(f"[dim]Digest: {manifest_digest[:16]}...[/dim]")
    except TagMovedError as e:
        # Specific handling for tag race errors
        console.print(f"[red]✗[/red] {e}")
        console.print("[yellow]Hint: Use --force to override if you're sure you want to push[/yellow]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Push failed: {e}")
        raise typer.Exit(1)


@app.command()
def pull(
    tag: Optional[str] = typer.Option(None, help="Tag to pull"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite local changes"),
    restore_deleted: bool = typer.Option(False, "--restore-deleted", help="Restore deleted files"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pulled"),
):
    """Pull bundle from registry."""
    ctx = require_project_context(require_storage=True)

    try:
        config = load_config(ctx)
        tracked = load_tracked(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    # Get remote state (require it to exist for pull)
    adapter = _get_oras_adapter(config, ctx)
    remote = require_remote(adapter, config.registry_ref, tag or config.default_tag)
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Compute diff with automatic deletion handling
    state = load_state(ctx)
    diff = working_state.compute_diff(remote, state)
    
    # Check for untracked file collisions (filter ignored files like ops.pull does)
    untracked_collisions = []
    for path in remote.files:
        local_path = ctx.root / path
        if local_path.exists() and path not in tracked.files:
            # Only include non-ignored files
            if not ctx.should_ignore(path):
                untracked_collisions.append(path)
    
    # Create preview (pass both flags)
    preview = diff.to_pull_preview(overwrite, restore_deleted=restore_deleted)

    # Add untracked collisions to preview if overwrite is enabled
    if overwrite and untracked_collisions:
        preview.will_overwrite_untracked = untracked_collisions
    
    # Display preview
    console.print("[bold]Analyzing changes...[/bold]")
    console.print(preview.summary())
    
    if preview.will_update_or_add:
        console.print("\n[yellow]Files from remote:[/yellow]")
        # Try to get storage info from remote index
        storage_info = {}
        try:
            from .storage_models import BundleIndex
            adapter = _get_oras_adapter(config, ctx)
            # Use the resolved digest from the preview
            if hasattr(preview, 'resolved_digest') and preview.resolved_digest:
                try:
                    index = adapter.get_index(config.registry_ref, preview.resolved_digest)
                    for file_path, entry in index.files.items():
                        storage_info[file_path] = format_storage_display(
                            entry.storage,
                            config=config,
                            entry=entry,
                            direction="←"
                        )
                except:
                    pass
        except:
            pass
        
        for file in preview.will_update_or_add:
            storage_display = ""
            if file.path in storage_info:
                storage_display = " " + storage_info[file.path]
            else:
                # Fallback: classify based on policy if no index info
                from .storage_models import StorageType
                storage_type, _ = config.storage.classify(Path(file.path), file.size)
                storage_display = " " + format_storage_display(storage_type, config=config, direction="←")
            console.print(f"  [blue]↓[/blue] {file.path} ({humanize_size(file.size)}){storage_display}")
    
    if preview.will_delete_local and overwrite:
        console.print("\n[red]Files to delete locally:[/red]")
        for path in preview.will_delete_local:
            console.print(f"  [red]-[/red] {path}")
    
    if preview.will_overwrite_untracked and overwrite:
        console.print("\n[yellow]Untracked files to overwrite:[/yellow]")
        for path in preview.will_overwrite_untracked:
            console.print(f"  [yellow]![/yellow] {path}")
    
    if preview.conflicts and not overwrite:
        console.print("\n[red]Conflicts (use --overwrite to force):[/red]")
        for path in preview.conflicts:
            console.print(f"  [red]⚠[/red] {path}")
        if not dry_run:
            console.print("\n[red]✗[/red] Pull aborted due to conflicts")
            raise typer.Exit(1)
    
    # If dry-run, show what would happen but don't pull
    if dry_run:
        console.print("\n[dim]Dry run - no changes made[/dim]")
        return
    
    # Check if there's anything to do
    if not preview.will_update_or_add and not preview.will_delete_local:
        console.print("\n[green]✓[/green] Everything up to date")
        return
    
    # Show warning if overwriting
    if overwrite and preview.has_destructive_changes():
        console.print("\n[red]Warning: Overwriting local changes![/red]")
    
    # Execute pull
    if overwrite:
        console.print("\n[bold]Pulling files (full mirror, will delete local-only files)...[/bold]")
    else:
        console.print("\n[bold]Pulling changes...[/bold]")
    try:
        result = ops_pull(config, tracked, tag=tag, overwrite=overwrite, restore_deleted=restore_deleted, ctx=ctx)
        console.print(f"[green]✓[/green] {result.summary()}")
    except Exception as e:
        console.print(f"[red]✗[/red] Pull failed: {e}")
        raise typer.Exit(1)


@app.command()
def manifest(
    reference: Optional[str] = typer.Argument(None, help="Tag or digest to inspect"),
    tags_only: bool = typer.Option(False, "--tags-only", help="List only tag names"),
    full: bool = typer.Option(False, "--full", help="Show full digests"),
    show_all: bool = typer.Option(False, "--all", help="Show all manifests (no filtering)"),
    limit: int = typer.Option(10, "-n", help="Number of manifests to show (default: 10)"),
):
    """Inspect registry manifests and tags."""
    ctx = require_project_context()
    
    try:
        config = load_config(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    adapter = _get_oras_adapter(config, ctx)

    # If a specific reference is provided, show its details
    if reference:
        # First resolve to digest and fetch manifest (doesn't require index)
        try:
            resolved_digest = adapter.resolve_tag_to_digest(config.registry_ref, reference)
            manifest = adapter.get_manifest(config.registry_ref, resolved_digest)
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to fetch manifest: {e}")
            raise typer.Exit(1)
        
        # Try to get storage info from index if available
        storage_info = {}
        remote = None
        try:
            index = adapter.get_index(config.registry_ref, resolved_digest)
            # Build remote state from index
            from .ops import _index_to_remote_state
            remote = _index_to_remote_state(index, resolved_digest)
            # Store storage info for display
            for path, entry in index.files.items():
                storage_info[path] = format_storage_display(
                    entry.storage,
                    config=config,
                    entry=entry
                )
        except MissingIndexError:
            # No index available - show manifest without storage info
            remote = RemoteState(manifest_digest=resolved_digest, files={})
        
        # Display manifest info
        console.print(f"\n[bold]Manifest for {config.registry_ref}:{reference}[/bold]")
        
        # Manifest digest
        digest = remote.manifest_digest
        if not full and digest.startswith("sha256:"):
            # Show sha256:7chars format
            digest = "sha256:" + digest[7:14]
        console.print(f"Digest: [cyan]{digest}[/cyan]")
        
        # Manifest annotations
        if manifest.get("annotations"):
            console.print("\n[bold]Annotations:[/bold]")
            for key, value in manifest["annotations"].items():
                # Show human-readable date for creation timestamp
                if key == "org.opencontainers.image.created":
                    clean_date = format_iso_date(value)
                    human_date = humanize_date(value)
                    console.print(f"  Created: {clean_date} ([cyan]{human_date}[/cyan])")
                else:
                    console.print(f"  {key}: {value}")
        
        # Layers (files)
        console.print(f"\n[bold]Files ({len(remote.files)}):[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Path")
        table.add_column("Size", justify="right")
        table.add_column("Digest")
        if storage_info:  # Add storage column if available
            table.add_column("Storage")
        
        for path, file_info in sorted(remote.files.items()):
            file_digest = file_info.digest
            if not full and file_digest.startswith("sha256:"):
                # Show sha256:7chars format
                file_digest = "sha256:" + file_digest[7:14]
            
            # Build row data
            row = [path, humanize_size(file_info.size), file_digest]
            if storage_info:
                row.append(storage_info.get(path, "unknown"))
            
            table.add_row(*row)
        
        console.print(table)
    else:
        # No reference provided - list manifests with smart filtering
        if tags_only:
            # Simple tag list for scripting
            try:
                tags = adapter.list_tags(config.registry_ref)
                for tag in sorted(tags):
                    console.print(tag)
            except Exception as e:
                _handle_manifest_connection_error(e, config.registry_ref)
                raise typer.Exit(1)
            return

        # Get all manifests with metadata
        try:
            all_manifests = adapter.list_all_manifests(config.registry_ref)
        except Exception as e:
            _handle_manifest_connection_error(e, config.registry_ref)
            raise typer.Exit(1)

        if not all_manifests:
            console.print(f"[yellow]No manifests found for {config.registry_ref}[/yellow]")
            return

        # Apply smart filtering unless --all is specified
        if show_all:
            filtered_manifests = all_manifests
            showing_all = True
        else:
            filtered_manifests = _apply_smart_filtering(all_manifests, limit)
            showing_all = len(filtered_manifests) == len(all_manifests)

        # Display manifests
        console.print(f"\n[bold]Manifests for {config.registry_ref}:[/bold]")

        # Show summary if filtered
        if not showing_all:
            console.print(f"Showing {len(filtered_manifests)} of {len(all_manifests)} manifests (use --all for complete history)")

        console.print()

        # Display each manifest
        for manifest_info in filtered_manifests:
            digest = manifest_info["digest"]
            tags = manifest_info["tags"]
            created = manifest_info["created"]
            size = manifest_info["size"]
            file_count = manifest_info["file_count"]

            # Format digest
            display_digest = digest
            if not full and digest.startswith("sha256:"):
                display_digest = "sha256:" + digest[7:14]

            # Format tags or show as orphaned
            if tags:
                tag_display = f"({', '.join(sorted(tags))})"
            else:
                tag_display = "(orphaned)"

            # Format creation time
            if created:
                time_display = f" - {humanize_date(created)}"
            else:
                time_display = ""

            console.print(f"[cyan]{display_digest}[/cyan] {tag_display}{time_display}")
            console.print(f"  Files: {file_count} ({humanize_size(size)})")
            console.print()

        console.print("[dim]Use 'modelops-bundle manifest <tag>' to inspect a specific manifest[/dim]")
        console.print("[dim]Use 'modelops-bundle manifest --tags-only' for a simple tag list[/dim]")


@app.command()
def diff(
    tag: Optional[str] = typer.Option(None, help="Tag to compare"),
):
    """Show differences between local and remote."""
    ctx = require_project_context(require_storage=True)

    try:
        config = load_config(ctx)
        tracked = load_tracked(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    if not tracked.files:
        console.print("[yellow]No tracked files[/yellow]")
        return
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    adapter = _get_oras_adapter(config, ctx)

    # Get remote state (require it to exist for diff)
    remote = require_remote(adapter, config.registry_ref, tag or config.default_tag)
    
    # Compute diff with automatic deletion handling
    state = load_state(ctx)
    diff = working_state.compute_diff(remote, state)
    
    # Group changes by type
    groups = {}
    for change in diff.changes:
        if change.change_type not in groups:
            groups[change.change_type] = []
        groups[change.change_type].append(change)
    
    # Display grouped changes
    target = f"{config.registry_ref}:{tag or config.default_tag}"
    console.print(f"[bold]Comparing with {target}[/bold]\n")
    
    type_labels = {
        ChangeType.ADDED_LOCAL: ("Local only", "[green]+[/green]"),
        ChangeType.ADDED_REMOTE: ("Remote only", "[blue]↓[/blue]"),
        ChangeType.MODIFIED_LOCAL: ("Modified locally", "[yellow]M[/yellow]"),
        ChangeType.MODIFIED_REMOTE: ("Modified remotely", "[blue]↓[/blue]"),
        ChangeType.DELETED_LOCAL: ("Deleted locally", "[red]-[/red]"),
        ChangeType.DELETED_REMOTE: ("Deleted remotely", "[blue]×[/blue]"),
        ChangeType.CONFLICT: ("Conflicts", "[red]⚠[/red]"),
        ChangeType.UNCHANGED: ("Unchanged", "[green]✓[/green]"),
    }
    
    for change_type, changes in groups.items():
        if change_type == ChangeType.UNCHANGED and len(changes) > 3:
            # Summarize unchanged files
            console.print(f"[green]✓[/green] {len(changes)} files unchanged")
        else:
            label, icon = type_labels.get(change_type, (str(change_type), "?"))
            if changes:
                console.print(f"[bold]{label}:[/bold]")
                for change in changes:
                    console.print(f"  {icon} {change.path}")
        console.print()


@app.command()
def ensure(
    ref: Optional[str] = typer.Option(None, "--ref", help="Tag or sha256:<manifest>"),
    dest: Path = typer.Option(..., "--dest", help="Destination directory to materialize the bundle"),
    mirror: bool = typer.Option(False, "--mirror", help="Prune files in dest that aren't in the bundle"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview what would happen"),
):
    """Materialize a bundle into a destination directory (for cloud workstations, etc.)."""
    ctx = require_project_context()
    try:
        config = load_config(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)

    try:
        from .ops import ensure_local
        result = ensure_local(config, ref=ref, dest=dest, mirror=mirror, dry_run=dry_run, ctx=ctx)
    except Exception as e:
        console.print(f"[red]✗[/red] ensure failed: {e}")
        raise typer.Exit(1)

    # minimal, consistent output
    mode = "mirror" if mirror else "update-only"
    console.print(f"[bold]Ensure ({mode})[/bold]")
    console.print(f"Resolved: [cyan]{result.resolved_digest[:16]}...[/cyan]")
    console.print(f"Download: {result.downloaded} files ({humanize_size(result.bytes_downloaded)})")
    if mirror:
        console.print(f"Pruned:   {result.deleted} extra files")
    if dry_run:
        console.print("[dim]Dry run - no changes made[/dim]")


def _handle_manifest_connection_error(e: Exception, registry_ref: str):
    """Handle connection errors for manifest operations."""
    from .core import RemoteStatus
    import requests
    import os

    # Debug output
    if os.environ.get("DEBUG"):
        print(f"DEBUG: Exception in manifest operation: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    if isinstance(e, requests.exceptions.ConnectionError):
        display_remote_status(RemoteStatus.UNREACHABLE, registry_ref)
    elif isinstance(e, requests.exceptions.HTTPError):
        if e.response.status_code in (401, 403):
            display_remote_status(RemoteStatus.AUTH_FAILED, registry_ref)
        else:
            display_remote_status(RemoteStatus.UNKNOWN_ERROR, registry_ref)
    else:
        display_remote_status(RemoteStatus.UNKNOWN_ERROR, registry_ref)


def _apply_smart_filtering(all_manifests: List[dict], limit: int) -> List[dict]:
    """Apply smart filtering to manifest list.

    Rules:
    - Always include all tagged manifests
    - Always include manifests from last 7 days
    - Backfill with older manifests up to limit
    """
    if not all_manifests:
        return []

    # Calculate 7 days ago
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    # Separate manifests by criteria
    tagged_manifests = []
    recent_manifests = []
    older_manifests = []

    for manifest in all_manifests:
        has_tags = bool(manifest["tags"])

        # Parse creation date if available
        is_recent = False
        if manifest["created"]:
            try:
                # Parse ISO format timestamp
                created_date = datetime.fromisoformat(manifest["created"].replace('Z', '+00:00'))
                is_recent = created_date >= seven_days_ago
            except (ValueError, TypeError):
                # If we can't parse the date, treat as not recent
                pass

        if has_tags:
            tagged_manifests.append(manifest)
        elif is_recent:
            recent_manifests.append(manifest)
        else:
            older_manifests.append(manifest)

    # Combine results respecting the limit
    result = []

    # Always include tagged manifests
    result.extend(tagged_manifests)

    # Add recent manifests if we have room
    remaining_slots = max(0, limit - len(result))
    result.extend(recent_manifests[:remaining_slots])

    # Backfill with older manifests if we still have room
    remaining_slots = max(0, limit - len(result))
    result.extend(older_manifests[:remaining_slots])

    # Sort the final result by creation date (newest first)
    def sort_key(m):
        if m["created"]:
            return (m["created"], m["digest"])
        else:
            return ("0000-01-01T00:00:00Z", m["digest"])

    result.sort(key=sort_key, reverse=True)

    return result


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
