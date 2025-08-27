# ORAS-based Model Bundle Design

## Design 

### Bundle as OCI Artifact

Each bundle is stored as a standard OCI artifact in any OCI-compliant registry.
Following the idea of ORAS, files are stored as layers with their
project-relative paths preserved via annotations (to get around the current
ORAS Python SDK basename issue).

The Config blob is used to store bundle metadata (whether files are external or
stored in the registry as blobs, roles, schema version).

The OCI-based architecture enables versioning, distribution, and caching
through existing container infrastructure.

## OCI Config as the File Manifest

OCI Manifest layers are used to register the bundle files stored *in the OCI
registry*. However, ModelOps-Bundle supports *external blob-based storage* for
larger data files. To keep track of these for seamless push-and-pull
operations, we need a mapping of the files project-relative paths to their
external URI and digest. Since it's beneficial to have a *single source of
truth*, a **BundleIndex** of *all* files is kept in the OCI Config. This has
several advantages:

 - *One small fetch → full picture*: every file path, digest, size, roles, and
   storage policy (OCI vs blob). Single source of truth.

 - *Allows external-only storage via OCI*.

 - *Atomic, and immutable metadata*: The manifest digest covers the
   config descriptor, and the config blob is itself content-addressed.

 - *Fast planning without downloads*: `pull --role <role>` operations can look
   at the BundleIndex and know what to pull.

 - *Digests in config allow for fast diffing*.

An earlier design used *pointer files* (small JSON files with URIs, digests,
etc.). This was a bad design: multiple temporary files need to be downloaded
just to compute the diffs between digests. Pointer files create N tiny round
trips and break atomicity.

## External Storage

### Local Workspace Features

The `.modelops-bundle/` directory maintains local state (`config.yaml`,
`tracked`, `state.json`).

The local tracked files list (`~/.modelops-bundle/config.yaml`) is source of
truth for what belongs in bundle and should be published to registry on next
push.

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
