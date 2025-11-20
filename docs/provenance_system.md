# Provenance-Based Model Dependency Tracking

## Executive Summary

This document describes the provenance system that connects model bundle registration (in `modelops-bundle`) with cloud-based caching and invalidation (in `modelops`). The key insight is that by explicitly tracking model dependencies through a **model registry**, we can automatically invalidate cached results when ANY dependency changes, providing complete reproducibility and efficient re-computation.

## The Problem

In computational modeling and simulation, we face a fundamental challenge: **When should we re-run expensive computations?**

Consider an epidemiological model that:
- Takes 10 minutes to run a single simulation
- Needs 10,000 runs for proper calibration
- Depends on demographic data, contact matrices, and utility functions
- Has results cached from previous runs

If someone updates the contact matrix data file, which cached results are still valid? Traditional approaches either:
1. **Over-invalidate**: Clear everything and re-run (wasteful)
2. **Under-invalidate**: Miss the change and use stale results (incorrect)
3. **Manual tracking**: Require users to version everything (error-prone)

## The Solution: Input-Addressed Storage

The ModelOps platform uses **provenance-based storage** where every simulation result is stored at a path determined by its inputs:

```
storage/
└── sims/
    └── {hash(bundle_digest)}/     # Code version
        └── {hash(param_id)}/       # Parameter set
            └── seed_42/            # Random seed
                └── result.parquet  # Simulation output
```

When ANY input changes, the path changes, automatically creating a cache miss.

## The Model Registry

The model registry is the centerpiece of the provenance system. It provides:

### 1. Explicit Dependency Declaration

Instead of trying to automatically detect dependencies (which is error-prone), models explicitly declare what they depend on:

```python
# In .modelops-bundle/registry.yaml
models:
  seir_model:
    path: src/models/seir.py
    class_name: StochasticSEIR
    data:
      - data/demographics.csv      # Population data
      - data/contact_matrix.csv    # Age-stratified contacts
    code:
      - src/utils/calculations.py  # R0 calculation functions
    outputs:
      - prevalence                 # For prevalence_target
      - incidence                   # For incidence_target
```

### 2. Semantic Hashing

The registry uses two types of hashing:

**Token Hashing (Python Files)**:
- Parses Python into tokens, ignoring formatting and comments
- Only semantic changes (logic, variable names) change the hash
- Allows reformatting without cache invalidation

**Binary Hashing (Data Files)**:
- Standard SHA256 of file contents
- Any byte change invalidates the cache
- Appropriate for CSV, JSON, YAML data files

### 3. Composite Digest Generation

The bundle digest combines all dependencies with domain separation:

```python
def compute_composite_digest(components, env_digest):
    h = hashlib.blake2b(digest_size=32)

    # Environment component
    h.update(b'\x00ENV\x00' + env_digest.encode() + b'\x00')

    # Each dependency with type prefix
    for kind, path, digest in sorted(components):
        h.update(b'\x00' + kind.encode() + b'\x00')
        h.update(path.encode() + b'\x00')
        h.update(digest.encode() + b'\x00')

    return h.hexdigest()
```

Domain separation using null bytes prevents collision attacks where different inputs could produce the same digest.

## Architecture Overview

### 1. Bundle Registration (modelops-bundle)

Users explicitly declare dependencies when registering models:

```bash
# Register a model with its dependencies
mops-bundle register-model src/models/seir.py \
  --data data/demographics.csv \
  --data config/parameters.yaml \
  --code src/utils/calculations.py

# Register a target for evaluation
mops-bundle register-target src/targets/prevalence.py \
  --output prevalence \
  --observation data/observed_cases.csv
```

This creates a registry (`registry.yaml`):

```yaml
models:
  seir:
    path: src/models/seir.py
    class_name: StochasticSEIR
    outputs: [prevalence, peak_infections, final_size]
    data:
      - data/demographics.csv
      - config/parameters.yaml
    code:
      - src/utils/calculations.py
    model_digest: "a3f2b8c9d4e5..."  # Token hash of model code

targets:
  prevalence:
    path: src/targets/prevalence.py
    model_output: prevalence
    observation: data/observed_cases.csv
```

### 2. Digest Computation Details

The system uses specialized hashing for different file types:

#### Token-Based Hashing (for Python code)
```python
def token_hash(path: Path) -> str:
    """Hash Python file based on semantic tokens, ignoring formatting.

    This provides deterministic hashing that ignores:
    - Whitespace changes
    - Comment changes
    - Indentation style
    - Blank lines

    Returns 64-char BLAKE2b hex digest.
    """
    tokens = []
    for token in tokenize.generate_tokens(...):
        if token.type not in SKIP_TOKENS:  # Skip comments, whitespace
            tokens.append((token.type, token.string))

    return blake2b(str(tokens).encode()).hexdigest()
```

**Why this matters**: Reformatting code or adding comments doesn't invalidate cached results!

#### SHA256 Hashing (for data files)
```python
def file_hash(path: Path) -> str:
    """Hash file contents with SHA256.

    ANY byte change produces different hash.
    Used for data files where every bit matters.
    """
    return sha256(path.read_bytes()).hexdigest()
```

**Why this matters**: Even a single value change in data triggers re-computation!

### 3. Bundle Digest Generation

The bundle digest combines ALL dependencies with proper domain separation:

```python
def compute_bundle_digest(registry: BundleRegistry, env: EnvironmentDigest) -> str:
    """Compute composite digest of all dependencies.

    Changes to ANY dependency change the bundle digest,
    which changes storage paths, invalidating cache.

    Uses domain separation to prevent ambiguity and ensure determinism.
    """
    # Collect components with type and path for sorting
    components = []

    for model in registry.models.values():
        # Model code (semantic changes only)
        components.append(("MODEL_CODE", model.path, token_hash(model.path)))

        # Data files (any change)
        for data_file in model.data:
            components.append(("DATA", data_file, file_hash(data_file)))

        # Code dependencies (semantic changes)
        for code_file in model.code:
            components.append(("CODE_DEP", code_file, token_hash(code_file)))

    # Sort for deterministic ordering
    components.sort()

    # Build digest with domain separation
    h = blake2b(digest_size=32)

    # Include environment digest
    h.update(b'\x00ENV\x00' + env.compute_digest().encode() + b'\x00')

    # Add each component with proper separation
    for kind, path, digest in components:
        h.update(b'\x00' + kind.encode() + b'\x00' +
                str(path).encode() + b'\x00' +
                digest.encode() + b'\x00')

    return h.hexdigest()
```

### 4. Environment Tracking

To ensure reproducibility, the system tracks the complete execution environment:

```python
@dataclass(frozen=True)
class EnvironmentDigest:
    """Track all environment factors affecting reproducibility."""
    python_version: str  # e.g., "3.11.5"
    platform: str  # e.g., "linux-x86_64"
    dependencies: Dict[str, str]  # pkg -> version+build
    container_image: Optional[str] = None  # OCI digest if containerized
    cuda_version: Optional[str] = None  # For GPU workloads
    rng_algorithm: str = "PCG64"  # NumPy RNG algorithm
    thread_count: int = 1  # OMP_NUM_THREADS for determinism

    def compute_digest(self) -> str:
        """Generate stable digest of environment."""
        env_dict = {
            "python": self.python_version,
            "platform": self.platform,
            "deps": sorted(self.dependencies.items()),
            "container": self.container_image,
            "cuda": self.cuda_version,
            "rng": self.rng_algorithm,
            "threads": self.thread_count
        }
        # Use canonical JSON serialization
        canonical = json.dumps(env_dict, sort_keys=True, separators=(',', ':'))
        return blake2b(canonical.encode(), digest_size=32).hexdigest()
```

**Why environment tracking matters**:
- Same code + data can produce different results with different NumPy versions
- BLAS implementations affect numerical results
- Thread counts can change operation order
- Container images provide full reproducibility

### 5. ProvenanceStore (in modelops)

The ProvenanceStore uses these digests to create storage paths:

```python
from modelops_contracts import make_param_id  # Already uses BLAKE2b!

class ProvenanceSchema(BaseModel):
    """Declarative schema for storage paths using bundle digest."""

    sim_path_template: str = (
        "v1/sims/{hash(bundle_digest)[:12]}/"  # Version + code prefix
        "{shard(param_id,2,2)}/"               # Directory sharding
        "params_{param_id[:16]}/"              # Parameter set (16 hex)
        "seed_{seed}"                          # Random seed
    )

class ProvenanceStore:
    """Storage that automatically invalidates when inputs change.

    IMPORTANT: Uses atomic writes to prevent corruption.
    """

    def get_sim(self, task: SimTask) -> Optional[SimReturn]:
        """Retrieve cached result if exists."""
        path = self.schema.sim_path(
            bundle_digest=task.bundle_digest,  # From manifest
            param_id=make_param_id(task.params),  # Deterministic!
            seed=task.seed
        )

        if path.exists():
            return load_result(path)  # Cache hit!
        return None  # Cache miss - need to compute

    def put_sim(self, task: SimTask, result: SimReturn):
        """Store result at provenance-determined path.

        Uses atomic write: temp file + fsync + rename.
        """
        path = self.schema.sim_path(...)
        atomic_write(path, result)  # Already implemented!
```

## Implementation Details

### Registry Classes (modelops_bundle/registry.py)

**ModelEntry**: Tracks a single model and its dependencies
```python
class ModelEntry(BaseModel):
    path: Path                    # Model Python file
    class_name: str               # Class to instantiate
    outputs: List[str]            # Named outputs
    data: List[Path]              # Data dependencies
    code: List[Path]              # Code dependencies
    model_digest: Optional[str]   # Computed token hash
```

**TargetEntry**: Tracks calibration targets
```python
class TargetEntry(BaseModel):
    path: Path                    # Target Python file
    model_output: str             # Which model output to use
    observation: Path             # Observation data
    target_digest: Optional[str]  # Computed token hash
```

**BundleRegistry**: Central registry managing all models and targets
```python
class BundleRegistry(BaseModel):
    models: Dict[str, ModelEntry]
    targets: Dict[str, TargetEntry]

    def validate(self) -> List[str]:
        """Check all dependencies exist"""

    def compute_all_digests(self) -> None:
        """Update all semantic hashes"""
```

### Hashing Module (modelops_bundle/hashing.py)

**Token-based hashing for Python**:
- Uses Python's tokenize module
- Skips COMMENT, NL, NEWLINE, INDENT, DEDENT tokens
- Produces identical hash for semantically equivalent code

**Composite digest with domain separation**:
- Prevents length-extension attacks
- Ensures different component types can't collide
- Deterministic ordering for reproducibility

### Environment Tracking (modelops_contracts/environment.py)

The EnvironmentDigest captures execution environment:
```python
@dataclass(frozen=True)
class EnvironmentDigest:
    python_version: str
    platform: str
    dependencies: Dict[str, str]
    container_image: Optional[str]
    cuda_version: Optional[str]
    rng_algorithm: str = "PCG64"
    thread_count: int = 1
```

This ensures results are only reused when the environment matches exactly.

## Complete Workflow Example

### Step 1: Initial Setup

A researcher has an SEIR model with demographic data:

```python
# src/models/seir.py
class StochasticSEIR(BaseModel):
    @model_output("prevalence")
    def extract_prevalence(self, raw, seed):
        return pl.DataFrame({
            "time": raw["time"],
            "infected": raw["I"] / raw["N"]
        })
```

They register it:

```bash
mops-bundle register-model src/models/seir.py \
  --data data/population.csv \
  --data config/contact_matrix.csv
```

### Step 2: First Run

```bash
# Push to cloud
mops-bundle push

# Run calibration (10,000 simulations)
mops run-calibration --model seir --samples 10000
```

Cloud computes:
- `bundle_digest` = `a3f2b8c9...` (based on all files)
- Runs 10,000 simulations
- Stores each at: `sims/a3f2b8c9/{param_id}/seed_{n}/`

### Step 3: Data Update

Researcher updates contact matrix with new survey data:

```bash
# Edit the contact matrix
vi config/contact_matrix.csv  # Change some values

# Push updated bundle
mops-bundle push
```

New bundle:
- `bundle_digest` = `d5e6f7a8...` (different!)
- Previous results at `sims/a3f2b8c9/...` still exist
- New results will go to `sims/d5e6f7a8/...`

### Step 4: Automatic Invalidation

```bash
# Re-run calibration
mops run-calibration --model seir --samples 10000
```

ProvenanceStore checks each simulation:
- Looks for results at `sims/d5e6f7a8/{param_id}/seed_{n}/`
- **All cache misses!** (new bundle digest)
- Automatically re-runs everything
- No stale results possible!

### Step 5: Code Formatting (No Invalidation)

```bash
# Reformat code with black
black src/models/seir.py

# Push
mops-bundle push
```

Token hashing ignores formatting:
- `model_digest` unchanged
- `bundle_digest` unchanged
- **All cached results still valid!**
- No unnecessary re-computation

## Engineering Tradeoffs

### 1. Explicit vs Automatic Dependencies

**Our choice**: Explicit declaration via `--data` and `--code` flags

**Alternative**: Auto-discover by parsing imports and file I/O

**Tradeoffs**:
- ✅ **Explicit**: User knows exactly what's tracked
- ✅ **Explicit**: No surprise invalidations
- ✅ **Explicit**: Can exclude test/debug files
- ❌ **Explicit**: More work for user
- ❌ **Explicit**: Might forget dependencies

**Mitigation**: Validate imports on register, error if missing:
```python
def validate_imports(model_path, declared_code):
    imports = parse_imports(model_path)
    for imp in imports:
        if is_local(imp) and imp not in declared_code:
            raise ValueError(f"Missing dependency: {imp}")
```

### 2. Token Hashing vs AST Hashing

**Our choice**: Token-based hashing (simpler)

**Alternative**: Full AST comparison

**Tradeoffs**:
- ✅ **Tokens**: Simple, fast, good enough
- ✅ **Tokens**: Handles 99% of cases
- ❌ **Tokens**: Variable rename causes invalidation
- ❌ **AST**: Complex, slower
- ✅ **AST**: Could ignore variable names

**Decision**: Token hashing is sufficient for MVP.

### 3. Storage Path Strategy

**Our choice**: Input-addressed (hash of inputs)

**Alternative**: Content-addressed (hash of outputs)

**Tradeoffs**:
- ✅ **Input**: Can check cache before computing
- ✅ **Input**: Deterministic paths
- ✅ **Input**: Natural invalidation
- ❌ **Content**: Must compute first
- ✅ **Content**: Deduplicates identical results

**Decision**: Input-addressed enables cache checking before expensive computation.

### 4. Runtime Dependency Discovery

**Our approach**: Static declaration with runtime validation

**Implementation**: `mops-bundle doctor` command

```python
def runtime_dependency_tracker():
    """Monkeypatch I/O operations to track actual file access."""
    accessed_files = set()

    # Hook file operations
    original_open = builtins.open
    def tracked_open(path, *args, **kwargs):
        accessed_files.add(Path(path).resolve())
        return original_open(path, *args, **kwargs)
    builtins.open = tracked_open

    # Hook imports
    original_import = builtins.__import__
    def tracked_import(name, *args, **kwargs):
        module = original_import(name, *args, **kwargs)
        if hasattr(module, '__file__'):
            accessed_files.add(Path(module.__file__))
        return module
    builtins.__import__ = tracked_import

    return accessed_files
```

**Usage**:
```bash
# Detect undeclared dependencies
$ mops-bundle doctor
⚠ Detected access to undeclared files:
  - src/utils/helpers.py (imported by model)
  - data/supplemental.csv (read during simulation)

Suggested fixes:
  mops-bundle add --code src/utils/helpers.py
  mops-bundle add --data data/supplemental.csv
```

**Benefits**:
- Catches dynamic imports and runtime file access
- Prevents cache hits when undeclared deps change
- Improves provenance completeness

## Why This Design is Brilliant

The brilliance lies in the **composition of simple ideas**:

1. **Explicit dependency tracking** → Know what affects results
2. **Semantic hashing for code** → Ignore irrelevant changes
3. **Composite bundle digest** → Single version for everything
4. **Input-addressed storage** → Automatic cache invalidation
5. **Provenance paths** → Results traceable to exact inputs

Together, these create a system where:
- **Correctness is automatic**: Changed input = different path = cache miss
- **Efficiency is automatic**: Unchanged input = same path = cache hit
- **Provenance is automatic**: Path encodes complete input history
- **User experience is simple**: Just declare dependencies

The user never thinks about cache invalidation - it just works!

## Critical Production Considerations

### 1. Param ID Determinism ✅ (Already Solved!)

**Important**: The system already uses `make_param_id()` from modelops-contracts which implements deterministic hashing with BLAKE2b and canonical JSON serialization. We do NOT use Python's built-in `hash()` function.

```python
from modelops_contracts import make_param_id

# Deterministic across processes and runs
param_id = make_param_id({"beta": 0.5, "gamma": 0.25})
# Always produces same hash for same params
```

### 2. Atomic File Operations ✅ (Already Implemented!)

modelops-bundle includes atomic write operations to prevent corruption:

```python
def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write with temp file + fsync + rename."""
    # Create temp file in same directory
    with tempfile.NamedTemporaryFile(...) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())  # Ensure on disk
    # Atomic rename
    os.replace(tmp, path)
```

### 3. Large File Performance

For efficient hashing of large datasets:

```python
@dataclass
class DigestCache:
    """Cache digests to avoid re-hashing large files."""

    def get_digest(self, path: Path) -> str:
        stat = path.stat()
        cache_key = (path, stat.st_ino, stat.st_size, stat.st_mtime_ns)

        if cache_key in self._cache:
            return self._cache[cache_key]

        digest = compute_digest(path)
        self._cache[cache_key] = digest
        return digest
```

### 4. Concurrency & Deduplication

To prevent multiple workers computing the same result:

```python
class ProvenanceStore:
    def compute_if_absent(self, task: SimTask) -> SimReturn:
        path = self.get_path(task)
        lock_path = path.with_suffix('.lock')

        # Try to acquire exclusive lock
        try:
            lock_path.touch(exist_ok=False)
        except FileExistsError:
            # Another worker is computing, wait for result
            return self.wait_for_result(path)

        try:
            result = compute(task)
            atomic_write(path, result)
            return result
        finally:
            lock_path.unlink(missing_ok=True)
```

### 5. Schema Versioning

Storage paths include version prefix for future migrations:

```
v1/sims/{bundle_digest}/...  # Current schema
v2/sims/{bundle_digest}/...  # Future schema
```

### 6. Cache Pruning

Implement retention policies:

```bash
# Remove simulations older than 30 days
mops cache prune --older-than 30d

# Keep only results from specific experiments
mops cache prune --keep-tagged production

# Limit cache size
mops cache prune --max-size 100GB
```

## Known Limitations

### 1. Token Hashing Portability
- Python's `tokenize` module may vary between versions
- **Mitigation**: Include Python version in env_digest
- **Future**: Consider AST-based or LibCST for stability

### 2. Multi-Language Support
- Non-Python code (R, Julia, C++) falls back to byte hashing
- **Mitigation**: Document as known limitation
- **Future**: Pluggable language-specific hashers

### 3. Dynamic Dependencies
- Runtime imports and conditional file access may be missed
- **Mitigation**: Use `mops-bundle doctor` for validation
- **Future**: Sandboxed execution with syscall interception

### 4. Network & Database Dependencies
- External data sources not tracked automatically
- **Mitigation**: Require explicit versioning/snapshots
- **Future**: Query fingerprinting and result caching

## Implementation Roadmap

### Phase 1: Critical Fixes (Immediate)
- [x] ✅ Param ID determinism - Already using BLAKE2b via make_param_id()
- [x] ✅ Atomic writes - Already implemented in ops.py
- [ ] Add environment digest to bundle manifest
- [ ] Implement structured composite digest with domain separation
- [ ] Add digest caching for large files

### Phase 2: Core Registry (Week 1)
- [ ] Move token hashing from modelops-calabaria to modelops-bundle
- [ ] Implement model/target registration commands
- [ ] Create Pydantic models for registry
- [ ] Import validation on register
- [ ] Add `mops-bundle doctor` for dependency discovery

### Phase 3: Production Hardening (Week 2)
- [ ] Implement compute-if-absent with locking
- [ ] Add schema versioning (v1/ prefix)
- [ ] Create separate evaluation cache namespace
- [ ] Add provenance sidecar files (.meta.json)
- [ ] Implement basic cache pruning

### Phase 4: CLI Enhancements (Week 3)
- [ ] `mops-bundle diff` - Show changes since last push
- [ ] `mops cache-why` - Explain cache misses
- [ ] `mops cache-predict` - Estimate hits before running
- [ ] `mops-bundle manifest --json` - Machine-readable output
- [ ] Better progress indicators and error messages

### Phase 5: Testing & Documentation (Week 4)
- [ ] Property tests for param_id determinism
- [ ] Concurrency tests for atomic operations
- [ ] Environment sensitivity tests
- [ ] Cache invalidation scenario tests
- [ ] Comprehensive user documentation

## Example User Experience

```bash
# Day 1: Initial model
$ mops-bundle register-model src/models/seir.py --data data/demographics.csv
✓ Model 'seir' registered
✓ Import validation passed

$ mops-bundle push
✓ Computing digests...
  Model: a3f2b8c9 (token-based)
  Data: d5e7f8a9 (SHA256)
  Environment: b2c3d4e5 (Python 3.11.5, NumPy 1.24.3, linux-x86_64)
  Bundle: f1a2b3c4 (composite)
✓ Pushed to registry

$ mops run-calibration --model seir --samples 1000
✓ Running 1000 simulations...
✓ Cache: 0 hits, 1000 misses (first run)
✓ Completed in 10 minutes

# Day 2: Update data
$ vi data/demographics.csv  # Fix population count

$ mops-bundle push
✓ Computing digests...
  Model: a3f2b8c9 (unchanged)
  Data: e6f7a8b9 (changed!)
  Bundle: c5d6e7f8 (changed!)
✓ Pushed to registry

$ mops run-calibration --model seir --samples 1000
✓ Running 1000 simulations...
✓ Cache: 0 hits, 1000 misses (data changed)
✓ Completed in 10 minutes

# Day 3: Format code
$ black src/models/seir.py  # Reformat

$ mops-bundle push
✓ Computing digests...
  Model: a3f2b8c9 (unchanged - formatting ignored!)
  Data: e6f7a8b9 (unchanged)
  Bundle: c5d6e7f8 (unchanged!)
✓ No changes detected

$ mops run-calibration --model seir --samples 1000
✓ Running 1000 simulations...
✓ Cache: 1000 hits, 0 misses (using cached results!)
✓ Completed in 5 seconds
```

## Conclusion

This provenance system provides:
1. **Automatic correctness** through input-addressed storage
2. **Optimal efficiency** through semantic hashing
3. **Complete traceability** through provenance paths
4. **Simple UX** through explicit dependencies

The key insight is that by making dependencies explicit at bundle time, we can automatically handle cache invalidation at runtime, giving users both correctness and performance without complexity.

## Review Response Summary

Based on comprehensive review feedback, we've addressed the following critical issues:

### ✅ Already Implemented
- **Param ID Determinism**: Using BLAKE2b with canonical JSON (NOT Python's hash())
- **Atomic Writes**: Full implementation with temp file + fsync + rename

### 📝 Documented Solutions
- **Environment Drift**: Added EnvironmentDigest tracking
- **Composite Digest Structure**: Domain separation for determinism
- **Runtime Dependencies**: `mops-bundle doctor` for discovery
- **Concurrency**: Compute-if-absent with locking
- **Large File Performance**: Digest caching strategy
- **Schema Versioning**: v1/ prefix for migrations

### 🎯 Priority Next Steps
1. Add environment digest to bundle manifest
2. Implement structured composite digest
3. Build `mops-bundle doctor` command
4. Add digest caching for large files
5. Create comprehensive test suite

The design elegantly balances correctness, performance, and usability while maintaining production-readiness through atomic operations, deterministic hashing, and comprehensive dependency tracking.