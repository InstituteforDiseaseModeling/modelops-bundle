"""CLI for modelops-bundle."""

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
from .core import RemoteStatus


app = typer.Typer(help="ModelOps-Bundle - OCI artifact-based model bundle synchronization")
console = Console()


def require_project_context() -> ProjectContext:
    """Ensure project is initialized and return context."""
    try:
        return ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        console.print()
        console.print("[dim]Hint: Check you're in the right directory[/dim]")
        console.print()
        console.print("To initialize a new project, run:")
        console.print("  [cyan]uv run modelops-bundle init <registry-ref>[/cyan]")
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


def get_remote_state_with_status(
    oras: "OrasAdapter",
    registry_ref: str, 
    reference: str = "latest"
) -> Tuple[Optional["RemoteState"], "RemoteStatus"]:
    """Get remote state and status, handling all error cases cleanly."""
    from .core import RemoteStatus
    import requests
    
    try:
        remote_state = oras.get_remote_state(registry_ref, reference)
        return remote_state, RemoteStatus.AVAILABLE
    except requests.exceptions.ConnectionError:
        return None, RemoteStatus.UNREACHABLE
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None, RemoteStatus.EMPTY
        elif e.response.status_code in (401, 403):
            return None, RemoteStatus.AUTH_FAILED
        else:
            return None, RemoteStatus.UNKNOWN_ERROR
    except Exception as e:
        # Check for specific error patterns in the exception message
        error_str = str(e).lower()
        if "404" in error_str or "not found" in error_str:
            return None, RemoteStatus.EMPTY
        elif "401" in error_str or "403" in error_str or "auth" in error_str:
            return None, RemoteStatus.AUTH_FAILED
        elif "connection" in error_str or "network" in error_str:
            return None, RemoteStatus.UNREACHABLE
        else:
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
        console.print("To push initial content, run:")
        console.print(f"  [cyan]uv run modelops-bundle push[/cyan]")
    elif status == RemoteStatus.UNREACHABLE:
        console.print()
        console.print("If using a local registry, ensure it's running:")
        console.print("  [cyan]make up[/cyan]")
    
    raise typer.Exit(1)


@app.command()
def init(
    registry_ref: str = typer.Argument(..., help="Registry reference (e.g., localhost:5555/epi_model)"),
    tag: str = typer.Option("latest", help="Default tag"),
    storage_provider: Optional[str] = typer.Option(None, "--storage-provider", help="Storage provider (azure, fs, s3, gcs)"),
    storage_container: Optional[str] = typer.Option(None, "--storage-container", help="Container/bucket name or filesystem path"),
    storage_prefix: Optional[str] = typer.Option(None, "--storage-prefix", help="Optional key prefix for organization"),
    storage_threshold: Optional[int] = typer.Option(None, "--storage-threshold", help="Size threshold in MB (default: 50)"),
    storage_mode: Optional[str] = typer.Option(None, "--storage-mode", help="Storage mode (auto, oci-inline, blob-only)"),
    storage_preset: Optional[str] = typer.Option(None, "--storage-preset", help="Preset configuration (azurite, local)"),
):
    """Initialize a new bundle in the current directory."""
    import os
    from .policy import StoragePolicy
    
    # Check if already initialized in current directory
    if ProjectContext.is_initialized():
        console.print("[red]✗[/red] Already initialized in current directory")
        raise typer.Exit(1)
    
    # Initialize project context
    ctx = ProjectContext.init()
    
    # Handle storage presets
    if storage_preset:
        if storage_preset == "azurite":
            # Azurite preset for local development
            storage_provider = "azure"
            storage_container = storage_container or "modelops-bundles"
            # Check if Azurite connection string is available
            if "AZURE_STORAGE_CONNECTION_STRING" not in os.environ:
                # Set the well-known Azurite connection string
                os.environ["AZURE_STORAGE_CONNECTION_STRING"] = (
                    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
                    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
                    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1"
                )
                console.print("[yellow]ℹ[/yellow] Using Azurite connection string")
        elif storage_preset == "local":
            # Local filesystem preset for testing
            storage_provider = "fs"
            storage_container = storage_container or "/tmp/modelops-storage"
        else:
            console.print(f"[red]✗[/red] Unknown storage preset: {storage_preset}")
            raise typer.Exit(1)
    
    # Validate storage configuration
    if storage_provider:
        if storage_provider == "azure":
            if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
                console.print("[red]✗[/red] Azure storage requires AZURE_STORAGE_CONNECTION_STRING environment variable")
                console.print("[yellow]💡[/yellow] Set it with: export AZURE_STORAGE_CONNECTION_STRING=\"...\"")
                console.print("[yellow]💡[/yellow] Or use --storage-preset azurite for local development")
                raise typer.Exit(1)
            if not storage_container:
                console.print("[red]✗[/red] Azure storage requires --storage-container")
                raise typer.Exit(1)
        elif storage_provider == "fs":
            if not storage_container:
                console.print("[red]✗[/red] Filesystem storage requires --storage-container (absolute path)")
                raise typer.Exit(1)
            # Convert to absolute path if relative
            storage_container = str(Path(storage_container).absolute())
        elif storage_provider in ["s3", "gcs"]:
            console.print(f"[yellow]⚠[/yellow] {storage_provider.upper()} storage support coming soon")
            console.print("[yellow]💡[/yellow] Use azure or fs provider for now")
            raise typer.Exit(1)
        elif storage_provider != "":
            console.print(f"[red]✗[/red] Unknown storage provider: {storage_provider}")
            console.print("[yellow]💡[/yellow] Valid providers: azure, fs, s3 (future), gcs (future)")
            raise typer.Exit(1)
    
    # Build storage policy
    storage_policy = None
    if storage_provider or storage_mode or storage_threshold:
        storage_policy = StoragePolicy(
            provider=storage_provider or "",
            container=storage_container or "",
            prefix=storage_prefix or "",
            threshold_bytes=(storage_threshold * 1024 * 1024) if storage_threshold else (50 * 1024 * 1024),
            mode=storage_mode or "auto"
        )
    
    # Create config
    config = BundleConfig(
        registry_ref=registry_ref,
        default_tag=tag,
        storage=storage_policy if storage_policy else StoragePolicy()
    )
    save_config(config, ctx)
    
    # Create empty tracked files
    tracked = TrackedFiles()
    save_tracked(tracked, ctx)
    
    # Create gitignore entry
    gitignore = Path(".gitignore")
    if gitignore.exists():
        with gitignore.open("a") as f:
            f.write("\n# ModelOps Bundle\n.modelops-bundle/\n")
    
    console.print(f"[green]✓[/green] Initialized bundle: {registry_ref}")
    
    # Show storage configuration if enabled
    if storage_provider:
        console.print(f"[green]✓[/green] Storage provider: {storage_provider}")
        if storage_container:
            console.print(f"    Container: {storage_container}")
        if storage_prefix:
            console.print(f"    Prefix: {storage_prefix}")
        if storage_threshold:
            console.print(f"    Threshold: {storage_threshold}MB")
        if storage_mode:
            console.print(f"    Mode: {storage_mode}")


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
    adapter = OrasAdapter()
    remote, remote_status = get_remote_state_with_status(adapter, config.registry_ref, config.default_tag)
    
    # Display remote status if not available
    if not untracked_only:
        display_remote_status(remote_status, config.registry_ref, config.default_tag)
    
    # Get status summary
    summary = working_state.get_status(remote, state)
    
    # Show tracked files table (unless --untracked-only)
    if not untracked_only and remote and summary:
        
        # Create status table
        table = Table(title="File Status")
        table.add_column("File", style="cyan")
        table.add_column("Status")
        table.add_column("Size", justify="right")
        if config.storage and config.storage.enabled:
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
        if config.storage and config.storage.enabled and remote:
            try:
                # Try to get index for storage info  
                try:
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
            except:
                pass
        
        # Determine storage for local files based on policy
        from .storage_models import StorageType
        
        for path, change_type, file_info in sorted(all_items):
            # Determine storage location
            storage = "-"
            if config.storage and config.storage.enabled:
                if change_type not in [ChangeType.DELETED_LOCAL, ChangeType.DELETED_REMOTE]:
                    if path in storage_info:
                        # Use remote storage info if available
                        storage = storage_info[path]
                    elif file_info and config.storage.enabled:
                        # For local files, classify based on policy
                        storage_type = config.storage.classify(Path(path), file_info.size)
                        storage = format_storage_display(storage_type, config=config)
            
            # Build row data
            row_data = [
                path,
                status_map.get(change_type, str(change_type)),
                humanize_size(file_info.size) if file_info else "-"
            ]
            if config.storage and config.storage.enabled:
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
        # Just show local files
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
    ctx = require_project_context()
    
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
    adapter = OrasAdapter()
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
            # Determine storage destination if storage is enabled
            storage_display = ""
            if config.storage and config.storage.enabled:
                from .storage_models import StorageType
                storage_type = config.storage.classify(Path(file.path), file.size)
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
    except RuntimeError as e:
        # Specific handling for tag race errors
        if "Tag" in str(e) and "has moved" in str(e):
            console.print(f"[red]✗[/red] {e}")
            console.print("[yellow]Hint: Use --force to override if you're sure you want to push[/yellow]")
        else:
            console.print(f"[red]✗[/red] Push failed: {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Push failed: {e}")
        raise typer.Exit(1)


@app.command()
def pull(
    tag: Optional[str] = typer.Option(None, help="Tag to pull"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite local changes"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pulled"),
):
    """Pull bundle from registry."""
    ctx = require_project_context()
    
    try:
        config = load_config(ctx)
        tracked = load_tracked(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    # Get remote state (require it to exist for pull)
    adapter = OrasAdapter()
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
    
    # Create preview
    preview = diff.to_pull_preview(overwrite)
    
    # Add untracked collisions to preview if overwrite is enabled
    if overwrite and untracked_collisions:
        preview.will_overwrite_untracked = untracked_collisions
    
    # Display preview
    console.print("[bold]Analyzing changes...[/bold]")
    console.print(preview.summary())
    
    if preview.will_update_or_add:
        console.print("\n[yellow]Files from remote:[/yellow]")
        # Try to get storage info from remote index if available
        storage_info = {}
        if config.storage and config.storage.enabled:
            try:
                from .storage_models import BundleIndex
                adapter = OrasAdapter()
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
            elif config.storage and config.storage.enabled:
                # Fallback: classify based on policy if no index info
                from .storage_models import StorageType
                storage_type = config.storage.classify(Path(file.path), file.size)
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
    
    # Execute pull (mirror operation)
    console.print("\n[bold]Pulling files (full mirror)...[/bold]")
    try:
        result = ops_pull(config, tracked, tag=tag, overwrite=overwrite, ctx=ctx)
        console.print(f"[green]✓[/green] {result.summary()}")
    except Exception as e:
        console.print(f"[red]✗[/red] Pull failed: {e}")
        raise typer.Exit(1)


@app.command()
def manifest(
    reference: Optional[str] = typer.Argument(None, help="Tag or digest to inspect"),
    tags_only: bool = typer.Option(False, "--tags-only", help="List only tag names"),
    full: bool = typer.Option(False, "--full", help="Show full digests"),
):
    """Inspect registry manifests and tags."""
    ctx = require_project_context()
    
    try:
        config = load_config(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    adapter = OrasAdapter()
    
    # If a specific reference is provided, show its details
    if reference:
        # Get remote state (require it to exist)
        remote = require_remote(adapter, config.registry_ref, reference)
        
        # Resolve to digest for consistency
        resolved_digest = adapter.resolve_tag_to_digest(config.registry_ref, reference)
        manifest = adapter.get_manifest(config.registry_ref, resolved_digest)
        
        # Try index-based approach first
        storage_info = {}
        if config.storage.enabled:
            try:
                from .storage_models import StorageType
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
            except ValueError:
                # Fall back to legacy (remote already obtained above)
                pass
        
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
        # No reference provided - list all manifests or tags
        try:
            tags = adapter.list_tags(config.registry_ref)
        except Exception as e:
            # Handle connection/auth errors gracefully
            from .core import RemoteStatus
            import requests
            
            if isinstance(e, requests.exceptions.ConnectionError):
                display_remote_status(RemoteStatus.UNREACHABLE, config.registry_ref)
            elif isinstance(e, requests.exceptions.HTTPError):
                if e.response.status_code in (401, 403):
                    display_remote_status(RemoteStatus.AUTH_FAILED, config.registry_ref)
                else:
                    display_remote_status(RemoteStatus.UNKNOWN_ERROR, config.registry_ref)
            else:
                display_remote_status(RemoteStatus.UNKNOWN_ERROR, config.registry_ref)
            raise typer.Exit(1)
        
        if not tags:
            console.print(f"[yellow]No tags found for {config.registry_ref}[/yellow]")
            return
        
        if tags_only:
            # Simple tag list for scripting
            for tag in sorted(tags):
                console.print(tag)
        else:
            # Default: Group tags by manifest digest
            manifest_groups = {}
            tag_errors = []
            
            for tag in tags:
                try:
                    # Get manifest to find its digest and metadata
                    remote, _ = get_remote_state_with_status(adapter, config.registry_ref, tag)
                    if not remote:
                        continue
                    manifest = adapter.get_manifest(config.registry_ref, tag)
                    digest = remote.manifest_digest
                    
                    # Extract creation date from annotations
                    created = None
                    if manifest.get("annotations"):
                        created = manifest["annotations"].get("org.opencontainers.image.created")
                    
                    if digest not in manifest_groups:
                        manifest_groups[digest] = {
                            "tags": [],
                            "files": len(remote.files),
                            "size": sum(f.size for f in remote.files.values()),
                            "created": created
                        }
                    manifest_groups[digest]["tags"].append(tag)
                except Exception as e:
                    tag_errors.append((tag, str(e)))
            
            # Display grouped by manifest
            console.print(f"\n[bold]Manifests for {config.registry_ref}:[/bold]\n")
            
            for digest, info in manifest_groups.items():
                # Format tags with default marker
                tag_list = []
                for tag in sorted(info["tags"]):
                    if tag == config.default_tag:
                        tag_list.append(f"[green]{tag}[/green]")
                    else:
                        tag_list.append(tag)
                
                # Shorten digest for display (sha256:7chars)
                if not full and digest.startswith("sha256:"):
                    short_digest = "sha256:" + digest[7:14]
                else:
                    short_digest = digest
                console.print(f"[cyan]{short_digest}[/cyan] ({', '.join(tag_list)})")
                console.print(f"  Files: {info['files']} ({humanize_size(info['size'])})")
                if info.get('created'):
                    clean_date = format_iso_date(info['created'])
                    human_date = humanize_date(info['created'])
                    console.print(f"  Created: {clean_date} ([dim]{human_date}[/dim])")
                console.print()
            
            if tag_errors:
                console.print("[yellow]Warning: Some tags could not be fetched:[/yellow]")
                for tag, error in tag_errors:
                    console.print(f"  • {tag}: {error}")
            
            console.print("[dim]Use 'modelops-bundle manifest <tag>' to inspect a specific manifest[/dim]")
            console.print("[dim]Use 'modelops-bundle manifest --tags-only' for a simple tag list[/dim]")


@app.command()
def diff(
    tag: Optional[str] = typer.Option(None, help="Tag to compare"),
):
    """Show differences between local and remote."""
    ctx = require_project_context()
    
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
    adapter = OrasAdapter()
    
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


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
