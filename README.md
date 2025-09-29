# ModelOps Bundle

[![Tests](https://github.com/vsbuffalo/modelops-bundle/actions/workflows/tests.yml/badge.svg)](https://github.com/vsbuffalo/modelops-bundle/actions/workflows/tests.yml)

A git-like workflow for managing, building, and deploying model bundles to ModelOps infrastructure.

## Quick Start

```bash
# Install
pip install modelops-bundle

# Initialize a new project with local environment
mops-bundle init my-project --env local

# Or initialize current directory with dev environment
mops-bundle init --env dev

# Add files to bundle
mops-bundle add src/ data/ config.yaml

# Push bundle to registry
mops-bundle push
```

## Environment-Based Configuration

ModelOps Bundle uses explicit environment selection for registry and storage configuration.

### How It Works

1. **Choose Your Environment**
   - Specify environment with `--env` flag when initializing (defaults to "local")
   - Environment configuration must exist in `~/.modelops/bundle-env/{env}.yaml`
   - The chosen environment is saved in your project's config

2. **Environment Files**
   - **Local**: `~/.modelops/bundle-env/local.yaml` - for localhost development
   - **Dev**: `~/.modelops/bundle-env/dev.yaml` - created by `mops infra up`
   - These files contain registry URL and storage credentials

3. **Persistent Configuration**
   - Once initialized with an environment, all commands use that environment
   - No need to specify `--env` on subsequent commands (push, pull, etc.)
   - Environment credentials are loaded automatically at runtime

### Local Development Setup

For local development with Docker:

```bash
# 1. Start local registry and storage (from modelops-bundle/dev directory)
cd dev && make start

# This creates:
# - Local registry at localhost:5555
# - Azurite storage at localhost:10000
# - Environment file at ~/.modelops/bundle-env/local.yaml

# 2. Initialize your project with local environment
mops-bundle init my-project --env local
# ✓ Using environment 'local'
```

### Production Setup

For production/cloud environments:

```bash
# 1. Provision infrastructure (creates dev environment)
mops infra up --config infra.yaml

# This creates ~/.modelops/bundle-env/dev.yaml
# with Azure Container Registry URL and storage credentials

# 2. Initialize your project with dev environment
mops-bundle init my-project --env dev
# ✓ Using environment 'dev'
```

## Commands

### init
Create a new bundle project or initialize existing directory.

```bash
# Create new project directory
mops-bundle init my-project --env local

# Initialize current directory
mops-bundle init --env dev
```

Options:
- `--env`: Specify ModelOps environment (defaults to "local")

### add
Add files to the bundle.

```bash
mops-bundle add <files...> [--recursive]
```

Options:
- `--recursive`: Add directories recursively

### remove
Remove files from the bundle.

```bash
mops-bundle remove <files...>
```

### push
Push bundle to registry.

```bash
mops-bundle push [--tag TAG]
```

Options:
- `--tag`: Tag to push (defaults to "latest")

### pull
Pull bundle from registry.

```bash
mops-bundle pull [--tag TAG]
```

Options:
- `--tag`: Tag to pull (defaults to "latest")

### status
Show current bundle status.

```bash
mops-bundle status
```

## Configuration

Bundle configuration is stored in `.modelops-bundle/config.yaml`:

```yaml
environment: local  # Environment used for this bundle
registry_ref: localhost:5555/my-project
default_tag: latest
storage:
  provider: azure  # or s3, gcs, fs
  container: modelops-bundles
  mode: auto  # auto, blob, oci
  threshold_bytes: 52428800  # 50MB
```

This is created automatically when you run `mops-bundle init --env <env>`.

## Storage Modes

- **OCI**: Store everything in OCI registry (small bundles)
- **Blob**: Store in blob storage with registry manifest pointing to blobs
- **Auto**: Automatically choose based on size (default 50MB threshold)

## Environment Configuration Details

### Where Environments Come From

Environment configurations are created in two ways:

1. **Local Development** (`make start`)
   - Running `make start` in the dev directory creates `~/.modelops/bundle-env/local.yaml`
   - Contains localhost endpoints for registry (localhost:5555) and storage (127.0.0.1:10000)
   - Used for testing with Docker containers

2. **ModelOps Infrastructure** (`mops infra up`)
   - Provisions Azure resources (ACR, Storage Account)
   - Creates `~/.modelops/bundle-env/dev.yaml` with actual Azure endpoints
   - Contains registry URL and storage connection strings with credentials

### File Structure

```
~/.modelops/
├── environments/
│   ├── local.yaml      # Auto-created for Docker development
│   └── dev.yaml        # Created by mops infra up 
```

Currently modelops just uses "dev" for its created workspaces; there isn't
really the concept of a "production" alternative, since we assume research
users are creating and tearing down their own workspaces.

### Example Environment Config

```yaml
# ~/.modelops/environments/dev.yaml
environment: dev
timestamp: '2024-01-15T10:30:00'
registry:
  provider: azure
  login_server: modelopsdevacrvsp.azurecr.io
  registry_name: modelopsdevacrvsp
  requires_auth: true
storage:
  provider: azure
  account_name: modelopsdevstg
  connection_string: "DefaultEndpointsProtocol=https;..."
  containers:
    - bundles
    - results
  endpoint: "https://modelopsdevstg.blob.core.windows.net"
```
