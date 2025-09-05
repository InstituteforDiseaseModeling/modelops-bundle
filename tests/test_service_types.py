"""Test service types and Pydantic models."""

import json
import pytest
from typing import Protocol

from modelops_bundle.service_types import (
    ChangeInfo,
    StatusReport,
    AddResult,
    RemoveResult,
    PushResult,
    EnsureLocalResult,
    ProgressCallback,
)


class TestChangeInfo:
    """Test ChangeInfo model."""
    
    def test_change_info_validation(self):
        """Test ChangeInfo validation."""
        change = ChangeInfo(
            path="file.txt",
            change_type="added",
            size=100,
            digest="sha256:abcd"
        )
        
        assert change.path == "file.txt"
        assert change.change_type == "added"
        assert change.size == 100
        assert change.digest == "sha256:abcd"
    
    def test_change_info_optional_fields(self):
        """Test optional fields in ChangeInfo."""
        change = ChangeInfo(
            path="file.txt",
            change_type="deleted"
        )
        
        assert change.size == 0
        assert change.digest is None
    
    def test_change_info_serialization(self):
        """Test ChangeInfo JSON serialization."""
        change = ChangeInfo(
            path="test.py",
            change_type="modified",
            size=500
        )
        
        json_str = change.model_dump_json()
        data = json.loads(json_str)
        
        assert data["path"] == "test.py"
        assert data["change_type"] == "modified"
        assert data["size"] == 500


class TestStatusReport:
    """Test StatusReport model."""
    
    def test_status_report_validation(self):
        """Test StatusReport validation."""
        report = StatusReport(
            local_changes=[
                ChangeInfo(path="local.txt", change_type="added")
            ],
            remote_changes=[
                ChangeInfo(path="remote.txt", change_type="modified")
            ],
            conflicts=["conflict.txt"],
            local_only=["local.txt"],
            remote_only=["remote.txt"],
            up_to_date=False,
            summary="1 local, 1 remote changes"
        )
        
        assert len(report.local_changes) == 1
        assert len(report.remote_changes) == 1
        assert "conflict.txt" in report.conflicts
        assert not report.up_to_date
    
    def test_status_report_empty(self):
        """Test StatusReport with no changes."""
        report = StatusReport(
            local_changes=[],
            remote_changes=[],
            conflicts=[],
            local_only=[],
            remote_only=[],
            up_to_date=True,
            summary="Everything up to date"
        )
        
        assert report.up_to_date
        assert len(report.local_changes) == 0
    
    def test_status_report_dict_conversion(self):
        """Test StatusReport dict conversion."""
        report = StatusReport(
            local_changes=[ChangeInfo(path="file.txt", change_type="added")],
            remote_changes=[],
            conflicts=[],
            local_only=["file.txt"],
            remote_only=[],
            up_to_date=False,
            summary="1 local change"
        )
        
        data = report.model_dump()
        assert isinstance(data, dict)
        assert len(data["local_changes"]) == 1
        assert data["local_changes"][0]["path"] == "file.txt"


class TestAddResult:
    """Test AddResult model."""
    
    def test_add_result_validation(self):
        """Test AddResult validation."""
        result = AddResult(
            added=["file1.txt", "file2.txt"],
            already_tracked=["existing.txt"],
            ignored=["node_modules/"],
            total_size=1024
        )
        
        assert len(result.added) == 2
        assert "existing.txt" in result.already_tracked
        assert result.total_size == 1024
    
    def test_add_result_empty(self):
        """Test AddResult with no additions."""
        result = AddResult(
            added=[],
            already_tracked=[],
            ignored=[],
            total_size=0
        )
        
        assert len(result.added) == 0
        assert result.total_size == 0


class TestRemoveResult:
    """Test RemoveResult model."""
    
    def test_remove_result_validation(self):
        """Test RemoveResult validation."""
        result = RemoveResult(
            removed=["old1.txt", "old2.txt"],
            not_tracked=["unknown.txt"]
        )
        
        assert len(result.removed) == 2
        assert "unknown.txt" in result.not_tracked
    
    def test_remove_result_serialization(self):
        """Test RemoveResult serialization."""
        result = RemoveResult(
            removed=["file.txt"],
            not_tracked=[]
        )
        
        json_str = result.model_dump_json()
        data = json.loads(json_str)
        
        assert data["removed"] == ["file.txt"]
        assert data["not_tracked"] == []


class TestPushResult:
    """Test PushResult model."""
    
    def test_push_result_validation(self):
        """Test PushResult validation."""
        result = PushResult(
            manifest_digest="sha256:abcd1234",
            tag="v1.0",
            files_pushed=10,
            bytes_uploaded=5000,
            summary="Pushed 10 files to v1.0"
        )
        
        assert result.manifest_digest == "sha256:abcd1234"
        assert result.tag == "v1.0"
        assert result.files_pushed == 10
        assert result.bytes_uploaded == 5000
    
    def test_push_result_dict_conversion(self):
        """Test PushResult dict conversion."""
        result = PushResult(
            manifest_digest="sha256:test",
            tag="latest",
            files_pushed=1,
            bytes_uploaded=100,
            summary="Pushed 1 file"
        )
        
        data = result.model_dump()
        assert data["manifest_digest"] == "sha256:test"
        assert data["files_pushed"] == 1


class TestEnsureLocalResult:
    """Test EnsureLocalResult model."""
    
    def test_ensure_local_result_validation(self):
        """Test EnsureLocalResult validation."""
        result = EnsureLocalResult(
            resolved_digest="sha256:resolved",
            downloaded=5,
            deleted=2,
            bytes_downloaded=1000
        )
        
        assert result.resolved_digest == "sha256:resolved"
        assert result.downloaded == 5
        assert result.deleted == 2
        assert result.bytes_downloaded == 1000
        assert result.dry_run == False  # Default
    
    def test_ensure_local_result_dry_run(self):
        """Test EnsureLocalResult with dry_run."""
        result = EnsureLocalResult(
            resolved_digest="sha256:test",
            downloaded=10,
            deleted=0,
            bytes_downloaded=5000,
            dry_run=True
        )
        
        assert result.dry_run == True
    
    def test_ensure_local_result_serialization(self):
        """Test EnsureLocalResult JSON serialization."""
        result = EnsureLocalResult(
            resolved_digest="sha256:abc",
            downloaded=3,
            deleted=1,
            bytes_downloaded=500
        )
        
        json_str = result.model_dump_json()
        data = json.loads(json_str)
        
        assert data["resolved_digest"] == "sha256:abc"
        assert data["downloaded"] == 3
        assert data["deleted"] == 1
        assert data["dry_run"] == False


class TestProgressCallback:
    """Test ProgressCallback protocol."""
    
    def test_progress_callback_protocol(self):
        """Test that ProgressCallback is a proper Protocol."""
        # Protocol should be importable and usable
        assert ProgressCallback is not None
        
        # Check it's actually a Protocol
        assert hasattr(ProgressCallback, '__subclasshook__')
    
    def test_progress_callback_implementation(self):
        """Test implementing ProgressCallback."""
        class MockProgress:
            def __init__(self):
                self.started = []
                self.completed = []
                self.errors = []
            
            def on_file_start(self, path: str, size: int) -> None:
                self.started.append((path, size))
            
            def on_file_complete(self, path: str) -> None:
                self.completed.append(path)
            
            def on_file_error(self, path: str, error: str) -> None:
                self.errors.append((path, error))
        
        progress = MockProgress()
        
        # Test the interface
        progress.on_file_start("test.txt", 100)
        progress.on_file_complete("test.txt")
        progress.on_file_error("bad.txt", "Not found")
        
        assert ("test.txt", 100) in progress.started
        assert "test.txt" in progress.completed
        assert ("bad.txt", "Not found") in progress.errors
    
    def test_progress_callback_typing(self):
        """Test that ProgressCallback can be used as a type hint and works correctly."""
        def process_with_progress(callback: ProgressCallback) -> None:
            callback.on_file_start("file.txt", 50)
            callback.on_file_complete("file.txt")
        
        class SimpleProgress:
            def __init__(self):
                self.calls = []
            
            def on_file_start(self, path: str, size: int) -> None:
                self.calls.append(('start', path, size))
            
            def on_file_complete(self, path: str) -> None:
                self.calls.append(('complete', path))
            
            def on_file_error(self, path: str, error: str) -> None:
                self.calls.append(('error', path, error))
        
        # Should work with type checking and track calls
        progress = SimpleProgress()
        process_with_progress(progress)
        
        # Verify the callback methods were called correctly
        assert len(progress.calls) == 2
        assert progress.calls[0] == ('start', 'file.txt', 50)
        assert progress.calls[1] == ('complete', 'file.txt')


class TestModelIntegration:
    """Test integration between different models."""
    
    def test_status_report_with_changes(self):
        """Test StatusReport with multiple ChangeInfo objects."""
        changes = [
            ChangeInfo(path=f"file{i}.txt", change_type="added", size=i*100)
            for i in range(3)
        ]
        
        report = StatusReport(
            local_changes=changes,
            remote_changes=[],
            conflicts=[],
            local_only=[c.path for c in changes],
            remote_only=[],
            up_to_date=False,
            summary=f"{len(changes)} local changes"
        )
        
        # Test serialization roundtrip
        json_str = report.model_dump_json()
        data = json.loads(json_str)
        
        # Recreate from dict
        report2 = StatusReport.model_validate(data)
        assert len(report2.local_changes) == 3
        assert report2.local_changes[0].path == "file0.txt"
    
    def test_all_models_have_json_schema(self):
        """Test that all models can generate JSON schema."""
        models = [
            ChangeInfo,
            StatusReport,
            AddResult,
            RemoveResult,
            PushResult,
            EnsureLocalResult,
        ]
        
        for model in models:
            schema = model.model_json_schema()
            assert isinstance(schema, dict)
            assert "properties" in schema
            assert "type" in schema