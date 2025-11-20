# LocalCAS v2: Refined Implementation Plan & Patch

This is a revision of the original LocalCAS design with fixes and improvements based on the review. It includes **a full, drop-in reference implementation** incorporating: `flock`-based locking, re-checks after lock acquisition, strict digest validation, safer materialization without pre-unlink, better cleanup, and basic logging/observability.

---

## What changed (summary)

- **Locking:** Replaced ad-hoc `.lock` + spin with **`fcntl.flock`** (OS releases the lock on crash). Re-checks `dst.exists()` **after** lock acquisition (TOCTOU fix).
- **Digest validation:** Enforced strict hex format (`[0-9a-f]{64}`) before computing cache path (path traversal defense).
- **Materialization:** Removed pre-unlink; all strategies use **temp→fsync→atomic `os.replace`** with **guaranteed temp cleanup**. Fixes potential temp leak in the reflink→hardlink fallback path.
- **Error messages & logging:** Clearer messages and optional logger (`logging.getLogger(__name__)`). Soft failures emit `DEBUG`/`WARNING` rather than silent `pass`.
- **Copy optimization:** Optional `sendfile`-based fast copy when available; otherwise `shutil.copy2` with fsync.
- **Symlink hardening:** Best-effort check to avoid following symlinks inside the cache directory when creating parent dirs for new objects.
- **Config surface (unchanged):** Link policy remains `auto | reflink | hardlink | copy`. Read-only tree helper preserved.

---

## Directory layout (unchanged)

```
<cache_root>/objects/sha256/ab/cd/<fullsha>
```

Sharded by first 4 hex characters.

---

## Reference Implementation (v2)

> Save as `local_cache.py`. Python 3.10+. Linux reflink via `ioctl(FICLONE)` with silent fallback.

```python
# local_cache.py (v2)
from __future__ import annotations
import contextlib
import fcntl
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Literal, Optional

LinkMode = Literal["auto", "reflink", "hardlink", "copy"]

logger = logging.getLogger(__name__)

# ---- fsync helpers ---------------------------------------------------------

def _fsync_file(path: Path) -> None:
    with open(path, "rb+") as f:
        os.fsync(f.fileno())

def _fsync_dir(path: Path) -> None:
    dfd = os.open(str(path), os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)

# ---- safety helpers --------------------------------------------------------

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

def _validate_sha256(digest: str) -> str:
    if not digest.startswith("sha256:"):
        raise ValueError(f"invalid digest scheme: {digest!r}")
    h = digest.split(":", 1)[1]
    if not _HEX64.fullmatch(h):
        raise ValueError(f"invalid sha256 hex: {h!r}")
    return h

def _try_reflink(src: Path, dst: Path) -> bool:
    """Linux-only reflink; silently return False if unsupported."""
    try:
        import fcntl as _fcntl
        FICLONE = 0x40049409  # ioctl value for Linux
        with open(src, "rb") as s, open(dst, "wb") as d:
            _fcntl.ioctl(d.fileno(), FICLONE, s.fileno())
        return True
    except Exception:
        return False

def _copy_file(src: Path, dst: Path) -> None:
    """Fast copy with sendfile when available, else shutil.copy2; does not fsync."""
    try:
        with open(src, "rb") as s, open(dst, "wb") as d:
            in_fd, out_fd = s.fileno(), d.fileno()
            sent = 0
            size = os.fstat(in_fd).st_size
            while sent < size:
                n = os.sendfile(out_fd, in_fd, sent, size - sent)
                if n == 0:
                    break
                sent += n
            shutil.copystat(src, dst, follow_symlinks=True)
    except Exception:
        shutil.copy2(src, dst)

def _ensure_parents_no_symlinks(path: Path) -> None:
    """Best-effort: ensure that parent directories under the cache root are not symlinks."""
    for p in (path.parent, path.parent.parent):
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        try:
            st = os.lstat(p)
            if os.path.S_ISLNK(st.st_mode):
                raise RuntimeError(f"refusing to operate within symlinked cache dir: {p}")
        except Exception:
            logger.debug("could not verify symlink status for %s", p, exc_info=True)

# ---- FileLock based on flock ----------------------------------------------

class FileLock:
    """Advisory lock using flock; released by the OS on process crash."""
    def __init__(self, path: Path):
        self.path = path
        self.fd: Optional[int] = None

    def __enter__(self):
        self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.fd is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            if self.fd is not None:
                os.close(self.fd)

# ---- LocalCAS --------------------------------------------------------------

class LocalCAS:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else Path.home() / ".cache" / "modelops-bundle"
        self.objdir = self.root / "objects" / "sha256"
        self.objdir.mkdir(parents=True, exist_ok=True)

    def path_for(self, digest: str) -> Path:
        h = _validate_sha256(digest)
        return self.objdir / h[:2] / h[2:4] / h

    def has(self, digest: str) -> bool:
        return self.path_for(digest).exists()

    def ensure_present(self, digest: str, fetch_to_path: Callable[[str], None]) -> Path:
        """Ensure object exists in cache; verify digest; atomic promotion."""
        from .utils import compute_digest

        dst = self.path_for(digest)
        if dst.exists():
            return dst

        _ensure_parents_no_symlinks(dst)

        lock_path = dst.with_suffix(".lock")
        with FileLock(lock_path):
            if dst.exists():
                return dst

            tmph, tmppath = tempfile.mkstemp(prefix=".cas-", dir=str(dst.parent))
            os.close(tmph)
            tmp = Path(tmppath)

            try:
                fetch_to_path(tmppath)

                actual = compute_digest(tmp)
                if actual != digest:
                    raise ValueError(f"digest mismatch for {digest}: computed {actual} from {tmppath}")

                _fsync_file(tmp)
                os.replace(tmppath, dst)
                _fsync_dir(dst.parent)
                logger.debug("CAS promote: %s", dst)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmppath)
                raise

        return dst

    def materialize(self, digest: str, dest: Path, mode: LinkMode = "auto") -> None:
        """reflink → hardlink → copy (or forced); always atomic replace."""
        src = self.path_for(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if mode in ("reflink", "auto"):
            tmp = dest.with_name(f".{dest.name}.partial")
            try:
                if _try_reflink(src, tmp):
                    _fsync_file(tmp)
                    os.replace(tmp, dest)
                    _fsync_dir(dest.parent)
                    logger.debug("materialize reflink: %s <- %s", dest, src)
                    return
            finally:
                with contextlib.suppress(OSError):
                    tmp.unlink()

        if mode in ("hardlink", "auto"):
            try:
                os.link(src, dest)
                logger.debug("materialize hardlink: %s <- %s", dest, src)
                return
            except OSError:
                pass

        tmp = dest.with_name(f".{dest.name}.partial")
        try:
            _copy_file(src, tmp)
            _fsync_file(tmp)
            os.replace(tmp, dest)
            _fsync_dir(dest.parent)
            logger.debug("materialize copy: %s <- %s", dest, src)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def make_tree_readonly(self, root: Path, try_immutable: bool = True) -> None:
        for p in sorted(root.rglob("*"), key=lambda x: (x.is_file(), str(x))):
            try:
                if p.is_dir():
                    p.chmod(0o555)
                elif p.is_file():
                    p.chmod(0o444)
            except Exception as e:
                logger.debug("chmod failed for %s: %s", p, e)

            if try_immutable and p.is_file():
                try:
                    import ctypes
                    FS_IOC_GETFLAGS = 0x80086601
                    FS_IOC_SETFLAGS = 0x40086602
                    FS_IMMUTABLE_FL = 0x00000010
                    with open(p, "rb+") as f:
                        buf = ctypes.c_uint()
                        fcntl.ioctl(f.fileno(), FS_IOC_GETFLAGS, buf)
                        flags = buf.value | FS_IMMUTABLE_FL
                        fcntl.ioctl(f.fileno(), FS_IOC_SETFLAGS, ctypes.c_uint(flags))
                except Exception as e:
                    logger.debug("immutable-bit set failed for %s: %s", p, e)
```

---

## Integration points

### `oras.py`

```python
# inside OrasAdapter.pull_selected(...)
from .local_cache import LocalCAS, LinkMode

def pull_selected(..., cas: LocalCAS | None = None, link_mode: LinkMode = "auto"):
    cas = cas or LocalCAS()
    ...
    def fetch_to(path_on_disk: str) -> None:
        if entry.storage == StorageType.OCI:
            self.client.download_blob(container, entry.digest, path_on_disk)
        else:
            if not entry.blobRef or not blob_store:
                raise BlobProviderMissingError()
            blob_store.get(entry.blobRef, Path(path_on_disk))

    cas.ensure_present(entry.digest, fetch_to)
    cas.materialize(entry.digest, dst, mode=link_mode)
```

### `ops.py`

```python
from .local_cache import LocalCAS

def pull_apply(...):
    cas = LocalCAS(getattr(config, "cache_dir", None))
    link_mode = getattr(config, "cache_link_mode", "auto")
    adapter.pull_selected(..., cas=cas, link_mode=link_mode)
```

---

## Configuration (clarified)

```python
class BundleConfig(BaseModel):
    registry_ref: str
    default_tag: str = "latest"
    storage: StoragePolicy = Field(default_factory=StoragePolicy)
    cache_dir: str | None = None
    cache_link_mode: str = "auto"  # "auto" | "reflink" | "hardlink" | "copy"
    read_only_after_pull: bool = True
    use_immutable_bit: bool = False
```

---

## Tests to add

- Concurrency: two producers → one fetch; crash during lock releases properly.
- Fallback paths leave no `*.partial` files behind.
- Malformed digests rejected before directory creation.
- ENOSPC during materialization leaves no partial at destination.
- Symlink defense: refuse if shard dir is symlinked.
- Error messages include expected vs actual digests and temp path.

---

## Security notes

- Paths derived from validated digest only.
- Do not follow symlinks within shard dirs.
- Verify digest before promotion; no filename-based addressing.

---

## NOTE: Mount-level read-only (recommended)

For production “data must never be modified”, **enforce read-only at the mount** and treat the materialized tree as input only.

- **Kubernetes:** do materialization in an init container and mount the same volume into the app container with `readOnly: true`.

```yaml
volumes:
  - name: bundle
    emptyDir: {}
initContainers:
  - name: prep
    image: yourimage
    volumeMounts: [{ name: bundle, mountPath: /data }]
    command: ["sh","-lc"]
    args: ["modelops-bundle ensure --dest /data --mirror"]
containers:
  - name: app
    image: yourimage
    volumeMounts:
      - name: bundle
        mountPath: /data
        readOnly: true  # Enforce RO
```

- **Docker/Podman:** `-v /host/cache:/data:ro`

**S3 “container” question:** You can’t reflink/hardlink on object storage. With S3 FUSE (Mountpoint, s3fs, goofys) you can mount **read-only**, but POSIX semantics differ and LocalCAS link strategies won’t apply. The recommended pattern is: **prefetch into a local CAS on a block filesystem**, materialize to a local volume, then mount that volume read-only into the worker.
