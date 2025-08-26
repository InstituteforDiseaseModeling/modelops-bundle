"""Tests for diffing logic and edge cases."""

import tempfile
from pathlib import Path
import pytest

from modelops_bundle.core import (
    BundleConfig,
    ChangeType,
    DiffResult,
    FileChange,
    FileInfo,
    RemoteState,
    SyncState,
    TrackedFiles,
)
from modelops_bundle.diffing import compute_diff
from modelops_bundle.snapshot import TrackedFilesSnapshot
from modelops_bundle.working_state import TrackedWorkingState
from modelops_bundle.context import ProjectContext
from modelops_bundle.ops import save_tracked, save_state
from modelops_bundle.utils import compute_digest


class TestDiffingLogic:
    """Test the core diffing logic."""
    
    def test_modified_local_with_overwrite(self, tmp_path):
        """Test that MODIFIED_LOCAL files are downloaded when overwrite=True."""
        # Setup
        test_file = tmp_path / "test.txt"
        test_file.write_text("local content")
        
        local = TrackedFilesSnapshot(
            files={
                "test.txt": FileInfo(
                    path="test.txt",
                    digest=compute_digest(test_file),
                    size=len("local content")
                )
            }
        )
        
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "test.txt": FileInfo(
                    path="test.txt",
                    digest="sha256:remote_content",
                    size=100
                )
            }
        )
        
        last_sync = SyncState(
            last_synced_files={
                "test.txt": "sha256:remote_content"  # Was synced with remote
            }
        )
        
        # Compute diff
        diff_result = compute_diff(local, remote, last_sync, set())
        
        # Verify change type is MODIFIED_LOCAL
        assert len(diff_result.changes) == 1
        change = diff_result.changes[0]
        assert change.change_type == ChangeType.MODIFIED_LOCAL
        
        # Test pull plan WITHOUT overwrite - should skip
        pull_plan = diff_result.to_pull_plan(overwrite=False)
        assert len(pull_plan.files_to_download) == 0
        assert "test.txt" in pull_plan.files_to_skip
        
        # Test pull plan WITH overwrite - should download
        pull_plan_overwrite = diff_result.to_pull_plan(overwrite=True)
        assert len(pull_plan_overwrite.files_to_download) == 1
        assert pull_plan_overwrite.files_to_download[0].path == "test.txt"
        assert len(pull_plan_overwrite.files_to_skip) == 0
    
    def test_file_added_deleted_before_sync(self, tmp_path):
        """Test that files added and deleted before any sync don't cause errors."""
        # Setup: empty snapshots
        local = TrackedFilesSnapshot(files={})
        remote = RemoteState(manifest_digest="sha256:remote123", files={})
        last_sync = SyncState()  # Never synced
        
        # File was tracked but deleted before sync
        missing_local = {"never_existed.txt"}
        
        # Should not error and should skip this file
        diff_result = compute_diff(local, remote, last_sync, missing_local)
        
        # Should have no changes (file never synced and doesn't exist anywhere)
        assert len(diff_result.changes) == 0
    
    def test_file_deleted_locally_exists_remotely(self, tmp_path):
        """Test deletion detection when file exists remotely."""
        local = TrackedFilesSnapshot(files={})  # No local files
        
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "deleted.txt": FileInfo(
                    path="deleted.txt",
                    digest="sha256:remote_content",
                    size=100
                )
            }
        )
        
        last_sync = SyncState(
            last_synced_files={
                "deleted.txt": "sha256:remote_content"  # Was synced
            }
        )
        
        # File is missing locally
        missing_local = {"deleted.txt"}
        
        diff_result = compute_diff(local, remote, last_sync, missing_local)
        
        assert len(diff_result.changes) == 1
        change = diff_result.changes[0]
        assert change.path == "deleted.txt"
        assert change.change_type == ChangeType.DELETED_LOCAL
        assert change.local is None
        assert change.remote is not None
    
    def test_conflict_both_modified(self, tmp_path):
        """Test conflict detection when both local and remote modified."""
        test_file = tmp_path / "conflict.txt"
        test_file.write_text("local changes")
        
        local = TrackedFilesSnapshot(
            files={
                "conflict.txt": FileInfo(
                    path="conflict.txt",
                    digest=compute_digest(test_file),
                    size=len("local changes")
                )
            }
        )
        
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "conflict.txt": FileInfo(
                    path="conflict.txt",
                    digest="sha256:remote_changes",
                    size=200
                )
            }
        )
        
        last_sync = SyncState(
            last_synced_files={
                "conflict.txt": "sha256:original"  # Both modified from original
            }
        )
        
        diff_result = compute_diff(local, remote, last_sync, set())
        
        assert len(diff_result.changes) == 1
        change = diff_result.changes[0]
        assert change.change_type == ChangeType.CONFLICT
        
        # Without overwrite, should be in conflicts
        pull_plan = diff_result.to_pull_plan(overwrite=False)
        assert "conflict.txt" in pull_plan.conflicts
        assert len(pull_plan.files_to_download) == 0
        
        # With overwrite, should download remote version
        pull_plan_overwrite = diff_result.to_pull_plan(overwrite=True)
        assert len(pull_plan_overwrite.conflicts) == 0
        assert len(pull_plan_overwrite.files_to_download) == 1
        assert pull_plan_overwrite.files_to_download[0].path == "conflict.txt"
    
    def test_deleted_remote_handling(self, tmp_path):
        """Test handling of files deleted on remote."""
        test_file = tmp_path / "local_only.txt"
        test_file.write_text("local content")
        
        local = TrackedFilesSnapshot(
            files={
                "local_only.txt": FileInfo(
                    path="local_only.txt",
                    digest=compute_digest(test_file),
                    size=len("local content")
                )
            }
        )
        
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={}  # File doesn't exist remotely
        )
        
        last_sync = SyncState(
            last_synced_files={
                "local_only.txt": compute_digest(test_file)  # Was synced, unchanged locally
            }
        )
        
        diff_result = compute_diff(local, remote, last_sync, set())
        
        assert len(diff_result.changes) == 1
        change = diff_result.changes[0]
        assert change.change_type == ChangeType.DELETED_REMOTE
        
        # Without overwrite, should be conflict
        pull_plan = diff_result.to_pull_plan(overwrite=False)
        assert "local_only.txt" in pull_plan.conflicts
        assert len(pull_plan.files_to_delete_local) == 0
        
        # With overwrite, should delete locally
        pull_plan_overwrite = diff_result.to_pull_plan(overwrite=True)
        assert len(pull_plan_overwrite.conflicts) == 0
        assert "local_only.txt" in pull_plan_overwrite.files_to_delete_local
    
    def test_push_plan_excludes_deleted_local(self, tmp_path):
        """Test that push plans don't include locally deleted files."""
        # Only file1 exists locally
        file1 = tmp_path / "file1.txt"
        file1.write_text("content1")
        
        local = TrackedFilesSnapshot(
            files={
                "file1.txt": FileInfo(
                    path="file1.txt",
                    digest=compute_digest(file1),
                    size=len("content1")
                )
            }
        )
        
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "file1.txt": FileInfo(
                    path="file1.txt",
                    digest=compute_digest(file1),
                    size=len("content1")
                ),
                "file2.txt": FileInfo(
                    path="file2.txt",
                    digest="sha256:old_content",
                    size=100
                )
            }
        )
        
        last_sync = SyncState(
            last_synced_files={
                "file1.txt": compute_digest(file1),
                "file2.txt": "sha256:old_content"
            }
        )
        
        # file2.txt was deleted locally
        missing_local = {"file2.txt"}
        
        diff_result = compute_diff(local, remote, last_sync, missing_local)
        push_plan = diff_result.to_push_plan()
        
        # Manifest should only have file1
        assert len(push_plan.manifest_files) == 1
        assert push_plan.manifest_files[0].path == "file1.txt"
        
        # Deletes should include file2
        assert "file2.txt" in push_plan.deletes
        
        # Nothing to upload (file1 unchanged)
        assert len(push_plan.files_to_upload) == 0


class TestTrackedWorkingState:
    """Test the TrackedWorkingState abstraction."""
    
    def test_working_state_detects_missing(self, tmp_path):
        """Test that TrackedWorkingState automatically detects missing files."""
        # Create a project with one file
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        file1 = project_dir / "exists.txt"
        file1.write_text("content")
        
        # Initialize project context
        import os
        os.chdir(project_dir)
        ctx = ProjectContext.init()
        
        # Track two files (one exists, one doesn't)
        tracked = TrackedFiles()
        tracked.add(Path("exists.txt"), Path("missing.txt"))
        
        # Create working state
        working_state = TrackedWorkingState.from_tracked(tracked, ctx)
        
        # Should detect the missing file
        assert len(working_state.files) == 1
        assert "exists.txt" in working_state.files
        assert len(working_state.missing) == 1
        assert "missing.txt" in working_state.missing
        assert working_state.has_deletions()
    
    def test_sync_state_prunes_deleted_files(self, tmp_path):
        """Test that SyncState.update_after_push() prunes deleted files."""
        # Create snapshot with only remaining files
        file1 = tmp_path / "remaining.txt"
        file1.write_text("content")
        
        snapshot = TrackedFilesSnapshot(
            files={
                "remaining.txt": FileInfo(
                    path="remaining.txt",
                    digest=compute_digest(file1),
                    size=len("content")
                )
            }
        )
        
        # Sync state has both remaining and deleted files
        sync_state = SyncState(
            last_synced_files={
                "remaining.txt": "sha256:old",
                "deleted.txt": "sha256:deleted"
            }
        )
        
        # Update after push
        sync_state.update_after_push("sha256:new_manifest", snapshot)
        
        # Should only have remaining file
        assert len(sync_state.last_synced_files) == 1
        assert "remaining.txt" in sync_state.last_synced_files
        assert "deleted.txt" not in sync_state.last_synced_files
        assert sync_state.last_push_digest == "sha256:new_manifest"
