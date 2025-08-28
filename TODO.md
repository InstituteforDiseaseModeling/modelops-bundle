# TODO

## MVP Features

### Role-Layers

### Async Client?

### GC

**Issue**: Add bundle gc --blob that:
 - Lists all reachable digests by reading manifests in the registry/tag space.
 - Deletes unreferenced blobs from the store.

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

