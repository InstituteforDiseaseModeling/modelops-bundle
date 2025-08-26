# Developer Notes



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
