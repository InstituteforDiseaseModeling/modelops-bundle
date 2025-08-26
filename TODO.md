# TODO

## Isssues

1.	Manifest digest calculation is non-canonical You compute the
    digest by json.dumps(manifest, sort_keys=True). OCI digest is over
    the raw bytes served by the registry; re-serializing JSON can
    yield a different digest. This will disagree with other tools and
    headers. Fix: prefer the registry’s digest (e.g.,
    Docker-Content-Digest or a descriptor digest) via oras-py APIs if
    available; otherwise GET/HEAD the manifest and read the header.
    Only fall back to self-hashing if there’s no other option (and
    mark it clearly).

2.	Tag race during push/pull You resolve a tag to a manifest, then
    perform operations by tag. If the tag moves, your preview and
    execution diverge. Fix: after resolving, operate by digest for the
    rest of the command (best), or re-resolve immediately before
    executing and abort if it changed.

3.	Config advertises artifact_type but it’s ignored You accept/store
    artifact_type, but oras-py doesn’t set it. That’s leaky UX. Fix:
    either (a) set it post-push if you add a manifest-patch step, or
    (b) hide it from user config until supported (keep an internal
    default).

Medium / polish:
 - Status recomputes diff to add unchanged entries (≤10). You already had a diff to build the summary — consider returning unchanged there to avoid a second compute.
 - Default insecure=True in OrasAdapter: good for local, surprising for prod. Make it configurable (env or config), default secure.
 - PullResult.downloaded counts len(remote.files) rather than actual transfers. Not wrong functionally, but misleading.
 - Windows/paths: you store POSIX-like paths in annotations; joining with Path is usually fine, but explicitly normalize to POSIX for registry titles and to OS paths for disk writes to avoid edge cases.


## Future Enhancements

### Role-Layers

### External Blob Storage & The Hybrid Storage Model

### Async Client

### CLI Enhancements
    - [x] Untracked in status
    - [x] Clean out emoji crap
    - [ ] Auto semvar
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

