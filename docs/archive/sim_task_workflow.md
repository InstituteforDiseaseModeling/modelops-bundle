# Simulation Task and Job Submission Workflow

## Overview

This document describes how simulation tasks flow from model registration through job submission in the ModelOps ecosystem.

## Core Architecture

### 1. Data Types (from modelops-contracts)

```
SimTask → SimBatch → SimJob → K8s Job
```

- **SimTask**: Single deterministic simulation with bundle_ref, entrypoint, params, seed
- **SimBatch**: Group of related SimTasks from same sampling method
- **SimJob**: Executable job containing one or more SimBatches
- **SimulationStudy**: Abstract experimental design (no bundle ref) that converts to SimJob

### 2. Key Workflow Steps

1. **Model Registration** (modelops-bundle)
   - Register models with `modelops-bundle register-model`
   - Tracks model code, data dependencies, and their digests
   - Stores in `.modelops-bundle/registry.yaml`

2. **Bundle Creation** (modelops-bundle)
   - Build bundle from registered models
   - Push to registry (ACR/GHCR)
   - Returns bundle digest (sha256:xxx)

3. **Study Creation** (modelops-calabaria)
   - Create SimulationStudy with parameter sets
   - Use SamplingStrategy to generate parameter combinations
   - No bundle reference yet (abstract design)

4. **Job Submission** (modelops)
   - Bind SimulationStudy to bundle_ref → creates SimJob
   - Upload job spec to blob storage
   - Create K8s Job pointing to blob
   - Runner pulls bundle and executes tasks

## Current State

### ✅ Completed
- Model registration with digest tracking
- Registry management (.modelops-bundle/registry.yaml)
- Digest computation for models and dependencies
- Status command showing model state

### 🔧 Needs Implementation

1. **Bundle Building from Registry**
   - Package registered models + data into OCI bundle
   - Include registry.yaml in bundle
   - Push to container registry

2. **Fix modelops-bundle Tests**
   - Tests failing due to missing `compute_digest` import
   - Function was moved to `hashing.py` module
   - Need to update test imports

3. **Integration Testing**
   - Test full workflow from registration to job submission
   - Verify bundle contains all tracked dependencies
   - Ensure job runner can load and execute models

## Test Errors to Fix

All test failures are due to incorrect import of `compute_digest`:

```python
# OLD (in tests):
from modelops_bundle.utils import compute_digest

# NEW (should be):
from modelops_bundle.hashing import compute_file_digest
```

Affected test files:
- `tests/test_concurrent_operations.py`
- `tests/test_diffing.py`
- `tests/test_digest_consistency.py`
- `tests/test_pull_mirror_semantics.py`
- `tests/test_pull_safety.py`
- `tests/test_push_tags.py`
- `tests/test_tag_races.py`

## Next Steps

1. Fix test imports to use `modelops_bundle.hashing.compute_file_digest`
2. Implement bundle build command that packages registry contents
3. Test job submission with real registered models
4. Create example workflow documentation

## Example Workflow (Target)

```bash
# 1. Register models
modelops-bundle register-model model:StochasticSIR --path src/model.py --data data/

# 2. Build and push bundle
modelops-bundle build --push
# Returns: sha256:abc123...

# 3. Create study and submit
python submit_job.py --bundle sha256:abc123...
```

## Key Integration Points

- **Registry → Bundle**: Registry tracks what goes into bundle
- **Bundle → Job**: Bundle digest is required for SimJob creation
- **Local → Cloud**: Status command shows sync state with cloud registry