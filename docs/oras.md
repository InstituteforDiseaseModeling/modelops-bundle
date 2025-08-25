# ORAS-based Model Bundle Design

## Core ORAS Concepts

### The OCI Artifact Structure

ORAS (OCI Registry As Storage) extends the OCI image specification to store any
type of content in container registries. An ORAS artifact consists of three
main components:

1. **Manifest** - A JSON document that describes the artifact
2. **Config** - Optional structured metadata 
3. **Layers** - The actual content files (an OCI concept for packaging multiple blobs)

**Simple Analogy**: Think of an artifact as a versioned folder/package, and
layers as the individual files within that folder. The manifest is like a
detailed table of contents that describes everything in the folder, while the
config is a special metadata file about the folder's purpose.

### Artifact Types

The `artifactType` property declares what kind of content the artifact
contains. This is your bundle's identity - it tells consumers "this is an
epidemiology model bundle" before they even download it.

```json
"artifactType": "application/vnd.myorg.epi-model.v1"
```

Without `artifactType`, the system falls back to using `config.mediaType` for identification (legacy approach).

### Layers vs Config
Understanding when to use layers versus config is crucial for clean design:

**Layers** (OCI concept for content storage):
- Store actual files: model code, parameter files, datasets
- Each layer is a blob with its own mediaType
- Content-addressed and deduplicated
- Downloaded on-demand
- Best for: Simulation scripts, data files, documentation

**Config**:
- Stores structured metadata about the artifact
- Single JSON/YAML object
- Always downloaded with manifest
- Parsed by tools for decision-making
- Best for: Model parameters, population structure, simulation settings

Why the distinction? Config is meant to be small, quickly parseable metadata
that tools can use to understand the artifact without downloading all the
layers. Layers are for the actual content.

### Annotations

Annotations are simple key-value pairs for searchable metadata. They can appear
at three levels:

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
digest, size, and mediaType. Everything in OCI is referenced through
descriptors - the manifest has a descriptor, config has a descriptor, each
layer has a descriptor. They're the "pointers" of the OCI world.

## Epidemiology Model Bundle Design

### Recommended Structure

A clean epidemiology model bundle design uses ORAS primitives intentionally.
Each layer includes `modelops-bundle.bundle-layer` annotations to specify which workers/roles
need which files:

```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.manifest.v1+json",
  "artifactType": "application/vnd.myorg.epi-model.v1",
  
  "config": {
    "mediaType": "application/vnd.myorg.epi-model.config.v1+json",
    "digest": "sha256:config123...",
    "size": 2048
  },
  
  "layers": [
    {
      "mediaType": "application/x-yaml",
      "digest": "sha256:cfg456...",
      "size": 1024,
      "annotations": {
        "org.opencontainers.image.title": "config.yaml",
        "modelops-bundle.bundle-layer": "config"
      }
    },
    {
      "mediaType": "application/x-python",
      "digest": "sha256:model789...",
      "size": 8192,
      "annotations": {
        "org.opencontainers.image.title": "src/model.py",
        "modelops-bundle.bundle-layer": "compute,simulation"
      }
    },
    {
      "mediaType": "application/x-python",
      "digest": "sha256:targets012...",
      "size": 4096,
      "annotations": {
        "org.opencontainers.image.title": "src/targets.py",
        "modelops-bundle.bundle-layer": "compute,analysis"
      }
    },
    {
      "mediaType": "text/csv",
      "digest": "sha256:data345...",
      "size": 156789,
      "annotations": {
        "org.opencontainers.image.title": "data/data.csv",
        "modelops-bundle.bundle-layer": "data,compute"
      }
    },
    {
      "mediaType": "text/plain",
      "digest": "sha256:reqs678...",
      "size": 256,
      "annotations": {
        "org.opencontainers.image.title": "requirements.txt",
        "modelops-bundle.bundle-layer": "setup"
      }
    },
    {
      "mediaType": "text/markdown",
      "digest": "sha256:readme901...",
      "size": 3456,
      "annotations": {
        "org.opencontainers.image.title": "README.md",
        "modelops-bundle.bundle-layer": "documentation"
      }
    }
  ],
  
  "annotations": {
    "epi.model.name": "seattle-flu-abm",
    "epi.model.version": "3.2.0",
    "epi.model.framework": "mesa"
  }
}
```

### Config Content Example

The config blob would contain structured agent-based model metadata. Below is
an artificial example (in reality modelops won't use this exact scheme):

```json
{
  "model": {
    "type": "agent-based",
    "framework": "mesa",
    "disease": "influenza",
    "transmission_mode": "airborne",
    "timestep": "hourly"
  },
  "population": {
    "size": 100000,
    "age_distribution": "seattle-metro-2020",
    "contact_layers": ["household", "workplace", "school", "community"],
    "geography": {
      "region": "seattle-metro",
      "resolution": "census-tract"
    }
  },
  "parameters": {
    "R0": 1.4,
    "incubation_period_days": 2.1,
    "infectious_period_days": 3.5,
    "transmission_probability": 0.02,
    "vaccination_coverage": 0.45
  },
  "interventions": {
    "social_distancing": {
      "enabled": true,
      "reduction_factor": 0.3
    },
    "school_closure": {
      "enabled": false,
      "threshold": 0.05
    }
  },
  "metrics": {
    "calibration": {
      "mse_incidence": 0.023,
      "r2_hospitalizations": 0.89,
      "mae_peak_timing": 2.3
    },
    "validation": {
      "holdout_mse": 0.031,
      "cross_validation_score": 0.86
    }
  }
}
```
