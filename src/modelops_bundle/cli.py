"""CLI for modelops-bundle."""

from pathlib import Path
from typing import List, Optional
import sys

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import track

from .context import ProjectContext
from .core import (
    BundleConfig,
    ChangeType,
    TrackedFiles,
    WorkingTreeState,
)
from .ops import (
    compute_diff,
    load_config,
    load_state,
    load_tracked,
    pull as ops_pull,
    push as ops_push,
    save_config,
    save_tracked,
)
from .oras import OrasAdapter


app = typer.Typer(help="ModelOps Bundle - OCI artifact-based model synchronization")
console = Console()


@app.command()
def init(
    registry_ref: str = typer.Argument(..., help="Registry reference (e.g., localhost:5555/epi_model)"),
    tag: str = typer.Option("latest", help="Default tag"),
):
    """Initialize a new bundle in the current directory."""
    # Check if already initialized
    try:
        ctx = ProjectContext()
        console.print("[red]✗[/red] Already initialized")
        raise typer.Exit(1)
    except ValueError:
        # Not initialized, proceed
        pass
    
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
        abs_file = ctx.absolute(file) if not file.is_absolute() else file
        if not abs_file.exists():
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
    for file in files:
        # Convert to project-relative path
        try:
            rel_path = ctx.resolve(file)
            if str(rel_path) in tracked.files:
                tracked.remove(rel_path)
                removed.append(rel_path)
        except ValueError:
            # File outside project
            pass
    
    # Save
    save_tracked(tracked, ctx)
    
    # Display results
    if removed:
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
    
    # Scan working tree
    working = WorkingTreeState.scan(tracked.files, ctx.root)
    
    # Try to get remote state
    adapter = OrasAdapter()
    try:
        remote = adapter.get_remote_state(config.registry_ref, config.default_tag)
    except Exception:
        remote = None
    
    # Compute diff if remote exists
    if remote:
        diff = compute_diff(working, remote, state)
        
        # Create status table
        table = Table(title="File Status")
        table.add_column("File", style="cyan")
        table.add_column("Status")
        table.add_column("Size", justify="right")
        
        for change in diff.changes:
            status_map = {
                ChangeType.UNCHANGED: "[green]✓[/green] unchanged",
                ChangeType.ADDED_LOCAL: "[green]+[/green] new",
                ChangeType.ADDED_REMOTE: "[blue]↓[/blue] remote only",
                ChangeType.MODIFIED_LOCAL: "[yellow]M[/yellow] modified locally",
                ChangeType.MODIFIED_REMOTE: "[blue]↓[/blue] modified remotely",
                ChangeType.CONFLICT: "[red]⚠[/red] conflict",
            }
            
            file_info = change.local or change.remote
            if file_info:
                table.add_row(
                    change.path,
                    status_map.get(change.change_type, str(change.change_type)),
                    _humanize_size(file_info.size)
                )
        
        console.print("\n", table)
    else:
        # Just show local files
        console.print("\n[bold]Local files:[/bold]")
        for path, file_info in working.files.items():
            console.print(f"  {path} ({_humanize_size(file_info.size)})")
        console.print("\n[yellow]Remote not accessible or empty[/yellow]")


@app.command()
def push(
    tag: Optional[str] = typer.Option(None, help="Tag to push"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pushed"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation"),
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
    
    # Scan working tree
    working = WorkingTreeState.scan(tracked.files, ctx.root)
    
    # Get remote state
    adapter = OrasAdapter()
    try:
        remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
    except Exception:
        remote = None
    
    # Compute diff
    state = load_state(ctx)
    if remote:
        diff = compute_diff(working, remote, state)
    else:
        # No remote - all files are new
        from .core import DiffResult, FileChange
        changes = [
            FileChange(
                path=path,
                change_type=ChangeType.ADDED_LOCAL,
                local=file_info
            )
            for path, file_info in working.files.items()
        ]
        diff = DiffResult(changes=changes)
    
    # Create plan
    plan = diff.to_push_plan()
    
    # Display plan
    console.print("[bold]Analyzing changes...[/bold]")
    console.print(plan.summary())
    
    if plan.files_to_upload:
        console.print("\n[yellow]Changes to push:[/yellow]")
        for file in plan.files_to_upload:
            console.print(f"  [green]↑[/green] {file.path} ({_humanize_size(file.size)})")
    
    if dry_run:
        console.print("\n[dim]Dry run - no changes made[/dim]")
        return
    
    if not plan.files_to_upload:
        console.print("\n[green]✓[/green] Everything up to date")
        return
    
    # Confirm
    target = f"{config.registry_ref}:{tag or config.default_tag}"
    if not yes:
        if not typer.confirm(f"\nPush to {target}?"):
            raise typer.Abort()
    
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
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation"),
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
    
    # Scan working tree
    working = WorkingTreeState.scan(tracked.files, ctx.root)
    
    # Compute diff
    state = load_state(ctx)
    diff = compute_diff(working, remote, state)
    
    # Create plan
    plan = diff.to_pull_plan(overwrite)
    
    # Display plan
    console.print("[bold]Analyzing changes...[/bold]")
    console.print(plan.summary())
    
    if plan.files_to_download:
        console.print("\n[yellow]Changes from remote:[/yellow]")
        for file in plan.files_to_download:
            console.print(f"  [blue]↓[/blue] {file.path} ({_humanize_size(file.size)})")
    
    if plan.conflicts and not overwrite:
        console.print("\n[red]Conflicts (use --overwrite to force):[/red]")
        for path in plan.conflicts:
            console.print(f"  [red]⚠[/red] {path}")
        console.print("\n[red]✗[/red] Pull aborted due to conflicts")
        raise typer.Exit(1)
    
    if not plan.files_to_download:
        console.print("\n[green]✓[/green] Everything up to date")
        return
    
    # Confirm
    if not yes:
        if overwrite and plan.conflicts:
            console.print("\n[red]Warning: This will overwrite local changes![/red]")
        if not typer.confirm("\nContinue?"):
            raise typer.Abort()
    
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
    
    # Get states
    working = WorkingTreeState.scan(tracked.files, ctx.root)
    adapter = OrasAdapter()
    
    try:
        remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to fetch remote: {e}")
        raise typer.Exit(1)
    
    # Compute diff
    state = load_state(ctx)
    diff = compute_diff(working, remote, state)
    
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


def _humanize_size(size: int) -> str:
    """Convert bytes to human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()