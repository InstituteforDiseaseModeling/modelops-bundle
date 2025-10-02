# ModelOps Bundle

[![Tests](https://github.com/vsbuffalo/modelops-bundle/actions/workflows/tests.yml/badge.svg)](https://github.com/vsbuffalo/modelops-bundle/actions/workflows/tests.yml)

A git-like workflow for managing ML model bundles with integrated model registry capabilities.

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
# Initialize a new bundle project
mops-bundle init my-project

# Register a model from Python file
mops-bundle register-model src/model.py:StochasticSIR --data data/

# Check status with registered models
mops-bundle status

# Push bundle with model registry to cloud
mops-bundle push
```

## Core Concepts

### Model Registry

ModelOps Bundle includes an integrated model registry that tracks models, their dependencies, and metadata. The registry travels WITH the bundle (in `.modelops-bundle/registry.yaml`), ensuring reproducibility and versioning.

### Bundle Structure

A bundle contains:
- **Model code**: Python files with simulation classes
- **Data files**: Input data referenced by models
- **Registry metadata**: Model definitions and dependencies
- **Configuration**: Bundle settings and cloud endpoints

## Model Registration Workflow

### 1. Register Models

Register simulation models from your Python code:

```bash
# Register a single model class
mops-bundle register-model src/model.py:DeterministicSIR --data data/

# Register with multiple data files
mops-bundle register-model src/model.py:StochasticSIR --data data/input.csv data/params.json

# Register multiple models at once
mops-bundle register-model src/model.py:NetworkSIR src/model.py:DeterministicSIR --data data/
```

### 2. View Model Status

Check the status of your registered models:

```bash
> mops-bundle status

Bundle: modelopsdevacrvsb.azurecr.io/epi_model:latest
Cloud sync: ✓ Up to date

                    Registered Models (3)

Model               Status    Dependencies    Last Changed    Cloud
DeterministicSIR    ⚠ Stale   1 changed      7 hours ago     Synced
NetworkSIR          ⚠ Stale   1 changed      7 hours ago     Synced
StochasticSIR       ⚠ Stale   1 changed      7 hours ago     Synced

Issues requiring attention:
  • StochasticSIR: Modified: data/data.csv
  • DeterministicSIR: Modified: data/data.csv
  • NetworkSIR: Modified: data/data.csv

Run 'mops-bundle status --details <model>' for specific model info
Run 'mops-bundle status --files' for file-level status
```

### 3. View Detailed Model Information

Get detailed information about a specific model:

```bash
> mops-bundle status --details DeterministicSIR

Model: DeterministicSIR
Path: src/model.py
Entrypoint: model:DeterministicSIR
Status: ⚠ Stale - dependencies modified

Model Digest:
    Local:  sha256:52a7275f2...
    Cloud:  sha256:a1b8716ee... (differs)

Dependencies:
    Model File:
      ✓ src/model.py (5.6 KB, 2025-09-30 10:31:32.764988)
    Data Files:
      ⚠ data/data.csv (447.0 B, 2025-09-30 19:17:18.164177)
          Expected: sha256:688c7a30b...
          Actual:   sha256:a617a930f...
    Code Files: None

Cloud State:
    Sync: Local changes not pushed

Issues:
  • Modified: data/data.csv
  • Local changes not pushed - run 'push' to sync
```

### 4. Push to Cloud

Push your bundle with registered models to the cloud:

```bash
> mops-bundle push
Analyzing changes...
↑ 2 files to upload (1.7 KB), 1 unchanged

Changes to push:
  ↑ .modelops-bundle/registry.yaml (1.3 KB) → OCI
  ↑ data/data.csv (447.0 B) → OCI

Pushing to modelopsdevacrvsb.azurecr.io/epi_model:latest...
Successfully pushed modelopsdevacrvsb.azurecr.io/epi_model:latest
✓ Pushed successfully
Digest: sha256:8ef7a2be7...
```

## Lower-Level Bundle Operations

For fine-grained control over bundle contents, use the file-level operations:

### Add Files

```bash
# Add specific files
mops-bundle add src/utils.py config/settings.yaml

# Add entire directories recursively
mops-bundle add src/ data/

# Add everything (respects .modelopsignore)
mops-bundle add .

# Force-add ignored files
mops-bundle add --force temp/debug.log
```

### Remove Files

```bash
# Stop tracking files (keeps on disk)
mops-bundle remove src/old_model.py

# Untrack AND delete files
mops-bundle remove --rm tmp/
```

### File Status

```bash
# Show all tracked files
mops-bundle status --files

# Show untracked files
mops-bundle status -u

# Show only untracked files
mops-bundle status --untracked-only
```

## Environment Setup

ModelOps Bundle uses environment configurations from `~/.modelops/bundle-env/` which are automatically created when you provision ModelOps infrastructure with `mops infra up`.

For local development:
```bash
# From modelops-bundle/dev directory
make start  # Starts local registry and storage
```

## Additional Commands

### Pull Bundle

Pull bundle from registry without overwriting local changes:

```bash
# Pull latest version
mops-bundle pull

# Pull specific tag
mops-bundle pull --tag v1.2.3

# Overwrite local changes
mops-bundle pull --overwrite
```

### View Manifests

List and inspect registry tags:

```bash
# List all tags
mops-bundle manifest

# Show specific tag details
mops-bundle manifest v1.2.3

# Just list tag names
mops-bundle manifest --tags-only
```

### Diff Changes

Compare local and remote bundles:

```bash
# Compare with latest
mops-bundle diff

# Compare with specific tag
mops-bundle diff --tag v1.2
```

### Deploy Bundle

Materialize bundle to another directory (useful for deployments):

```bash
# Download latest to deployment directory
mops-bundle ensure --dest /deploy/model

# Download specific version
mops-bundle ensure --ref v1.2 --dest /tmp

# Mirror mode (removes extra files)
mops-bundle ensure --mirror --dest /clean
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

Model registry is stored in `.modelops-bundle/registry.yaml`:

```yaml
version: '1.0'
models:
  model_stochasticsir:
    entrypoint: model:StochasticSIR
    path: src/model.py
    class_name: StochasticSIR
    scenarios: []
    parameters: []
    outputs: []
    data:
    - data/data.csv
    data_digests:
      data/data.csv: sha256:4f964a58ca00...
    model_digest: sha256:448dc295027b...
```

Both files are created automatically and travel with the bundle for reproducibility.

## Storage Modes

- **OCI**: Store everything in OCI registry (small bundles)
- **Blob**: Store in blob storage with registry manifest pointing to blobs
- **Auto**: Automatically choose based on size (default 50MB threshold)

## Integration with ModelOps

The model registry enables seamless integration with ModelOps infrastructure:

1. **Science Phase**: Scientists register models in bundles
2. **Bundle Phase**: Push bundles with registry to OCI/cloud storage
3. **Execution Phase**: ModelOps workers fetch bundles and use registry to discover models
4. **Simulation**: Models are executed with parameters from Calabaria studies

The registry travels WITH the bundle, ensuring that model metadata is always versioned alongside the code and data it describes.