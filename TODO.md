# TODO

## Future Enhancements

### Module Restructuring
- [ ] Create new modules for better organization
  - `inventory.py` - lightweight file scanning (WorkspaceInventory)
  - `snapshot.py` - full file hashing (TrackedFilesSnapshot)
  - `status.py` - combine both for status operations (WorkspaceStatus)
- [ ] Rename `WorkingTreeState` to `TrackedFilesSnapshot` throughout codebase

### Testing
- [ ] Add comprehensive unit tests for `compute_diff`
    - Three-way diff logic
- Deletion detection (DELETED_LOCAL, DELETED_REMOTE)
    - Conflict detection
    - Edge cases

### CLI Enhancements
    - [ ] Add `--dry-run` flag for pull operations
    - [ ] Support glob patterns in add/remove commands
    - [ ] Add `bundle export` to create tar archives
    - [ ] Add `bundle import` from tar archives

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

