"""Display logic for model status command."""

from typing import Optional

from rich.console import Console
from rich.table import Table

from .core import ChangeType
from .model_state import ModelReadiness, ModelState, ModelStatusSnapshot, ModelSyncState
from .utils import humanize_date, humanize_size


def display_model_status(snapshot: ModelStatusSnapshot, console: Console, explain: bool = False):
    """Display model-centric status view.

    Args:
        snapshot: Complete model status snapshot
        console: Rich console for output
        explain: If True, show detailed explanations for STALE models
    """
    # Header
    console.print(f"\n[bold]Bundle:[/bold] {snapshot.bundle_ref}:{snapshot.bundle_tag}")

    # Cloud sync summary
    if snapshot.cloud_manifest_digest:
        if snapshot.all_synced:
            console.print("[green]Cloud sync: ✓ Up to date[/green]")
        else:
            # Count models needing push
            models_ahead = len(snapshot.get_models_by_sync_state(ModelSyncState.AHEAD))
            models_diverged = len(snapshot.get_models_by_sync_state(ModelSyncState.DIVERGED))
            total_need_push = models_ahead + models_diverged

            if total_need_push > 0:
                console.print(f"[yellow]Cloud sync: {total_need_push} models have local changes[/yellow]")
            else:
                console.print("[blue]Cloud sync: Changes available from cloud[/blue]")
    else:
        console.print("[dim]Cloud sync: Not pushed yet[/dim]")

    # Model table
    table = Table(title=f"\nRegistered Models ({len(snapshot.models)})")
    table.add_column("Model", style="cyan")
    table.add_column("Local")  # Renamed from "Status" for clarity
    table.add_column("Dependencies")
    table.add_column("Last Changed")
    table.add_column("Cloud")

    for model_id, model in sorted(snapshot.models.items()):
        # Status icon based on readiness
        status_icon = {
            ModelReadiness.READY: "[green]✓ Ready[/green]",
            ModelReadiness.STALE: "[yellow]⚠ Stale[/yellow]",
            ModelReadiness.BROKEN: "[red]✗ Error[/red]",
            ModelReadiness.UNKNOWN: "[dim]? Unknown[/dim]",
        }[model.local_readiness]

        # Dependencies summary
        total_deps = len(model.all_dependencies)
        valid_deps = sum(1 for d in model.all_dependencies if d.is_valid)
        if valid_deps == total_deps:
            deps_text = "[green]✓ Current[/green]"
        else:
            invalid = total_deps - valid_deps
            deps_text = f"[yellow]{invalid} changed[/yellow]"

        # Last changed time (as string for Rich table)
        last_changed = _get_last_changed(model)

        # Cloud sync state with clear, actionable text
        cloud_text = {
            ModelSyncState.SYNCED: "[green]✓ SYNCED[/green]",
            ModelSyncState.AHEAD: "[blue]⚠ AHEAD[/blue]",  # Local has unpushed changes
            ModelSyncState.BEHIND: "[yellow]⚠ BEHIND[/yellow]",  # Cloud is newer
            ModelSyncState.DIVERGED: "[red]✗ DIVERGED[/red]",  # Both changed
            ModelSyncState.UNTRACKED: "[dim]— NOT PUSHED[/dim]",
            ModelSyncState.UNKNOWN: "[dim]? UNKNOWN[/dim]",
        }[model.cloud_sync_state]

        table.add_row(model.name, status_icon, deps_text, last_changed, cloud_text)

    console.print(table)

    # Target table (if targets exist)
    if snapshot.targets:
        console.print("")  # Spacing
        display_target_status(snapshot, console)

    # Issues section
    models_with_issues = snapshot.get_models_needing_attention()
    targets_with_issues = snapshot.get_targets_needing_attention()
    if models_with_issues or targets_with_issues:
        console.print("\n[bold]Issues requiring attention:[/bold]")

        # Import target readiness for grouping
        from .target_state import TargetReadiness

        # Group models by severity
        broken_models = [m for m in models_with_issues if m.local_readiness == ModelReadiness.BROKEN]
        diverged_models = [m for m in models_with_issues if m.cloud_sync_state == ModelSyncState.DIVERGED]
        other_models = [
            m
            for m in models_with_issues
            if m not in broken_models and m not in diverged_models
        ]

        # Group targets by severity
        broken_targets = [t for t in targets_with_issues if t.local_readiness == TargetReadiness.BROKEN]
        diverged_targets = [t for t in targets_with_issues if t.cloud_sync_state == ModelSyncState.DIVERGED]
        other_targets = [
            t
            for t in targets_with_issues
            if t not in broken_targets and t not in diverged_targets
        ]

        # Show broken first (highest priority) - models and targets
        for model in broken_models:
            for issue in model.issues:
                console.print(f"  [red]•[/red] {model.name}: {issue}")
        for target in broken_targets:
            target_name = target.entrypoint.split(':')[-1]
            for issue in target.issues:
                console.print(f"  [red]•[/red] {target_name}: {issue}")

        # Then diverged - models and targets
        for model in diverged_models:
            for issue in model.issues:
                console.print(f"  [yellow]•[/yellow] {model.name}: {issue}")
        for target in diverged_targets:
            target_name = target.entrypoint.split(':')[-1]
            for issue in target.issues:
                console.print(f"  [yellow]•[/yellow] {target_name}: {issue}")

        # Then others - models and targets
        for model in other_models:
            for issue in model.issues:
                console.print(f"  • {model.name}: {issue}")
        for target in other_targets:
            target_name = target.entrypoint.split(':')[-1]
            for issue in target.issues:
                console.print(f"  • {target_name}: {issue}")

    # Preflight validation
    display_preflight_issues(snapshot, console)

    # Explain mode: Show detailed digest mismatches for STALE models
    if explain:
        from .model_state import FileDigestState
        stale_models = [m for m in snapshot.models.values() if m.local_readiness == ModelReadiness.STALE]
        if stale_models:
            console.print("\n[bold]Explanation (why models are STALE):[/bold]")
            for model in stale_models:
                console.print(f"\n[yellow]{model.name} → STALE[/yellow]")
                for dep in model.all_dependencies:
                    if dep.file_state == FileDigestState.MODIFIED:
                        expected_short = dep.expected_digest[:16] if dep.expected_digest else "none"
                        actual_short = dep.actual_digest[:16] if dep.actual_digest else "none"
                        console.print(f"  {dep.path}")
                        console.print(f"    tracked: {expected_short}...")
                        console.print(f"    current: {actual_short}...")
                console.print(f"[dim]Action: run `mops-bundle push` to publish and refresh digests.[/dim]")

    # Help text
    console.print("\n[dim]Run 'mops-bundle status --details <model|target>' for specific info[/dim]")
    console.print("[dim]Run 'mops-bundle status --files' for file-level status[/dim]")
    if not explain:
        console.print("[dim]Run 'mops-bundle status --explain' to see digest mismatches[/dim]")


def display_model_details(model: ModelState, console: Console):
    """Display detailed status for a specific model.

    Args:
        model: Model to display details for
        console: Rich console for output
    """
    console.print(f"\n[bold]Model:[/bold] {model.name}")
    console.print(f"[bold]Path:[/bold] {model.model_file}")
    console.print(f"[bold]Entrypoint:[/bold] {model.entrypoint}")

    # Status with color
    status_text = {
        ModelReadiness.READY: "[green]✓ Ready to run[/green]",
        ModelReadiness.STALE: "[yellow]⚠ Stale - dependencies modified[/yellow]",
        ModelReadiness.BROKEN: "[red]✗ Broken - missing dependencies[/red]",
        ModelReadiness.UNKNOWN: "[dim]? Unknown state[/dim]",
    }[model.local_readiness]
    console.print(f"[bold]Status:[/bold] {status_text}")

    # Model digests if available
    if model.local_model_digest:
        console.print(f"\n[bold]Model Digest:[/bold]")
        console.print(f"  Local:  {model.local_model_digest[:16]}...")
        if model.cloud_model_digest:
            if model.local_model_digest == model.cloud_model_digest:
                console.print(f"  Cloud:  {model.cloud_model_digest[:16]}... [green](matches)[/green]")
            else:
                console.print(f"  Cloud:  {model.cloud_model_digest[:16]}... [yellow](differs)[/yellow]")

    # Dependencies section
    console.print("\n[bold]Dependencies:[/bold]")

    # Model file
    console.print("  Model File:")
    _display_dependency(model.model_file_state, console, indent="    ")

    # Data files
    if model.data_dependencies:
        console.print("  Data Files:")
        for dep in model.data_dependencies:
            _display_dependency(dep, console, indent="    ")
    else:
        console.print("  Data Files: [dim]None[/dim]")

    # Code files
    if model.code_dependencies:
        console.print("  Code Files:")
        for dep in model.code_dependencies:
            _display_dependency(dep, console, indent="    ")
    else:
        console.print("  Code Files: [dim]None[/dim]")

    # Cloud state
    console.print("\n[bold]Cloud State:[/bold]")
    sync_text = {
        ModelSyncState.SYNCED: "[green]Synced with cloud[/green]",
        ModelSyncState.AHEAD: "[blue]Local changes not pushed[/blue]",
        ModelSyncState.BEHIND: "[yellow]Cloud has newer version[/yellow]",
        ModelSyncState.DIVERGED: "[red]Local and cloud have diverged[/red]",
        ModelSyncState.UNTRACKED: "[dim]Never pushed to cloud[/dim]",
        ModelSyncState.UNKNOWN: "[dim]Unknown sync state[/dim]",
    }[model.cloud_sync_state]
    console.print(f"  Sync: {sync_text}")

    if model.cloud_timestamp:
        console.print(f"  Last pushed: {humanize_date(model.cloud_timestamp)}")

    # Issues
    if model.issues:
        console.print("\n[bold red]Issues:[/bold red]")
        for issue in model.issues:
            console.print(f"  • {issue}")
    else:
        console.print("\n[green]No issues - model is ready to run[/green]")


def _display_dependency(dep, console: Console, indent: str = ""):
    """Display a single dependency state.

    Args:
        dep: ModelDependencyState to display
        console: Rich console
        indent: Indentation string
    """
    # Status icon
    status_icon = {
        "current": "[green]✓[/green]",
        "modified": "[yellow]Δ[/yellow]",
        "missing": "[red]✗[/red]",
        "unknown": "[dim]?[/dim]",
    }.get(dep.file_state.value, "")

    # Size and time
    size_str = humanize_size(dep.size) if dep.size else "?"
    time_str = f", {humanize_date(dep.last_modified)}" if dep.last_modified else ""

    # Display line
    console.print(f"{indent}{status_icon} {dep.path} ({size_str}{time_str})")

    # Show digest mismatch if relevant
    if dep.file_state.value == "modified" and dep.expected_digest and dep.actual_digest:
        console.print(f"{indent}    Expected: {dep.expected_digest[:16]}...")
        console.print(f"{indent}    Actual:   {dep.actual_digest[:16]}...")


def _get_last_changed(model: ModelState) -> str:
    """Get human-readable last change time for model.

    Args:
        model: Model to check

    Returns:
        Human-readable time string
    """
    # Find most recent modification time
    latest = None
    for dep in model.all_dependencies:
        if dep.last_modified:
            if latest is None or dep.last_modified > latest:
                latest = dep.last_modified

    if latest:
        # Convert datetime to ISO string for humanize_date
        return humanize_date(latest.isoformat())
    return "[dim]Unknown[/dim]"


def display_target_status(snapshot: ModelStatusSnapshot, console: Console):
    """Display registered targets table.

    Args:
        snapshot: Complete status snapshot
        console: Rich console for output
    """
    from .target_state import TargetReadiness

    table = Table(title=f"Registered Targets ({len(snapshot.targets)})")
    table.add_column("Target", style="cyan")
    table.add_column("Model Output", style="dim")
    table.add_column("Status")
    table.add_column("Dependencies")
    table.add_column("Last Changed")
    table.add_column("Cloud")

    for target_id, target in sorted(snapshot.targets.items()):
        # Status icon based on readiness
        status_icon = {
            TargetReadiness.READY: "[green]✓ Ready[/green]",
            TargetReadiness.STALE: "[yellow]⚠ Stale[/yellow]",
            TargetReadiness.BROKEN: "[red]✗ Error[/red]",
            TargetReadiness.UNKNOWN: "[dim]? Unknown[/dim]",
        }[target.local_readiness]

        # Dependencies summary (only data files for targets)
        total_deps = len(target.all_dependencies)
        valid_deps = sum(1 for d in target.all_dependencies if d.is_valid)
        if valid_deps == total_deps:
            deps_text = "[green]✓ Current[/green]"
        else:
            invalid = total_deps - valid_deps
            deps_text = f"[yellow]{invalid} changed[/yellow]"

        # Last changed time
        last_changed = _get_last_changed_target(target)

        # Cloud sync state (reuse model logic)
        cloud_text = {
            ModelSyncState.SYNCED: "[green]Synced[/green]",
            ModelSyncState.AHEAD: "[blue]Local ahead[/blue]",
            ModelSyncState.BEHIND: "[yellow]Local behind[/yellow]",
            ModelSyncState.DIVERGED: "[red]Diverged[/red]",
            ModelSyncState.UNTRACKED: "[dim]Never pushed[/dim]",
            ModelSyncState.UNKNOWN: "[dim]Unknown[/dim]",
        }[target.cloud_sync_state]

        # Extract target name from entrypoint for display
        target_name = target.entrypoint.split(':')[-1]

        table.add_row(
            target_name,
            target.model_output,
            status_icon,
            deps_text,
            last_changed,
            cloud_text
        )

    console.print(table)


def display_target_details(target, console: Console):
    """Display detailed status for a specific target.

    Args:
        target: Target to display details for
        console: Rich console for output
    """
    from .target_state import TargetReadiness

    target_name = target.entrypoint.split(':')[-1]
    console.print(f"\n[bold]Target:[/bold] {target_name}")
    console.print(f"[bold]Path:[/bold] {target.target_file}")
    console.print(f"[bold]Entrypoint:[/bold] {target.entrypoint}")
    console.print(f"[bold]Model Output:[/bold] {target.model_output}")

    # Status with color
    status_text = {
        TargetReadiness.READY: "[green]✓ Ready to run[/green]",
        TargetReadiness.STALE: "[yellow]⚠ Stale - dependencies modified[/yellow]",
        TargetReadiness.BROKEN: "[red]✗ Broken - missing dependencies[/red]",
        TargetReadiness.UNKNOWN: "[dim]? Unknown state[/dim]",
    }[target.local_readiness]
    console.print(f"[bold]Status:[/bold] {status_text}")

    # Target digests if available
    if target.local_target_digest:
        console.print(f"\n[bold]Target Digest:[/bold]")
        console.print(f"  Local:  {target.local_target_digest[:16]}...")
        if target.cloud_target_digest:
            if target.local_target_digest == target.cloud_target_digest:
                console.print(f"  Cloud:  {target.cloud_target_digest[:16]}... [green](matches)[/green]")
            else:
                console.print(f"  Cloud:  {target.cloud_target_digest[:16]}... [yellow](differs)[/yellow]")

    # Dependencies section
    console.print("\n[bold]Dependencies:[/bold]")

    # Target file
    console.print("  Target File:")
    _display_dependency(target.target_file_state, console, indent="    ")

    # Data files (observation files)
    if target.data_dependencies:
        console.print("  Observation Files:")
        for dep in target.data_dependencies:
            _display_dependency(dep, console, indent="    ")
    else:
        console.print("  Observation Files: [dim]None[/dim]")

    # Cloud state (same as model)
    console.print("\n[bold]Cloud State:[/bold]")
    sync_text = {
        ModelSyncState.SYNCED: "[green]Synced with cloud[/green]",
        ModelSyncState.AHEAD: "[blue]Local changes not pushed[/blue]",
        ModelSyncState.BEHIND: "[yellow]Cloud has newer version[/yellow]",
        ModelSyncState.DIVERGED: "[red]Local and cloud have diverged[/red]",
        ModelSyncState.UNTRACKED: "[dim]Never pushed to cloud[/dim]",
        ModelSyncState.UNKNOWN: "[dim]Unknown sync state[/dim]",
    }[target.cloud_sync_state]
    console.print(f"  Sync: {sync_text}")

    if target.cloud_timestamp:
        console.print(f"  Last pushed: {humanize_date(target.cloud_timestamp)}")

    # Issues
    if target.issues:
        console.print("\n[bold red]Issues:[/bold red]")
        for issue in target.issues:
            console.print(f"  • {issue}")
    else:
        console.print("\n[green]No issues - target is ready to run[/green]")


def _get_last_changed_target(target) -> str:
    """Get human-readable last change time for target.

    Args:
        target: Target to check

    Returns:
        Human-readable time string
    """
    # Find most recent modification time
    latest = None
    for dep in target.all_dependencies:
        if dep.last_modified:
            if latest is None or dep.last_modified > latest:
                latest = dep.last_modified

    if latest:
        return humanize_date(latest.isoformat())
    return "[dim]Unknown[/dim]"


def display_status_legend(console: Console):
    """Display legend explaining status symbols.

    Args:
        console: Rich console for output
    """
    console.print("\n[bold]Legend:[/bold]")
    console.print("  [green]✓[/green] Current/Ready   [yellow]⚠[/yellow] Stale/Warning   [red]✗[/red] Missing/Error")
    console.print("  [blue]Local ahead[/blue] = You have changes not in cloud")
    console.print("  [yellow]Local behind[/yellow] = Cloud has newer version")


def display_preflight_issues(snapshot: ModelStatusSnapshot, console: Console):
    """Display preflight validation warnings and errors in status output.

    Args:
        snapshot: Status snapshot
        console: Rich console
    """
    from .context import ProjectContext
    from .preflight import PreflightValidator
    from modelops_contracts import BundleRegistry

    # Load registry
    try:
        ctx = ProjectContext()
        registry_path = ctx.storage_dir / "registry.yaml"
        if not registry_path.exists():
            return

        registry = BundleRegistry.load(registry_path)

        # Run validation
        validator = PreflightValidator(ctx, registry)
        result = validator.validate_all()

        # Display warnings
        if result.warnings:
            console.print("\n[bold yellow]Preflight Warnings:[/bold yellow]")
            for issue in result.warnings:
                console.print(f"  [yellow]⚠[/yellow] {issue.message}")
                if issue.suggestion:
                    console.print(f"      [dim]{issue.suggestion}[/dim]")

        # Display errors
        if result.errors:
            console.print("\n[bold red]Preflight Errors (will block job submission):[/bold red]")
            for issue in result.errors:
                console.print(f"  [red]✗[/red] {issue.message}")
                if issue.suggestion:
                    console.print(f"      [dim]{issue.suggestion}[/dim]")

        # Display info messages
        if result.infos:
            console.print("\n[bold blue]Preflight Information:[/bold blue]")
            for issue in result.infos:
                console.print(f"  [blue]ℹ[/blue] {issue.message}")
                if issue.suggestion:
                    console.print(f"      [dim]{issue.suggestion}[/dim]")

    except Exception as e:
        # Don't let preflight checks crash status command
        console.print(f"\n[dim]Note: Preflight validation failed: {e}[/dim]")