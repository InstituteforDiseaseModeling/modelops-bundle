# Developer Notes




## Registry UI + ORAS Artifacts

Registry UI at http://localhost:8080 won't show ORAS artifacts properly. It's
designed for Docker images, not OCI artifacts. The artifacts are there, just
not displayed.

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

ORAS layers != Docker layers:

**Docker layers**: Filesystem changesets that stack to build a complete filesystem. Order matters.

**ORAS layers**: Independent blobs. Each file becomes its own layer with metadata. No filesystem semantics - they don't stack or merge.

Example:
```bash
oras push localhost:5555/epi_model:v1 src/model.py:text/x-python src/targets.py:text/x-python
```

Creates:
- **Layer 1**: `model.py` as blob with `org.opencontainers.image.title: "src/model.py"`  
- **Layer 2**: `targets.py` as blob with `org.opencontainers.image.title: "src/targets.py"`

No deduplication, no compression by default, flat structure. Pull recreates files from annotations.
