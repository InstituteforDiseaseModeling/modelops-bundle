"""Tests for --restore-deleted flag functionality."""

import pytest
from pathlib import Path
from modelops_bundle.core import (
    DiffResult,
    FileChange,
    ChangeType,
    FileInfo,
    RemoteState,
    SyncState,
)
from modelops_bundle.snapshot import TrackedFilesSnapshot
from modelops_bundle.diffing import compute_diff


@pytest.fixture
def sample_files():
    """Create sample file info objects for testing."""
    return {
        "file1.py": FileInfo(
            path="file1.py",
            digest="sha256:abc123",
            size=100,
            mtime=1234567890.0
        ),
        "file2.py": FileInfo(
            path="file2.py",
            digest="sha256:def456",
            size=200,
            mtime=1234567891.0
        ),
        "file3.py": FileInfo(
            path="file3.py",
            digest="sha256:ghi789",
            size=300,
            mtime=1234567892.0
        ),
    }


@pytest.fixture
def sample_remote(sample_files):
    """Create a sample remote state."""
    return RemoteState(
        manifest_digest="sha256:test_manifest",
        files={
            "file1.py": sample_files["file1.py"],
            "file2.py": sample_files["file2.py"],
            "file3.py": sample_files["file3.py"],
        }
    )


@pytest.fixture
def sample_sync_state(sample_files):
    """Create a sample sync state where all files were previously synced."""
    return SyncState(
        last_synced_files={
            "file1.py": sample_files["file1.py"].digest,
            "file2.py": sample_files["file2.py"].digest,
            "file3.py": sample_files["file3.py"].digest,
        }
    )


class TestRestoreDeletedFlag:
    """Test the --restore-deleted flag behavior."""

    def test_restore_deleted_flag_only(self, sample_files, sample_remote, sample_sync_state):
        """Test that --restore-deleted ONLY restores deletions, doesn't touch other changes."""
        # Local state: file1 deleted, file2 modified, file3 unchanged
        local_snapshot = TrackedFilesSnapshot(
            files={
                # file1.py is missing (deleted)
                "file2.py": FileInfo(
                    path="file2.py",
                    digest="sha256:modified",  # Different from remote
                    size=250,
                    mtime=1234567899.0
                ),
                "file3.py": sample_files["file3.py"],  # Unchanged
            }
        )

        # Compute diff with file1 marked as deleted
        diff_result = compute_diff(
            local=local_snapshot,
            remote=sample_remote,
            last_sync=sample_sync_state,
            missing_local={"file1.py"}  # file1 is deleted locally
        )

        # Create preview with ONLY restore_deleted flag
        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=True
        )

        # Assert: Only deleted file should be restored
        restored_files = [f.path for f in preview.will_update_or_add]
        assert "file1.py" in restored_files, "Deleted file should be restored"
        assert "file2.py" not in restored_files, "Modified file should NOT be touched"
        assert "file3.py" not in restored_files, "Unchanged file should NOT be touched"
        assert len(preview.will_update_or_add) == 1

    def test_overwrite_flag_includes_restore(self, sample_files, sample_remote, sample_sync_state):
        """Test that --overwrite still restores deleted files for backwards compatibility."""
        # Local state: file1 deleted, file2 modified
        local_snapshot = TrackedFilesSnapshot(
            files={
                "file2.py": FileInfo(
                    path="file2.py",
                    digest="sha256:modified",
                    size=250,
                    mtime=1234567899.0
                ),
                "file3.py": sample_files["file3.py"],
            }
        )

        diff_result = compute_diff(
            local=local_snapshot,
            remote=sample_remote,
            last_sync=sample_sync_state,
            missing_local={"file1.py"}
        )

        # Create preview with ONLY overwrite flag
        preview = diff_result.to_pull_preview(
            overwrite=True,
            restore_deleted=False  # Explicitly false
        )

        # Assert: Both deleted and modified files should be updated
        restored_files = [f.path for f in preview.will_update_or_add]
        assert "file1.py" in restored_files, "Deleted file should be restored with overwrite"
        assert "file2.py" in restored_files, "Modified file should be overwritten"
        assert len(preview.will_update_or_add) == 2

    def test_no_flags_preserves_deletions(self, sample_files, sample_remote, sample_sync_state):
        """Test that without flags, deleted files stay deleted."""
        # Local state: file1 deleted
        local_snapshot = TrackedFilesSnapshot(
            files={
                "file2.py": sample_files["file2.py"],
                "file3.py": sample_files["file3.py"],
            }
        )

        diff_result = compute_diff(
            local=local_snapshot,
            remote=sample_remote,
            last_sync=sample_sync_state,
            missing_local={"file1.py"}
        )

        # Create preview with NO flags
        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=False
        )

        # Assert: Deleted file should NOT be restored
        restored_files = [f.path for f in preview.will_update_or_add]
        assert "file1.py" not in restored_files, "Deleted file should NOT be restored without flags"
        assert len(preview.will_update_or_add) == 0

    def test_both_flags_together(self, sample_files, sample_remote, sample_sync_state):
        """Test using both --overwrite and --restore-deleted together."""
        # Local state: file1 deleted, file2 modified
        local_snapshot = TrackedFilesSnapshot(
            files={
                "file2.py": FileInfo(
                    path="file2.py",
                    digest="sha256:modified",
                    size=250,
                    mtime=1234567899.0
                ),
                "file3.py": sample_files["file3.py"],
            }
        )

        diff_result = compute_diff(
            local=local_snapshot,
            remote=sample_remote,
            last_sync=sample_sync_state,
            missing_local={"file1.py"}
        )

        # Create preview with BOTH flags
        preview = diff_result.to_pull_preview(
            overwrite=True,
            restore_deleted=True
        )

        # Assert: Both deleted and modified files should be handled
        restored_files = [f.path for f in preview.will_update_or_add]
        assert "file1.py" in restored_files
        assert "file2.py" in restored_files
        assert len(preview.will_update_or_add) == 2

    def test_restore_deleted_with_conflict(self, sample_files, sample_remote, sample_sync_state):
        """Test that --restore-deleted doesn't override conflict detection."""
        # Create a conflict scenario: file2 modified both locally and remotely
        local_snapshot = TrackedFilesSnapshot(
            files={
                # file1.py is missing
                "file2.py": FileInfo(
                    path="file2.py",
                    digest="sha256:local_change",
                    size=250,
                    mtime=1234567899.0
                ),
                "file3.py": sample_files["file3.py"],
            }
        )

        # Remote has different change to file2
        remote_with_change = RemoteState(
            manifest_digest="sha256:test_manifest",
            files={
                "file1.py": sample_files["file1.py"],
                "file2.py": FileInfo(
                    path="file2.py",
                    digest="sha256:remote_change",
                    size=260,
                    mtime=1234567895.0
                ),
                "file3.py": sample_files["file3.py"],
            }
        )

        diff_result = compute_diff(
            local=local_snapshot,
            remote=remote_with_change,
            last_sync=sample_sync_state,
            missing_local={"file1.py"}
        )

        # Create preview with restore_deleted but not overwrite
        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=True
        )

        # Assert: Deleted file restored, but conflict is preserved
        restored_files = [f.path for f in preview.will_update_or_add]
        assert "file1.py" in restored_files, "Deleted file should be restored"
        assert "file2.py" not in restored_files, "Conflicted file should not be touched"
        assert "file2.py" in preview.conflicts, "Conflict should be reported"

    def test_restore_multiple_deleted_files(self, sample_files, sample_remote, sample_sync_state):
        """Test restoring multiple deleted files at once."""
        # Local state: file1 and file2 deleted, file3 present
        local_snapshot = TrackedFilesSnapshot(
            files={
                "file3.py": sample_files["file3.py"],
            }
        )

        diff_result = compute_diff(
            local=local_snapshot,
            remote=sample_remote,
            last_sync=sample_sync_state,
            missing_local={"file1.py", "file2.py"}  # Multiple deletions
        )

        # Create preview with restore_deleted
        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=True
        )

        # Assert: All deleted files should be restored
        restored_files = [f.path for f in preview.will_update_or_add]
        assert "file1.py" in restored_files
        assert "file2.py" in restored_files
        assert "file3.py" not in restored_files
        assert len(preview.will_update_or_add) == 2

    def test_restore_deleted_ignores_remote_deletions(self, sample_files, sample_sync_state):
        """Test that --restore-deleted doesn't affect remote deletions."""
        # Local has all files
        local_snapshot = TrackedFilesSnapshot(files=sample_files)

        # Remote deleted file3
        remote_with_deletion = RemoteState(
            manifest_digest="sha256:test_manifest",
            files={
                "file1.py": sample_files["file1.py"],
                "file2.py": sample_files["file2.py"],
                # file3.py is deleted remotely
            }
        )

        diff_result = compute_diff(
            local=local_snapshot,
            remote=remote_with_deletion,
            last_sync=sample_sync_state,
            missing_local=set()  # No local deletions
        )

        # Create preview with restore_deleted
        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=True
        )

        # Assert: Remote deletion is not affected by restore_deleted
        assert len(preview.will_update_or_add) == 0
        assert "file3.py" not in preview.will_delete_local  # Without overwrite, remote deletion is not applied

    def test_restore_deleted_with_new_remote_file(self, sample_files, sample_remote, sample_sync_state):
        """Test interaction between restore_deleted and new remote files."""
        # Local state: file1 deleted
        local_snapshot = TrackedFilesSnapshot(
            files={
                "file2.py": sample_files["file2.py"],
                "file3.py": sample_files["file3.py"],
            }
        )

        # Remote has a new file
        remote_with_new = RemoteState(
            manifest_digest="sha256:test_manifest",
            files={
                "file1.py": sample_files["file1.py"],
                "file2.py": sample_files["file2.py"],
                "file3.py": sample_files["file3.py"],
                "file4.py": FileInfo(
                    path="file4.py",
                    digest="sha256:new123",
                    size=400,
                    mtime=1234567893.0
                ),
            }
        )

        diff_result = compute_diff(
            local=local_snapshot,
            remote=remote_with_new,
            last_sync=sample_sync_state,
            missing_local={"file1.py"}
        )

        # Create preview with restore_deleted
        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=True
        )

        # Assert: Both deleted file and new file should be pulled
        restored_files = [f.path for f in preview.will_update_or_add]
        assert "file1.py" in restored_files, "Deleted file should be restored"
        assert "file4.py" in restored_files, "New remote file should be added"
        assert len(preview.will_update_or_add) == 2


class TestEdgeCases:
    """Test edge cases for restore-deleted functionality."""

    def test_restore_deleted_when_never_synced(self):
        """Test restoring a file that was never synced (added then deleted locally)."""
        # Local state: empty
        local_snapshot = TrackedFilesSnapshot(files={})

        # Remote has file
        remote = RemoteState(
            manifest_digest="sha256:test_manifest",
            files={
                "new_file.py": FileInfo(
                    path="new_file.py",
                    digest="sha256:abc",
                    size=100,
                    mtime=1234567890.0
                )
            }
        )

        # Sync state: file was never synced
        sync_state = SyncState(last_synced_files={})

        # file is in missing_local (tracked but deleted before sync)
        diff_result = compute_diff(
            local=local_snapshot,
            remote=remote,
            last_sync=sync_state,
            missing_local={"new_file.py"}
        )

        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=True
        )

        # File exists remotely but was never synced and is missing locally
        # This is treated as DELETED_LOCAL and should be restored
        changes = [c for c in diff_result.changes if c.path == "new_file.py"]
        if changes:
            assert changes[0].change_type == ChangeType.DELETED_LOCAL
            restored_files = [f.path for f in preview.will_update_or_add]
            assert "new_file.py" in restored_files

    def test_restore_deleted_file_not_in_remote(self):
        """Test --restore-deleted when file doesn't exist remotely."""
        local_snapshot = TrackedFilesSnapshot(files={})
        remote = RemoteState(manifest_digest="sha256:test_manifest", files={})
        sync_state = SyncState(last_synced_files={"old_file.py": "sha256:old"})

        # File was previously synced but now missing locally and remotely
        diff_result = compute_diff(
            local=local_snapshot,
            remote=remote,
            last_sync=sync_state,
            missing_local={"old_file.py"}
        )

        preview = diff_result.to_pull_preview(
            overwrite=False,
            restore_deleted=True
        )

        # Can't restore a file that doesn't exist remotely
        assert len(preview.will_update_or_add) == 0