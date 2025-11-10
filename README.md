# ModelOps-Bundle

[![Tests](https://github.com/institutefordiseasemodeling/modelops-bundle/actions/workflows/tests.yml/badge.svg)](https://github.com/institutefordiseasemodeling/modelops-bundle/actions/workflows/tests.yml)

OCI artifact-based packaging for reproducible simulation model distribution and
provenance tracking.

## What is ModelOps-Bundle?

ModelOps-Bundle is the reproducibility layer of the ModelOps/Calabaria platform
for simulation-based disease modeling. It provides a git-like workflow for
packaging, versioning, and distributing simulation models with their code
and data dependencies. But, it is intentionally decoupled from Git, since 
we want finer-scale tracking of model provenance and dependency invalidation.

**Key Features:**
- **Scientific Reproducibility**: Every simulation run is fully traceable to
  exact code and data versions
- **Dependency Tracking**: Automatically detects when model dependencies change
  and invalidates cached results
- **OCI-Native**: Uses industry-standard container registries for distribution
  (no custom infrastructure)
- **Model Registry**: Tracks registered models and their metadata, traveling
  WITH the bundle for versioning
- **Cloud-Agnostic Storage**: Supports Azure Blob, AWS S3, GCS, or pure OCI for
  large data files
- **Git-Like Workflow**: Familiar commands (init, add, push, pull) for
  scientists already using version control

## Why ModelOps-Bundle?

Traditional approaches to distributing simulation models face challenges:
- **Version Mismatches**: Code updates break previously calibrated models
- **Lost Dependencies**: Data files get moved or modified without tracking
- **Reproducibility Crisis**: Can't recreate exact conditions from past
  experiments
- **Manual Tracking**: Scientists manually track which code version goes with
  which data

ModelOps-Bundle solves these by:
1. **Content-Addressed Storage**: Same content always gets same SHA256 digest
2. **Atomic Bundles**: Code + data + metadata travel together as a unit
3. **Automatic Invalidation**: When dependencies change, cached results are
   marked stale
4. **Provenance Chain**: Every simulation result links back to exact bundle
   digest

## Installation

ModelOps-Bundle is typically installed as part of the full ModelOps suite, see
the directions at
[ModelOps](https://github.com/InstituteforDiseaseModeling/modelops).

<!---->
<!-- TODO: add later when repo is public -->
<!-- ```bash -->
<!-- # Install complete ModelOps suite (recommended) -->
<!-- curl -sSL https://raw.githubusercontent.com/institutefordiseasemodeling/modelops/main/install.sh | bash -->
<!---->
<!-- # Or install standalone with uv -->
<!-- uv tool install modelops-bundle@git+https://github.com/institutefordiseasemodeling/modelops-bundle.git -->
<!-- ``` -->

## Quick Start

### 1. Initialize a Project

```bash
# Create a new project
mkdir seir-model
cd seir-model
mops bundle init .

# Or initialize existing project
cd my-existing-model
mops bundle init .
```

This creates:
- `.modelops-bundle/` - Bundle metadata directory
- `pyproject.toml` - Python project configuration
- `.modelopsignore` - Files to exclude from bundle

### 2. Register Your Simulation Model

```bash
# Register a Starsim model with its data dependencies
mops bundle register-model models/seir.py --class StarsimSIR \
  --data data/demographics.csv \
  --data data/contact_patterns.csv

# Auto-discovers BaseModel subclasses if class not specified
mops bundle register-model models/network_model.py --data data/
```

**Why Register Models?**
- Enables automatic discovery by execution workers
- Tracks which data files each model depends on
- Computes content digests for cache invalidation
- Your model code stays clean - no decorators or imports needed!

### 3. Check Bundle Status

```bash
mops bundle status

Bundle: modelopsdevacrvsp.azurecr.io/seir-model:latest
Local changes: 3 files modified

Registered Models (2)
─────────────────────────────────────────────────
Model              Status      Dependencies    Cloud
StarsimSIR         ✓ Ready     4 files        Not pushed
NetworkSEIR        ⚠ Stale     2 modified     Not pushed

Run 'mops bundle push' to sync with cloud
```

### 4. Push to Registry

```bash
# Push to configured registry
mops bundle push

# Or push with specific tag
mops bundle push --tag v1.0.0
```

## Core Concepts

### Bundle Structure

A bundle is an OCI artifact containing:

```
my-model-bundle/
├── .modelops-bundle/
│   ├── registry.yaml      # Model registry (travels with bundle)
│   ├── config.yaml        # Bundle configuration
│   └── manifest.yaml      # Content manifest with digests
├── models/
│   ├── seir.py           # Simulation model code
│   └── network.py        # Alternative model implementation
├── data/
│   ├── demographics.csv  # Input data
│   └── contacts.csv      # Contact matrices
├── targets/              # Calibration targets (optional)
│   └── incidence.py      # Target definitions
└── pyproject.toml        # Python dependencies
```

### Model Registry

The registry (`.modelops-bundle/registry.yaml`) tracks:
- Model entry points (module:Class)
- Data dependencies with SHA256 digests
- Code dependencies
- Model parameters and outputs
- Scenarios for each model

This registry is versioned WITH the bundle, ensuring metadata stays synchronized with code.

### Content Addressing

Every file gets a SHA256 digest. When ModelOps executes a simulation:
1. Worker pulls bundle by digest (immutable)
2. Verifies all file digests match registry
3. Runs simulation with exact code/data
4. Results tagged with bundle digest for provenance

## Advanced Usage

### Working with Large Data Files

For bundles with large data files (>50MB), ModelOps-Bundle automatically uses blob storage:

```yaml
# .modelops-bundle/config.yaml
storage:
  mode: auto              # auto, blob, or oci
  threshold_bytes: 52428800  # 50MB
  provider: azure         # or s3, gcs
  container: modelops-blobs
```

### Calibration Target Registration

Register calibration targets that define how models compare to observed data:

```bash
# Register target functions
mops bundle register-target targets/incidence.py

# Targets use Calabaria decorators but are tracked by bundle
# for reproducibility
```

### Bundle Comparison

Compare local changes with registry:

```bash
# Show what would be pushed
mops bundle diff

# Compare with specific version
mops bundle diff --ref v1.0.0

# Show file-level changes
mops bundle status --files
```

### Pulling Remote Bundles

```bash
# Pull latest (won't overwrite local changes)
mops bundle pull

# Force overwrite local changes
mops bundle pull --overwrite

# Pull specific version
mops bundle pull --ref sha256:abc123...
```

## Integration with ModelOps Workflow

ModelOps-Bundle integrates seamlessly with the full platform:

1. **Development**: Scientists develop models locally
2. **Registration**: Models registered with `mops bundle register-model`
3. **Pushing**: Bundle pushed to registry with `mops bundle push`
4. **Study Design**: Calabaria creates parameter sweeps referencing bundle models
5. **Execution**: Workers pull bundle and discover models via registry
6. **Provenance**: Results tagged with bundle digest for reproducibility

### Monitoring Job Execution

After submitting jobs with `mops jobs submit`, you can monitor the Dask cluster
executing your bundled models:

```bash
# Port-forward to access Dask dashboard (run in separate terminals or use &)
kubectl port-forward -n modelops-dask-dev svc/dask-scheduler 8787:8787 &
kubectl port-forward -n modelops-dask-dev svc/dask-scheduler 8786:8786 &

# Access Dask dashboard at http://localhost:8787
# Workers connect via port 8786
```

This lets you monitor task progress, worker utilization, and debug any issues
with your bundle execution in real-time.

## Lower-Level Bundle Operations

For fine-grained control over bundle contents:

### Add Files

```bash
# Add specific files
mops bundle add src/utils.py config/settings.yaml

# Add directories recursively
mops bundle add src/ data/

# Add everything (respects .modelopsignore)
mops bundle add .
```

### Remove Files

```bash
# Stop tracking files (keeps on disk)
mops bundle remove src/old_model.py

# Untrack AND delete files
mops bundle remove --rm tmp/
```

### File Status

```bash
# Show all tracked files
mops bundle status --files

# Show only untracked files
mops bundle status --untracked-only
```

## Development

For development and testing:

```bash
# Clone the repository
git clone https://github.com/institutefordiseasemodeling/modelops-bundle.git
cd modelops-bundle

# Install in development mode
uv pip install -e .

# Start local registry for testing
cd dev
docker compose up -d

# Run tests
uv run pytest

# Run with local registry
export REGISTRY_URL=localhost:5555
export MODELOPS_BUNDLE_INSECURE=true
mops bundle push
```

## Environment Configuration

ModelOps-Bundle uses environment configurations from `~/.modelops/bundle-env/`
which are automatically created when you provision ModelOps infrastructure with
`mops infra up`.

These YAML files contain your registry and storage settings:

```yaml
# ~/.modelops/bundle-env/dev.yaml
environment: dev
registry:
  provider: docker
  login_server: modelopsdevacr.azurecr.io
storage:
  provider: azure
  container: bundle-blobs
  connection_string: "DefaultEndpointsProtocol=..."
```

When you run bundle commands, the appropriate environment is loaded automatically:
- Projects are initialized with an environment (e.g., `mops bundle init --env dev`)
- The environment is pinned in `.modelops-bundle/env`
- Credentials are loaded from the environment file when needed

For local development/testing, you can create a `local.yaml` environment that uses
a local Docker registry (see Development section).

## Related Projects

- **[modelops](https://github.com/institutefordiseasemodeling/modelops)** - Infrastructure orchestration
- **[modelops-contracts](https://github.com/institutefordiseasemodeling/modelops-contracts)** - API contracts
- **[modelops-calabaria](https://github.com/institutefordiseasemodeling/modelops-calabaria)** - Science framework

## License

MIT

## Support

- **Issues**: [GitHub Issues](https://github.com/institutefordiseasemodeling/modelops-bundle/issues)
- **Discussions**: [GitHub Discussions](https://github.com/institutefordiseasemodeling/modelops-bundle/discussions)
