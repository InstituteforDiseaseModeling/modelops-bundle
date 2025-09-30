# ModelOps Bundle

[![Tests](https://github.com/vsbuffalo/modelops-bundle/actions/workflows/tests.yml/badge.svg)](https://github.com/vsbuffalo/modelops-bundle/actions/workflows/tests.yml)

A git-like workflow for managing, building, and deploying model bundles to ModelOps infrastructure.

## Installation

```bash
# Install from GitHub using uv
uv pip install git+https://github.com/vsbuffalo/modelops-bundle.git

# Or clone and install for development
git clone https://github.com/vsbuffalo/modelops-bundle.git
cd modelops-bundle
uv pip install -e .
```

## Quick Start

```bash
# Initialize a new project
mops-bundle init my-project

# Or initialize current directory
mops-bundle init

# Add files to bundle
mops-bundle add src/ data/ config.yaml

# Push bundle to registry
mops-bundle push
```

## Environment Setup

ModelOps Bundle uses environment configurations from `~/.modelops/bundle-env/` which are automatically created when you provision ModelOps infrastructure with `mops infra up`.

For local development, start the Docker services:
```bash
# From modelops-bundle/dev directory
make start  # Starts local registry and storage
```

## Commands

### init
Create a new bundle project or initialize existing directory.

```bash
# Create new project directory
mops-bundle init my-project

# Initialize current directory
mops-bundle init

# Customize storage threshold
mops-bundle init my-project --threshold 100
```

Options:
- `--threshold`: Size threshold in MB for blob storage (default: 50MB)
- `--env`: Environment to use (default: dev)
- `--tag`: Default tag (default: latest)

### add
Add files or directories to tracking. Directories are added recursively.

```bash
# Add specific files
mops-bundle add src/model.py data/config.yaml

# Add entire directories
mops-bundle add src/ data/

# Add everything (respects .modelopsignore)
mops-bundle add .

# Force-add ignored files
mops-bundle add --force ignored.tmp
```

Options:
- `--force`: Add files even if they're ignored

### remove
Remove files from tracking (doesn't delete from disk unless --rm is used).

```bash
# Stop tracking files
mops-bundle remove src/old_model.py

# Untrack AND delete files
mops-bundle remove --rm tmp/
```

Options:
- `--rm`: Also delete the files from disk

### status
Show tracked files and their sync status.

```bash
# Show tracked and modified files
mops-bundle status

# Also show untracked files
mops-bundle status -u

# Only show untracked files
mops-bundle status --untracked-only
```

Options:
- `-u, --untracked`: Show untracked files
- `--untracked-only`: Show only untracked files
- `--include-ignored`: Include ignored files

### push
Push bundle to registry. Files larger than threshold (default 50MB) use blob storage.

```bash
# Push to default tag
mops-bundle push

# Push with specific tag
mops-bundle push --tag v1.2.3

# Preview what would be pushed
mops-bundle push --dry-run
```

Options:
- `--tag`: Tag to push (defaults to config default_tag)
- `--dry-run`: Show what would be pushed without pushing
- `--force`: Push even if tag has moved (bypass race protection)

### pull
Pull bundle from registry. Won't overwrite local changes by default.

```bash
# Pull latest version
mops-bundle pull

# Pull specific tag
mops-bundle pull --tag v1.2.3

# Overwrite local changes
mops-bundle pull --overwrite

# Also restore deleted files
mops-bundle pull --restore-deleted
```

Options:
- `--tag`: Tag to pull (defaults to config default_tag)
- `--overwrite`: Overwrite local changes
- `--restore-deleted`: Restore files that were deleted locally
- `--dry-run`: Show what would be pulled without pulling

### manifest
List and inspect registry tags.

```bash
# List all tags and manifests
mops-bundle manifest

# Show specific tag details
mops-bundle manifest v1.2.3

# Just list tag names
mops-bundle manifest --tags-only
```

Options:
- `--tags-only`: List only tag names
- `--full`: Show full digests
- `--all`: Show all manifests (no filtering)

### diff
Show differences between local and remote bundles.

```bash
# Compare with latest
mops-bundle diff

# Compare with specific tag
mops-bundle diff --tag v1.2
```

Options:
- `--tag`: Tag to compare with

### ensure
Materialize bundle to another directory (useful for deployments).

```bash
# Download latest to deployment directory
mops-bundle ensure --dest /deploy/model

# Download specific version
mops-bundle ensure --ref v1.2 --dest /tmp

# Mirror mode (removes extra files)
mops-bundle ensure --mirror --dest /clean
```

Options:
- `--ref`: Tag or sha256 digest to download
- `--dest`: Destination directory (required)
- `--mirror`: Prune files in dest that aren't in bundle
- `--dry-run`: Preview what would happen

### status
Show current bundle status.

```bash
mops-bundle status
```

### diff
Show differences between local and remote bundle.

```bash
# Compare with latest tag
mops-bundle diff

# Compare with specific tag
mops-bundle diff --tag v1.0
```

### manifest
Inspect registry manifests and tags.

```bash
# List all tags
mops-bundle manifest

# Inspect specific manifest
mops-bundle manifest inspect --tag v1.0

# Show manifest with limited entries
mops-bundle manifest --limit 5
```

### ensure
Materialize a bundle into a destination directory (useful for cloud workstations).

```bash
# Pull bundle to specific directory
mops-bundle ensure /path/to/destination

# Pull specific tag
mops-bundle ensure /path/to/destination --tag v1.0
```

## Configuration

Bundle configuration is stored in `.modelops-bundle/config.yaml`:

```yaml
registry_ref: localhost:5555/my-project
default_tag: latest
storage:
  provider: azure  # or s3, gcs, fs
  container: modelops-bundles
  mode: auto  # auto, blob, oci-inline
  threshold_bytes: 52428800  # 50MB
```

This is created automatically when you run `mops-bundle init`.

## Storage Modes

- **OCI**: Store everything in OCI registry (small bundles)
- **Blob**: Store in blob storage with registry manifest pointing to blobs
- **Auto**: Automatically choose based on size (default 50MB threshold)

