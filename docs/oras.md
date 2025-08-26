# ORAS-based Model Bundle Design

## Core ORAS Concepts

### The OCI Artifact Structure

ORAS (OCI Registry As Storage) extends the OCI image specification to store any
type of content in container registries. An ORAS artifact consists of three
main components:

1. **Manifest** - A JSON document that describes the artifact

2. **Config** - Optional structured metadata, stored as a *single blob*
   (content-addressed by digest) that the manifest points. It's like a layer in
   storage, but it's not downloaded as a "file payload".

3. **Layers** - The actual content files (an OCI concept for packaging multiple blobs)

**Simple Analogy**: Think of an artifact as a versioned folder/package, and
layers as the individual files within that folder. The manifest is like a
detailed table of contents that describes everything in the folder, while the
config is a special metadata file about the folder's purpose.

### Artifact Types

The `artifactType` property declares what kind of content the artifact
contains. This is your bundle's identity - it tells consumers "this is an model
bundle" before they even download it.

```json
"artifactType": "application/vnd.idm.modelops-bundle.v1"
```

Without `artifactType`, the system falls back to using `config.mediaType` for
identification (legacy approach). Not that the ORAS Python SDK does not yet
support `artifactType`.

### Layers vs Config

Understanding when to use layers versus config is crucial for clean design:

**Layers** (OCI concept for content storage, related but distinct from the
container layers):

- Store actual files: model code, parameter files, datasets
- Each layer is a blob with its own `mediaType`
- Content-addressed and deduplicated
- Downloaded on-demand
- Best for: Simulation scripts, data files, documentation

**Config**:

- Stores structured metadata about the artifact
- Single JSON/YAML object that can be downloaded using its descriptor.
- Often parsed by tools for decision-making
- Best for: Model parameters, population structure, simulation settings

Why the distinction? Config is meant to be small, quickly parseable metadata
that tools can use to understand the artifact without downloading all the
layers. Layers are for the actual content.

### Annotations

Annotations are simple key-value pairs for searchable metadata. They can appear
at two levels:

**Manifest annotations** (artifact-level):
```json
"annotations": {
  "org.opencontainers.image.created": "2024-01-15T10:30:00Z",
  "epi.model.type": "agent-based",
  "epi.model.disease": "influenza",
  "epi.model.population": "seattle-metro",
  "epi.model.r0": "1.4"
}
```

**Layer annotations** (file-level):
```json
{
  "mediaType": "application/x-python",
  "digest": "sha256:abc123...",
  "size": 104857600,
  "annotations": {
    "org.opencontainers.image.title": "simulation.py",
    "epi.model.component": "transmission-dynamics",
    "epi.model.validated": "2024-01-10"
  }
}
```

### Descriptors

Descriptors are the lower-level abstraction that references any content by its
`digest`, `size`, and `mediaType` (you'll see these triplets everywhere in OCI
JSON). Everything in OCI is referenced through descriptors - the manifest has a
descriptor, config has a descriptor, each layer has a descriptor. They're the
"pointers" of the OCI world.

## Design 

### Bundle as OCI Artifact

- Each bundle is stored as a standard OCI artifact in any OCI-compliant registry
- Files are stored as layers with preserved paths via annotations
- Config blob could store bundle metadata (roles, schema version) but isn't used currently
- Enables versioning, distribution, and caching through existing container infrastructure

### Local-First Architecture


- `.modelops-bundle/` directory maintains local state (`config.yaml`, `tracked`, `state.json`).

- Local tracked files list is source of truth for what belongs in bundle and
  should be published to registry on next push.

### State Synchronization Model

- Three-way diff between: local files, remote registry, last sync state
- Tracks changes via content hashes (SHA256 digests)
- Sync state records last push/pull digests for change detection
- Enables conflict detection and safe merge operations

### Push/Pull Symmetry

- Push: Uploads all tracked files as layers, creates manifest
- Pull: Downloads all layers, mirrors exact remote state
- Both operations update sync state for future diff calculations
- Provides predictable, reproducible bundle deployment

### Registry as Dumb Storage

- Registry stores blobs and manifests, no business logic
- All intelligence in client (tracking, diffing, conflict resolution)
- Supports any OCI registry without custom extensions
- Maximum portability and compatibility

### Immutable Content Addressing

- All content identified by SHA256 digest
- Deduplication happens automatically at registry level
- Enables reliable caching and integrity verification
- Digests used for both change detection and content validation
