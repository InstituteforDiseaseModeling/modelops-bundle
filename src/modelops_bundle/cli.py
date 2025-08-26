"""CLI for modelops-bundle."""

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table
from .context import ProjectContext
from .core import (
    BundleConfig,
    ChangeType,
    TrackedFiles,
)
from .utils import humanize_size, humanize_date, format_iso_date
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


app = typer.Typer(help="ModelOps Bundle - OCI artifact-based model synchronization")
console = Console()



@app.command()
def init(
    registry_ref: str = typer.Argument(..., help="Registry reference (e.g., localhost:5555/epi_model)"),
    tag: str = typer.Option("latest", help="Default tag"),
):
    """Initialize a new bundle in the current directory."""
    # Check if already initialized in current directory
    if ProjectContext.is_initialized():
        console.print("[red]✗[/red] Already initialized in current directory")
        raise typer.Exit(1)
    
    # Initialize project context
    ctx = ProjectContext.init()
    
    # Create config
    config = BundleConfig(
        registry_ref=registry_ref,
        default_tag=tag
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


@app.command()
def add(
    files: List[Path] = typer.Argument(..., help="Files to track"),
):
    """Add files to tracking."""
    try:
        ctx = ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    
    # Load tracked files
    tracked = load_tracked(ctx)
    
    # Add files
    added = []
    for file in files:
        # Check if file exists (handles both absolute and relative paths)
        file_path = Path(file)
        if not file_path.exists():
            console.print(f"[red]✗[/red] File not found: {file}")
            continue
        # Store as project-relative path
        rel_path = ctx.resolve(file)
        tracked.add(rel_path)
        added.append(rel_path)
    
    # Save
    save_tracked(tracked, ctx)
    
    # Display results
    if added:
        console.print(f"[green]✓[/green] Tracking {len(added)} files:")
        for file in added:
            console.print(f"  [green]+[/green] {file}")
    else:
        console.print("[yellow]No files added[/yellow]")


@app.command()
def remove(
    files: List[Path] = typer.Argument(..., help="Files to untrack"),
    rm: bool = typer.Option(False, "--rm", help="Also delete the files from disk"),
):
    """Remove files from tracking."""
    try:
        ctx = ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    
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
def status():
    """Show bundle status."""
    try:
        ctx = ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    
    try:
        config = load_config(ctx)
        tracked = load_tracked(ctx)
        state = load_state(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    # Display bundle info
    console.print(f"[bold]Bundle:[/bold] {config.registry_ref}:{config.default_tag}")
    console.print(f"[bold]Tracked files:[/bold] {len(tracked.files)}")
    
    if not tracked.files:
        console.print("\n[yellow]No tracked files. Use 'add' to track files.[/yellow]")
        return
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Try to get remote state
    adapter = OrasAdapter()
    try:
        remote = adapter.get_remote_state(config.registry_ref, config.default_tag)
    except Exception:
        remote = None
    
    # Get status summary
    summary = working_state.get_status(remote, state)
    
    if remote and summary:
        
        # Create status table
        table = Table(title="File Status")
        table.add_column("File", style="cyan")
        table.add_column("Status")
        table.add_column("Size", justify="right")
        
        # Use summary for a cleaner display
        status_map = {
            ChangeType.UNCHANGED: "[green]✓[/green] unchanged",
            ChangeType.ADDED_LOCAL: "[green]+[/green] new",
            ChangeType.ADDED_REMOTE: "[blue]↓[/blue] remote only (untracked)",
            ChangeType.MODIFIED_LOCAL: "[yellow]M[/yellow] modified locally",
            ChangeType.MODIFIED_REMOTE: "[blue]↓[/blue] modified remotely",
            ChangeType.DELETED_LOCAL: "[red]-[/red] deleted locally",
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
        
        for path, change_type, file_info in sorted(all_items):
            # Always add row, even for deleted files where file_info is None
            table.add_row(
                path,
                status_map.get(change_type, str(change_type)),
                humanize_size(file_info.size) if file_info else "-"
            )
        
        console.print("\n", table)
        
        # Show hint about remote-only files
        if summary.added_remote > 0:
            console.print(f"\n[dim]💡 Tip: Push will prune {summary.added_remote} remote-only (untracked) file{'s' if summary.added_remote != 1 else ''} from the manifest[/dim]")
        
        # Show summary line
        if summary.unchanged > 10:
            console.print(f"\n[dim]Plus {summary.unchanged} unchanged files[/dim]")
    else:
        # Just show local files
        console.print("\n[bold]Local files:[/bold]")
        for path, file_info in working_state.files.items():
            console.print(f"  {path} ({humanize_size(file_info.size)})")
        if working_state.has_deletions():
            console.print(f"\n[red]Deleted locally ({len(working_state.missing)} files):[/red]")
            for path in sorted(working_state.missing):
                console.print(f"  [red]-[/red] {path}")
        console.print("\n[yellow]Remote not accessible or empty[/yellow]")


@app.command()
def push(
    tag: Optional[str] = typer.Option(None, help="Tag to push"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pushed"),
):
    """Push tracked files to registry."""
    try:
        ctx = ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    
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
    
    # Get remote state
    adapter = OrasAdapter()
    try:
        remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
    except Exception:
        remote = None
    
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
            console.print(f"  [green]↑[/green] {file.path} ({humanize_size(file.size)})")
    
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
        manifest_digest = ops_push(config, tracked, tag=tag, ctx=ctx)
        console.print(f"[green]✓[/green] Pushed successfully")
        console.print(f"[dim]Digest: {manifest_digest[:16]}...[/dim]")
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
    try:
        ctx = ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    
    try:
        config = load_config(ctx)
        tracked = load_tracked(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    # Get remote state
    adapter = OrasAdapter()
    try:
        remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to fetch remote: {e}")
        raise typer.Exit(1)
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Compute diff with automatic deletion handling
    state = load_state(ctx)
    diff = working_state.compute_diff(remote, state)
    
    # Create plan
    plan = diff.to_pull_plan(overwrite)
    
    # Display plan
    console.print("[bold]Analyzing changes...[/bold]")
    console.print(plan.summary())
    
    if plan.files_to_download:
        console.print("\n[yellow]Changes from remote:[/yellow]")
        for file in plan.files_to_download:
            console.print(f"  [blue]↓[/blue] {file.path} ({humanize_size(file.size)})")
    
    if plan.files_to_delete_local and overwrite:
        console.print("\n[red]Files to delete locally:[/red]")
        for path in plan.files_to_delete_local:
            console.print(f"  [red]-[/red] {path}")
    
    if plan.conflicts and not overwrite:
        console.print("\n[red]Conflicts (use --overwrite to force):[/red]")
        for path in plan.conflicts:
            console.print(f"  [red]⚠[/red] {path}")
        if not dry_run:
            console.print("\n[red]✗[/red] Pull aborted due to conflicts")
            raise typer.Exit(1)
    
    # If dry-run, show what would happen but don't pull
    if dry_run:
        console.print("\n[dim]Dry run - no changes made[/dim]")
        return
    
    # Only skip if no downloads AND no deletions
    if not plan.files_to_download and not plan.files_to_delete_local:
        console.print("\n[green]✓[/green] Everything up to date")
        return
    
    # Show warning if overwriting
    if overwrite and plan.conflicts:
        console.print("\n[red]Warning: Overwriting local changes![/red]")
    
    # Execute pull
    console.print("\n[bold]Pulling files...[/bold]")
    try:
        executed_plan = ops_pull(config, tracked, tag=tag, overwrite=overwrite, ctx=ctx)
        console.print(f"[green]✓[/green] Pulled successfully")
        if executed_plan.files_to_skip:
            console.print(f"[yellow]Skipped {len(executed_plan.files_to_skip)} files with local changes[/yellow]")
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
    try:
        ctx = ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    
    try:
        config = load_config(ctx)
    except FileNotFoundError:
        console.print("[red]✗[/red] Bundle not properly initialized")
        raise typer.Exit(1)
    
    adapter = OrasAdapter()
    
    # If a specific reference is provided, show its details
    if reference:
        try:
            # Try to get manifest (works for both tags and digests)
            manifest = adapter.get_manifest(config.registry_ref, reference)
            remote = adapter.get_remote_state(config.registry_ref, reference)
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to fetch manifest for '{reference}': {e}")
            raise typer.Exit(1)
        
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
        
        for path, file_info in sorted(remote.files.items()):
            file_digest = file_info.digest
            if not full and file_digest.startswith("sha256:"):
                # Show sha256:7chars format
                file_digest = "sha256:" + file_digest[7:14]
            table.add_row(
                path,
                humanize_size(file_info.size),
                file_digest
            )
        
        console.print(table)
    else:
        # No reference provided - list all manifests or tags
        try:
            tags = adapter.list_tags(config.registry_ref)
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to list tags: {e}")
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
                    remote = adapter.get_remote_state(config.registry_ref, tag)
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
    try:
        ctx = ProjectContext()
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    
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
    
    try:
        remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to fetch remote: {e}")
        raise typer.Exit(1)
    
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
