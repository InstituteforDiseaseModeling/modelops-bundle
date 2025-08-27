# Storage Architecture

ModelOps Bundle supports hybrid storage, combining OCI registry layers for small files with external blob storage for large datasets. This enables efficient distribution of models with multi-gigabyte parameter files while maintaining the simplicity of OCI-based versioning.

## Core Concepts

### BundleIndex

The **BundleIndex** is the single source of truth for all files in a bundle, stored as the OCI manifest's config blob. It contains:

- Complete file manifest with paths, digests, and sizes
- Storage location for each file (OCI layer vs external blob)
- Blob references for externally stored files
- Bundle metadata and creation timestamp

This design enables:
- Fast diffing without downloading files
- Atomic operations with immutable references
- Single fetch to understand entire bundle structure
- Support for external-only storage scenarios

### Storage Types

Files are classified into two storage types:

- **OCI**: Stored as layers in the OCI registry (default for files < 50MB)
- **BLOB**: Stored in external blob storage (Azure, S3, GCS, or filesystem)

### Content Addressing

All content uses SHA256 digests for:
- Deduplication and integrity verification
- Immutable references to specific versions
- Efficient caching and change detection
- Content validation on pull operations

## Configuration

### Default Behavior

By default, ModelOps Bundle uses OCI-only storage with no external dependencies:

```yaml
# .modelops-bundle/config.yaml
registry_ref: localhost:5555/my-model
default_tag: latest
storage:
  enabled: true
  mode: auto          # Hybrid mode (default)
  threshold_bytes: 52428800  # 50MB
  provider: ""        # Empty = OCI-only
```

### Enabling External Storage

To enable external blob storage for large files:

```yaml
# .modelops-bundle/config.yaml
storage:
  enabled: true
  mode: auto
  threshold_bytes: 10485760  # 10MB threshold
  provider: azure
  container: modelops-bundles
  prefix: models/  # Optional key prefix
```

### Storage Providers

| Provider | Description | Configuration |
|----------|-------------|---------------|
| `""` (empty) | OCI-only, no external storage | Default, no setup needed |
| `azure` | Azure Blob Storage | Requires `AZURE_STORAGE_CONNECTION_STRING` |
| `fs` | Local filesystem | For testing, requires `container` path |
| `s3` | Amazon S3 (future) | Will require AWS credentials |
| `gcs` | Google Cloud Storage (future) | Will require GCP credentials |

## Storage Policy

The storage policy determines where each file is stored based on size and patterns.

### Classification Rules

Files are classified in the following order:

1. **Force patterns**: Explicit patterns override all other rules
2. **Size threshold**: Files above threshold go to blob storage
3. **Mode override**: `oci-inline` or `blob-only` force all files to one type

### Configuration Options

```yaml
storage:
  # Mode determines overall behavior
  mode: auto           # "auto" | "oci-inline" | "blob-only"
  
  # Size threshold for auto mode (bytes)
  threshold_bytes: 52428800  # 50MB default
  
  # Pattern-based overrides (glob patterns)
  force_blob_patterns:
    - "*.safetensors"  # Always store in blob
    - "*.ckpt"
    - "data/*.parquet"
    
  force_oci_patterns:
    - "*.py"           # Always store in OCI
    - "*.yaml"
    - "docs/*"
```

### Storage Modes

- **`auto`** (default): Hybrid storage based on size and patterns
- **`oci-inline`**: Force all files to OCI layers (no external storage)
- **`blob-only`**: Force all files to external storage (requires provider)

## CLI Usage

### Push with Storage

When pushing with external storage enabled:

```bash
# Files are automatically classified by policy
modelops-bundle push

# Output shows storage decisions
Uploading 3 files (1.2 GB total)
  → src/model.py (1.5 KB) [OCI]
  → config.yaml (256 B) [OCI]
  → weights.safetensors (1.2 GB) [BLOB]
Successfully pushed localhost:5555/my-model:latest
```

### Pull with Storage

Pull operations transparently handle both storage types:

```bash
# Pulls from both OCI and blob storage
modelops-bundle pull

# Downloads files from appropriate sources
Downloading 3 files (1.2 GB total)
  ✓ src/model.py (from OCI)
  ✓ config.yaml (from OCI)
  ✓ weights.safetensors (from Azure blob)
```

### Inspect Manifest

The manifest command shows storage information:

```bash
modelops-bundle manifest inspect

# Shows storage column when index is available
Files in sha256:abc123...
PATH                    SIZE      DIGEST                         STORAGE
src/model.py           1.5 KB    sha256:def456...              OCI
config.yaml            256 B     sha256:789012...              OCI
weights.safetensors    1.2 GB    sha256:345678...              BLOB
```

## Architecture Details

### BundleIndex Structure

The BundleIndex is stored as the OCI manifest config:

```json
{
  "version": "1.0",
  "created": "2024-01-15T10:30:00Z",
  "tool": {
    "name": "modelops-bundle",
    "version": "0.1.0"
  },
  "files": {
    "src/model.py": {
      "path": "src/model.py",
      "digest": "sha256:abc123...",
      "size": 1536,
      "storage": "oci"
    },
    "weights.safetensors": {
      "path": "weights.safetensors",
      "digest": "sha256:def456...",
      "size": 1234567890,
      "storage": "blob",
      "blobRef": {
        "uri": "azure://modelops-bundles/models/de/f4/def456..."
      }
    }
  }
}
```

### Blob URI Format

External blobs use URIs with content-addressed paths:

```
<provider>://<container>/<prefix>/<first2>/<next2>/<sha256>

Examples:
azure://my-container/models/ab/cd/abcdef1234...
fs:///var/storage/12/34/1234567890...
s3://my-bucket/ml/fe/dc/fedcba9876...
```

The sharded path structure (first 2 chars / next 2 chars / full hash) prevents filesystem limitations with too many files in one directory.

### Two-Phase Operations

All operations use a two-phase pattern for race safety:

1. **Plan Phase**: Resolve tags to digests, capture current state
2. **Apply Phase**: Execute using immutable digests

This ensures operations are atomic and predictable even with concurrent tag updates.

## Provider Setup

### Azure Blob Storage

1. Set connection string environment variable:
```bash
export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;..."
```

2. Configure in bundle:
```yaml
storage:
  provider: azure
  container: my-container  # Must exist
  prefix: models/          # Optional
```

3. Ensure container exists in Azure:
```bash
az storage container create --name my-container
```

### Filesystem (Testing)

For local testing without cloud dependencies:

```yaml
storage:
  provider: fs
  container: /var/modelops/storage  # Absolute path
```

Directory structure created automatically:
```
/var/modelops/storage/
  ab/cd/abcdef1234...  # Content-addressed files
  12/34/1234567890...
```

## Examples

### Basic OCI-Only Workflow

Default configuration with all files in OCI:

```yaml
# .modelops-bundle/config.yaml
registry_ref: localhost:5555/small-model
storage:
  mode: oci-inline  # Force all to OCI
```

### Hybrid Storage for Large Models

Optimal for ML models with large weight files:

```yaml
storage:
  mode: auto
  threshold_bytes: 10485760  # 10MB
  provider: azure
  container: ml-artifacts
  force_blob_patterns:
    - "*.safetensors"
    - "*.ckpt"
    - "*.pt"
    - "*.h5"
```

### Dataset Management

Keep code in OCI, datasets in blob:

```yaml
storage:
  mode: auto
  threshold_bytes: 1048576  # 1MB
  force_oci_patterns:
    - "src/**"
    - "*.py"
    - "*.yaml"
    - "README.md"
  force_blob_patterns:
    - "data/**"
    - "*.parquet"
    - "*.csv"
```

## Best Practices

### When to Use External Storage

Use external blob storage when:
- Files exceed 50MB (configurable threshold)
- Working with large ML model weights
- Datasets that change independently of code
- Organizations with existing blob storage infrastructure

Stay with OCI-only when:
- All files are small (< 50MB)
- Maximum portability is needed
- Testing or development environments
- No cloud storage available

### Optimizing Thresholds

- **10MB**: Aggressive blob storage, minimizes registry size
- **50MB** (default): Balanced approach for most use cases
- **100MB**: Conservative, only largest files in blob
- **1GB**: Very conservative, almost everything in OCI

### Security Considerations

- Store connection strings in environment variables, never in config
- Use managed identities when possible (Azure)
- Implement least-privilege access to blob containers
- Consider encryption at rest for sensitive data
- Audit blob access separately from registry access

### Pattern Recommendations

Always force to OCI:
- Source code (`*.py`, `*.js`, `*.go`)
- Configuration (`*.yaml`, `*.json`, `*.toml`)
- Documentation (`*.md`, `*.txt`)
- Small scripts and utilities

Always force to blob:
- ML weights (`*.safetensors`, `*.ckpt`, `*.pt`)
- Large datasets (`*.parquet`, `*.arrow`)
- Media files (`*.mp4`, `*.wav`)
- Compressed archives (`*.tar.gz`, `*.zip`)

## Troubleshooting

### Missing BundleIndex

If you see "No BundleIndex found - fall back to legacy", the artifact was pushed without storage enabled. Re-push with storage enabled to create the index.

### Azure Connection Errors

Ensure `AZURE_STORAGE_CONNECTION_STRING` is set and container exists:

```bash
# Test connection
az storage container show --name your-container

# Create if needed
az storage container create --name your-container
```

### File Not Found in Blob

Check the blob reference in the manifest:

```bash
# Inspect manifest to see blob URIs
modelops-bundle manifest inspect

# Manually check blob exists (Azure)
az storage blob exists \
  --container-name my-container \
  --name "models/ab/cd/abcdef..."
```

### Performance Issues

- Increase threshold to keep more files in OCI (faster for small files)
- Use patterns to force frequently accessed files to OCI
- Consider geographic proximity of blob storage to compute
- Enable blob storage CDN for read-heavy workloads
