# Summary of Fixes Applied

## Refactor Context
The `compute_digest` function was removed from `utils.py` as part of a cleanup to consolidate duplicate hashing functions into a single module (`hashing.py`).

## Issues Fixed

### 1. Authentication Regression in CLI Status Command
**Problem**: Created a duplicate `OrasAdapter()` without authentication, causing "This endpoint requires a token" errors.

**Fix**: In `cli.py` line 661, removed the duplicate adapter creation and reused the existing authenticated adapter from line 571.

```python
# REMOVED BAD CODE:
# deps = BundleDeps(ctx=ctx, adapter=OrasAdapter())  # NO AUTH!

# NOW USES:
manifest = adapter.get_manifest(...)  # Uses existing authenticated adapter
```

### 2. Added Sync Status Column to Model Table
**Feature**: Added a sync status column showing model synchronization state with cloud.

**Symbols**:
- `?` = Unknown (cannot connect to registry)
- `✓` = Synced (model matches cloud version)
- `✗` = Out-of-date (local differs from cloud)

**Implementation**: Added legend after the model table in `cli.py`.

### 3. Fixed file_hash() Missing Prefix
**Problem**: `file_hash()` was returning raw hex without "sha256:" prefix.

**Fix**: In `hashing.py` line 104, changed to:
```python
return f"sha256:{sha256.hexdigest()}"
```

### 4. Fixed ModelEntry.compute_composite_digest()
**Problem**: Function was calling `compute_composite_digest()` incorrectly.

**Fix**: In `registry.py`, updated to:
- Build proper tuples with (domain, path, digest)
- Pass empty string for env_digest parameter
- Use correct domain separators (MODEL_CODE, DATA, CODE_DEP)

### 5. Fixed Test Imports
**Problem**: Tests were importing removed `compute_digest` function.

**Fix**: Updated all test files to:
```python
from modelops_bundle.hashing import file_hash as compute_digest
```

### 6. Fixed Test Patches
**Problem**: Test patches were targeting wrong module location.

**Fix**: In `test_local_cache.py`, changed patches from `modelops_bundle.utils.compute_digest` to `modelops_bundle.local_cache.file_hash`.

### 7. Fixed Orphaned Files Detection
**Problem**: Path comparison logic was comparing Path objects with strings incorrectly.

**Fix**: In `registry.py` `find_orphaned_files()`, simplified to use relative path strings consistently.

## Testing Verification

Run the following tests to verify all fixes:

```bash
# Key tests that were failing:
uv run pytest tests/test_registry_sync.py::TestRegistrySync::test_orphaned_files_detection -xvs
uv run pytest tests/test_e2e.py::TestBundleE2E::test_working_tree_scan -xvs
uv run pytest tests/test_local_cache.py -xvs
uv run pytest tests/test_deletions_simple.py -xvs

# Full test suite:
uv run pytest
```

## Verification Script

A verification script has been created at `verify_fixes.py` that tests:
1. file_hash returns sha256: prefix
2. Registry orphaned files detection works
3. ModelEntry.compute_composite_digest() executes without error

Run with: `uv run python verify_fixes.py`