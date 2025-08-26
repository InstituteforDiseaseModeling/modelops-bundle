# TODO

## Issues Fixed

- [x] Manifest digest calculation is non-canonical - Now uses Docker-Content-Digest 
  header when available, falls back to hashing raw bytes with proper warning
- [x] Tag race during push/pull - Implemented two-phase plan/apply pattern with 
  digest resolution and tag movement detection
- [x] Push race protection - Fixed force=False default, added --force CLI flag
- [x] Config artifact_type - Removed from user-facing config entirely

## High Priority Performance & Correctness Issues

### Duplicate diff computation in status (Grade: A, Severity: Low)

**Issue**: Status command recomputes diff to show ≤10 unchanged files
- Currently calls `compute_diff()` twice when unchanged count ≤ 10
- Location: `cli.py:271-275`
**Fix**: Return `unchanged_paths[:10]` from `get_status()` to avoid second computation
**Impact**: Minor performance overhead on status command

### Version constant duplication (Grade: A, Severity: Low)  

**Issue**: Both `__version__` and `BUNDLE_VERSION` exist with same value
- `__init__.py:3`: `__version__ = "0.1.0"`
- `constants.py:16`: `BUNDLE_VERSION = "0.1.0"`
**Fix**: Remove `BUNDLE_VERSION`, import `__version__` where needed
**Impact**: Risk of version mismatch if not updated together

### Symlink handling undefined (Grade: A, Severity: Low)

**Issue**: Current hashing follows symlinks (default Python behavior)
- No explicit symlink handling policy documented
- Location: `utils.py:10-11` - `open()` follows symlinks by default
**Fix**: Document behavior and consider adding `follow_symlinks=False` option
**Impact**: Inconsistent behavior if symlinks point outside project

### Init doesn't create .gitignore (Grade: A, Severity: Low)

**Issue**: Only appends to .gitignore if it exists
- Location: `cli.py:60-63`
- If .gitignore missing, `.modelops-bundle/` won't be git-ignored
**Fix**: Create .gitignore if missing, check for existing entry before appending
**Impact**: Git might track internal `.modelops-bundle/` directory

### Manifest listing performance (Grade: A, Severity: Low)

**Issue**: Makes two API calls per tag (get_remote_state + get_manifest)
- Location: `cli.py:646-647`
**Fix**: Use `get_manifest_with_digest()` once, parse files from manifest
**Impact**: Slower performance with many tags

### Improved ignore rules messaging (Grade: B, Severity: Info)

**Issue**: Message says "ignored by .modelopsignore" but built-in defaults also apply
- Location: `cli.py:99`
**Fix**: Change to "ignored by ignore rules (.modelopsignore + defaults)"
**Impact**: Minor user confusion about ignore rules

## Medium / polish

 - Status recomputes diff to add unchanged entries (≤10). You already had a
   diff to build the summary — consider returning unchanged there to avoid a
   second compute.

 - PullResult.downloaded counts len(remote.files) rather than actual transfers.
   Not wrong functionally, but misleading. 

 - Windows/paths: you store POSIX-like paths in annotations; joining with Path
   is usually fine, but explicitly normalize to POSIX for registry titles and
   to OS paths for disk writes to avoid edge cases.

 - Make sure all tests using local registry is going through
   `can_connect_to_registry`.


## MVP Features

### Role-Layers

### External Blob Storage & The Hybrid Storage Model

### Async Client?

### CLI Enhancements
    - [x] Untracked in status
    - [x] Clean out emoji crap
    - [ ] Auto semvar
    - [ ] Support glob patterns in add/remove commands
    - [ ] Add `bundle export` to create tar archives
    - [ ] Add `bundle import` from tar archives

## Future Enhancements

### Authentication Support
    - [ ] Add proper auth handling to `OrasAdapter`
    - Currently using `Registry(insecure=True)` 
    - Need to support authenticated registries
    - Consider using Docker credential helpers
    - Support for token-based auth

### Progress Indicators
- [ ] Add progress bars for long operations
  - Push operations (per file upload)
  - Pull operations (per file download)
  - Scanning large directories
  - Computing digests for large files
  - Use simple progress indicators, NOT rich output in tests
  
### Performance Optimizations
- [ ] Consider parallel hashing for multiple files
- [ ] Cache digest computation for unchanged files (using mtime)
- [ ] Optimize large file transfers

### Registry Features
- [ ] Registry health check command
- [ ] Support for registry namespaces/organizations

