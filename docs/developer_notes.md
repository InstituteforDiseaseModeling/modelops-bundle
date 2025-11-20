# Developer Notes

## Environment Management (Internal)

The environment system uses a pinning mechanism similar to `.python-version` or `.nvmrc`. This is an implementation detail that users don't need to know about.

### How It Works

1. On `init`, the environment is pinned to `.modelops-bundle/env` file (defaults to "dev")
2. Commands read this pinned environment and load credentials from `~/.modelops/bundle-env/{env}.yaml`
3. Developers can switch environments using `mops-bundle dev switch <env>`

### Developer Commands

These commands are hidden under the `dev` subcommand to keep them away from regular users:

```bash
# Show current pinned environment
mops-bundle dev env

# Switch to different environment
mops-bundle dev switch local
mops-bundle dev switch dev
```

### Implementation Details

- `env_manager.py` handles all environment operations
- `pin_env()` writes environment name to `.modelops-bundle/env`
- `read_pinned_env()` reads the pinned environment
- `load_env_for_command()` loads credentials at runtime
- No environment field in `config.yaml` (stored separately in `env` file)

### Directory Structure

```
project/
├── .modelops-bundle/
│   ├── config.yaml     # Bundle config (no environment field)
│   ├── env            # Pinned environment name (e.g., "dev")
│   └── tracked        # Tracked files list

~/.modelops/
└── bundle-env/        # Environment configs
    ├── local.yaml     # Created by make start
    └── dev.yaml       # Created by mops infra up
```

### Environment File Format

```yaml
# ~/.modelops/bundle-env/dev.yaml
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
  endpoint: "https://modelopsdevstg.blob.core.windows.net"
```



## Registry UI + ORAS Artifacts

The `joxit/docker-registry-ui` registry UI (from the `docker-compose.yml` file)
at http://localhost:8080 won't show ORAS artifacts properly. It's designed for
Docker images, not OCI artifacts. The artifacts are there, just not displayed.

To verify artifacts exist:

```bash
# Check catalog
curl -s http://localhost:5555/v2/_catalog

# Inspect manifest
curl -s http://localhost:5555/v2/epi_model/manifests/v1 \
  -H "Accept: application/vnd.oci.image.manifest.v1+json" | jq '.'

# List files  
oras manifest fetch --plain-http localhost:5555/epi_model:v1 | \
  jq '.layers[].annotations."org.opencontainers.image.title"'

# Pull back
oras pull --plain-http localhost:5555/epi_model:v1
```

## Local Registry and SSL/TLS

The local Docker registry runs on HTTP (port 5555), not HTTPS. To use it:

```bash
# Enable insecure mode for local testing
export MODELOPS_BUNDLE_INSECURE=true

# This allows HTTP connections to localhost:5555
# Values "true", "1", or "yes" enable insecure mode
# Any other value (including unset) requires HTTPS

# For tests:
MODELOPS_BUNDLE_INSECURE=true pytest

# Or add to your shell profile for development:
echo 'export MODELOPS_BUNDLE_INSECURE=true' >> ~/.bashrc
```

**Security Note**: This only affects TLS verification, not authentication. Never use in production.

## ORAS/OCI Artifact Layers

ORAS layers ≠ Docker layers:

 - **Docker layers**: Filesystem changesets that stack to build a complete
   filesystem. Order matters.

 - **ORAS layers**: Independent blobs. Each file becomes its own layer with
   metadata. No filesystem semantics - they don't stack or merge.

Example:
```bash
oras push localhost:5555/epi_model:v1 src/model.py:text/x-python src/targets.py:text/x-python
```

Creates:
- **Layer 1**: `model.py` as blob with `org.opencontainers.image.title: "src/model.py"`  
- **Layer 2**: `targets.py` as blob with `org.opencontainers.image.title: "src/targets.py"`

## ORAS-py Path Stripping Issue

**Problem**: oras-py strips directory paths when pushing files, unlike the ORAS
CLI. See issue:
https://github.com/oras-project/oras-py/issues/217#issuecomment-3221144601  

**Root cause**: Line 786 in oras-py's provider.py:
```python
blob_name = os.path.basename(blob)  # Strips path!
layer["annotations"] = {
    oras.defaults.annotation_title: blob_name.strip(os.sep)
}
```

**Test results**:
```bash
# ORAS CLI preserves paths
oras push localhost:5555/test:v1 src/model.py data/data.csv
# manifest: {"title": "src/model.py"}, {"title": "data/data.csv"}

# oras-py strips to basename
client.push(files=['src/model.py', 'data/data.csv'])  
# manifest: {"title": "model.py"}, {"title": "data.csv"}
```

**Critical issue - name clashes cause data loss**:
```python
# Push src/model.py and lib/model.py with oras-py
# Both get title "model.py" 
# On pull, second overwrites first - DATA LOSS!
files = client.pull('localhost:5555/py-clash:v1')
# Only get one model.py with content from lib/model.py
```

### Solution Options

**Understanding the parameters:**
- `annotation_file`: JSON file that sets **per-layer** annotations (can override title)
- `manifest_annotations`: Dict that sets **manifest-level** annotations only

**Option 1: Annotation file (temp JSON) - WORKS**

```python
# Create temp file mapping paths to per-layer annotations
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=True) as f:
    annotations = {
        "src/model.py": {"org.opencontainers.image.title": "src/model.py"},
        "lib/model.py": {"org.opencontainers.image.title": "lib/model.py"}
    }
    json.dump(annotations, f)
    f.flush()
    client.push(files=files, annotation_file=f.name)
```
- ✓ Overrides basename behavior at layer level
- ✓ Each layer gets unique title with full path
- ✓ Fixes clash issue completely
- ✓ Compatible with standard ORAS pull
- ✗ Requires temp file management
- ✗ Slightly more complex code

**Option 2: Custom layer annotations - WORKS but non-standard**

```python
# Store correct path in custom annotation, let title stay wrong
for layer in manifest["layers"]:
    layer["annotations"]["modelops.bundle.path"] = full_path
    # title remains "model.py" (basename)
```
- ✓ Could store full path in our custom annotation
- ✓ No temp files needed
- ✗ org.opencontainers.image.title still wrong (basename)
- ✗ Standard ORAS tools would extract to wrong paths
- ✗ Registry UI and other tools show wrong paths
- ✗ Need custom pull logic to use our annotation
- ✗ Requires modifying oras-py or post-processing manifest

**Option 3: Manifest annotations (dict) - DOESN'T WORK for paths**

```python
manifest_annotations = {
    "modelops.bundle.paths": json.dumps({...})
}
client.push(files=files, manifest_annotations=manifest_annotations)
```
- ✗ Only sets manifest-level annotations
- ✗ Layers still get basename titles (model.py)
- ✗ Name clashes still occur at layer level
- ✗ Would need custom pull logic and complex digest mapping

**Why the difference:**

```python
# In oras-py push():
layer["annotations"] = {
    oras.defaults.annotation_title: os.path.basename(blob)  # Always basename!
}
if annotations:  # From annotation_file only
    layer["annotations"].update(annotations)  # Can override title

manifest["annotations"] = manifest_annotations  # Separate, manifest-level only
```

**Recommendation**: Annotation file (Option 1) - only solution that properly
fixes the layer title issue. Temp file management is simple with context managers.

## Development Environment Setup

### Registry Configuration Hierarchy

The registry configuration follows this precedence:

1. **Pinned environment**: Read from `.modelops-bundle/env` file
2. **Environment configs**: Loaded from `~/.modelops/bundle-env/<env>.yaml`
3. **Environment variables**: `REGISTRY_URL` for testing/overrides
4. **Auto-detection**: Local Docker containers if available

### Local Development Setup

**Option 1: Docker Compose (Recommended)**
```bash
# From modelops-bundle root directory
make start
# or: docker-compose -f dev/docker-compose.yml up -d

# Check status
make status

# Services available:
# - OCI Registry: http://localhost:5555
# - Registry UI: http://localhost:8080
# - Azure Storage: http://localhost:10000 (Azurite)

# Required for HTTP registry:
export MODELOPS_BUNDLE_INSECURE=true

# Test the setup
mops-bundle init test-project
mops-bundle add src/
mops-bundle push

# Clean up
make stop
```

**Option 2: Manual Docker Registry**
```bash
# Minimal setup - just the registry
docker run -d -p 5555:5000 --name test-registry registry:2

# Set environment
export REGISTRY_URL=localhost:5555
export MODELOPS_BUNDLE_INSECURE=true

# Test
mops-bundle push

# Clean up
docker stop test-registry && docker rm test-registry
```

### Production/Cloud Setup

**How environments work in production:**
- **Infrastructure provisioning**: `mops infra up` creates `~/.modelops/bundle-env/dev.yaml`
- **Environment pinning**: `mops-bundle init` pins the environment to `.modelops-bundle/env`
- **Automatic loading**: Commands read pinned env and load credentials automatically
- **Switching**: Use `mops-bundle dev switch <env>` to change environments

**Environment config example (`~/.modelops/bundle-env/dev.yaml`):**
```yaml
registry:
  login_server: modelopsdevacrvsb.azurecr.io
  auth_provider: azure_cli
storage:
  connection_string: DefaultEndpointsProtocol=https;...
```

**Testing against Azure ACR:**
```bash
# Ensure Azure CLI is logged in
az login
az acr login --name modelopsdevacrvsb

# Don't set MODELOPS_BUNDLE_INSECURE (uses HTTPS)
unset MODELOPS_BUNDLE_INSECURE

# Test with real ACR
cd dev/sample_projects/epi_model
mops-bundle push
```

## Testing Guide

### Test Categories

**Unit Tests (No Registry Required)**
```bash
# Fast tests that don't need external services
uv run pytest -m "not integration" -v

# These tests skip cleanly when no registry available
# Should complete in under 30 seconds
```

**Integration Tests (Registry Required)**
```bash
# Start local registry first
make start

# Run integration tests
export REGISTRY_URL=localhost:5555
export MODELOPS_BUNDLE_INSECURE=true
uv run pytest -m integration -v

# Or run all tests (unit + integration)
uv run pytest -v
```

**E2E Testing with Sample Projects**
```bash
# Built-in E2E test
make test

# Manual E2E with sample projects
cd dev/sample_projects/epi_model
mops-bundle push
mops-bundle status
mops-bundle pull ../test-pull
```

### CI Testing

**GitHub Actions setup:**
- Starts Docker registry: `docker run -d -p 5555:5000 registry:2`
- Runs unit tests: `pytest -m "not integration"`
- Runs integration tests: `pytest -m integration` (with registry available)
- Tests both local and cloud registry scenarios

**Reproduce CI locally:**
```bash
# Exactly like CI
docker run -d -p 5555:5000 --name test-registry registry:2
export REGISTRY_URL=localhost:5555
export MODELOPS_BUNDLE_INSECURE=true
uv run pytest -m integration -v
docker stop test-registry && docker rm test-registry
```

## Troubleshooting Common Issues

### Registry Connection Problems

**Error: "Error accessing registry at ..."**
1. **Check registry availability:**
   ```bash
   curl -f http://localhost:5555/v2/
   # Should return: {}
   ```

2. **Check MODELOPS_BUNDLE_INSECURE setting:**
   ```bash
   echo $MODELOPS_BUNDLE_INSECURE
   # Should be "true" for localhost:5555
   # Should be unset for cloud registries
   ```

3. **Check REGISTRY_URL:**
   ```bash
   echo $REGISTRY_URL
   # Should match your intended registry
   ```

**Error: "HTTP 401 Unauthorized" (Azure ACR)**
```bash
# Re-authenticate with Azure
az login
az acr login --name <registry-name>

# Verify authentication
az acr repository list --name <registry-name>
```

**Error: "HTTP vs HTTPS issues"**
- **localhost:5555**: MUST use `MODELOPS_BUNDLE_INSECURE=true`
- **Azure ACR**: MUST NOT use `MODELOPS_BUNDLE_INSECURE=true`
- **Cloud registries**: Always use HTTPS (secure mode)

### Environment Configuration Issues

**Problem: Wrong registry being used**
1. **Check environment precedence:**
   ```bash
   # See what environment is being used
   mops-bundle config

   # Override explicitly
   mops-bundle --env local push
   mops-bundle --registry localhost:5555/project push
   ```

2. **Check environment files:**
   ```bash
   ls ~/.modelops/environments/
   cat ~/.modelops/environments/dev.yaml
   ```

3. **Force local development mode:**
   ```bash
   # Start Docker services
   make start

   # Unset conflicting variables
   unset REGISTRY_URL
   unset MODELOPS_ENV

   # Should auto-detect local environment
   mops-bundle init test
   ```

**Problem: Tests hanging or timing out**
- **Unit tests hanging**: Check if they're accidentally marked as integration tests
- **Integration tests hanging**: Check if registry is actually running
- **DNS/network timeouts**: Don't use fake domains like `fake-registry.invalid`

### Local vs Cloud Registry Confusion

**Safe local development pattern:**
```bash
# Start local services
make start

# Set up environment clearly
export MODELOPS_BUNDLE_INSECURE=true
export REGISTRY_URL=localhost:5555

# Verify setup
curl -f http://localhost:5555/v2/
mops-bundle push

# When switching to cloud:
unset MODELOPS_BUNDLE_INSECURE
unset REGISTRY_URL
az acr login --name <registry>
mops-bundle push
```

**Safe cloud testing pattern:**
```bash
# Clean environment
unset MODELOPS_BUNDLE_INSECURE
unset REGISTRY_URL

# Authenticate
az login
az acr login --name modelopsdevacrvsb

# Test
cd dev/sample_projects/epi_model
mops-bundle push
```

## Environment Variable Reference

| Variable | Purpose | Values | Notes |
|----------|---------|--------|-------|
| `REGISTRY_URL` | Override registry for testing | `localhost:5555`, `registry.azurecr.io` | Testing only - environments are pinned |
| `MODELOPS_BUNDLE_INSECURE` | Enable HTTP (not HTTPS) | `true`, `1`, `yes` | **Only for localhost registries** |
| `DEBUG` | Verbose error output | `1` | Shows full error traces |

## Development Workflow Summary

**Daily Development:**
```bash
# Start local services once
make start

# Set environment once per shell session
export MODELOPS_BUNDLE_INSECURE=true

# Normal workflow
mops-bundle init my-project
mops-bundle add src/
mops-bundle push
mops-bundle status
```

**Testing:**
```bash
# Unit tests (no registry)
uv run pytest -m "not integration"

# Integration tests (with registry)
make start
uv run pytest -m integration

# Clean up
make stop
```

**Production deployment:**
```bash
# Environment auto-configured by mops infra up
unset MODELOPS_BUNDLE_INSECURE
mops-bundle push  # Uses Azure ACR automatically
```
