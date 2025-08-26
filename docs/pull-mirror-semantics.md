# Pull Mirror Semantics

## Overview

The `modelops-bundle pull` command implements **mirror semantics**, meaning it makes your local state match the remote registry state exactly. This is similar to how `rsync` or `git pull` work - the operation is all-or-nothing at the operation level, not per-file.

## Safety Guards

To prevent accidental data loss, pull operations are blocked by default when they would:

1. **Overwrite local modifications** (MODIFIED_LOCAL)
2. **Delete local files** that were removed remotely (DELETED_REMOTE)  
3. **Resolve conflicts** where both local and remote have changed (CONFLICT)

When any of these conditions are detected, the pull will fail with a clear error message indicating what would be affected.

## Using --overwrite

The `--overwrite` flag explicitly allows all destructive changes:

```bash
modelops-bundle pull --overwrite
```

When used, this will:
- Replace all locally modified files with remote versions
- Delete local files that don't exist remotely
- Resolve all conflicts in favor of the remote version
- Result in an exact mirror of the remote state

## Understanding the Pull Plan

The pull operation generates a plan that shows:
- `files_to_download`: Files that would be fetched from remote
- `files_to_skip`: Files that would be preserved locally (display only)
- `conflicts`: Files with conflicting changes
- `files_to_delete_local`: Files that would be removed locally

**Important**: Under mirror semantics, these are informational. The actual operation pulls ALL remote files when any download is needed. The safety guards operate at the operation level, not the file level.

## Examples

### Safe Pull (No Local Changes)
```bash
$ modelops-bundle pull
Analyzing changes...
↓ 3 files to download (1.2 MB)
Pulling files...
✓ Pulled successfully
```

### Blocked Pull (Local Changes)
```bash
$ modelops-bundle pull
Analyzing changes...
Error: Pull would overwrite or delete local changes: 2 conflicts, 1 locally modified. Use --overwrite to force.
```

### Force Pull (Mirror Remote)
```bash
$ modelops-bundle pull --overwrite
Analyzing changes...
↓ 3 files to download (1.2 MB)
Warning: Overwriting local changes!
Pulling files...
✓ Pulled successfully
```

## State Management

After a successful pull:
- The sync state (`last_synced_files`) reflects ALL remote files
- The `last_pull_digest` is updated to the remote manifest digest
- Tracked files are updated to match the remote file set
- Local files not in the remote are removed (with --overwrite)

## Comparison with Push

While `push` also uses mirror semantics (replacing the entire remote manifest), it operates in the opposite direction:
- Push makes remote match local (minus locally deleted files)
- Pull makes local match remote (with safety guards)

Both operations ensure consistency between local and remote states, with clear semantics about what will be changed.