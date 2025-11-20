# Bundle Cache Locking & CAS Reuse

`ModelOpsBundleRepository.ensure_local` is the worker-side entrypoint for fetching bundles. After the November 2025 changes it now provides the guarantees we always wanted but never documented. This page explains how it works so operators and contributors know what to expect.

## Why the change?

- The original implementation treated any non-empty directory as “done”, so concurrent workers could trample each other and reuse half-written bundles.
- Crashes during download left stray directories that were happily reused on the next call.
- We hit ORAS even when every blob already existed in the local CAS.

## Behaviour in v2025.11+

1. **Per-digest lock** – we use `portalocker` on `<cache>/locks/<digest>.lock` so only one process populates a bundle directory at a time.
2. **Completion marker** – a `.complete` file (written atomically) signals that the directory is fully hydrated. No marker = delete and retry.
3. **Cached manifest indexes** – we persist the ORAS `BundleIndex` under `<cache>/indexes/<digest>.json` so subsequent calls know which files belong to the digest.
4. **CAS reuse** – if every file listed in the index already exists in `LocalCAS`, we rematerialize directly into the bundle directory without touching the registry.

### Control flow

```
ensure_local(bundle_ref):
  parse digest -> bundle_dir, complete_marker, lock_path
  with portalocker(lock_path):
      if .complete exists: return
      wipe bundle_dir; mkdir
      index = load_cached_index() or pull from ORAS
      if all entry.digest exist in LocalCAS: materialize_from_cache(index)
      else: pull_selected(..., cas=self.cas)
      save index JSON
      write .complete atomically
      return bundle_dir
```

If anything raises inside the lock, we `shutil.rmtree(bundle_dir)` to avoid leaving junk on disk and rethrow as `NotFoundError`.

## Tests

`tests/test_repository.py` now covers:

- First pull writes files + marker.
- Subsequent calls reuse the marker without hitting ORAS.
- Missing markers trigger a re-download.
- CAS materialization path (by pretending `LocalCAS.has()` is true) skips ORAS.

Run them with:

```bash
uv run python -m pytest tests/test_repository.py
```

## Operational Notes

- The cache directory now contains three subdirectories: `bundles/`, `indexes/`, and `locks/`. It’s safe to wipe them if disk pressure hits.
- If you ever need to force a re-download of a digest, delete both the bundle directory **and** its cached index so ensure_local doesn’t shortcut back to CAS.
- The `.complete` marker is just a tiny text file. Watching its timestamp is a quick way to know when the last pull finished.
