"""Tests for environment tracking in modelops-contracts."""

import pytest
import sys
import platform

# Test the contracts module
try:
    from modelops_contracts import EnvironmentDigest
    HAS_CONTRACTS = True
except ImportError:
    HAS_CONTRACTS = False
    # Don't skip at module level - mark individual tests
    EnvironmentDigest = None

pytestmark = pytest.mark.skipif(
    not HAS_CONTRACTS,
    reason="modelops-contracts not installed (install with: uv sync)"
)


class TestEnvironmentDigest:
    """Test environment digest tracking."""

    def test_create_environment_digest(self):
        """Test creating an environment digest."""
        env = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            dependencies={"numpy": "1.24.0", "pandas": "2.0.0"}
        )

        assert env.python_version == "3.11.5"
        assert env.platform == "linux-x86_64"
        assert env.dependencies["numpy"] == "1.24.0"
        assert env.rng_algorithm == "PCG64"  # Default
        assert env.thread_count == 1  # Default

    def test_environment_digest_deterministic(self):
        """Environment digest should be deterministic."""
        env1 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            dependencies={"numpy": "1.24.0", "scipy": "1.10.0"}
        )

        env2 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            dependencies={"scipy": "1.10.0", "numpy": "1.24.0"}  # Different order
        )

        digest1 = env1.compute_digest()
        digest2 = env2.compute_digest()

        assert digest1 == digest2, "Digest should be order-independent"

    def test_environment_digest_changes_with_python(self):
        """Digest should change with Python version."""
        env1 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64"
        )

        env2 = EnvironmentDigest(
            python_version="3.11.6",  # Patch version change
            platform="linux-x86_64"
        )

        digest1 = env1.compute_digest()
        digest2 = env2.compute_digest()

        assert digest1 != digest2, "Digest should change with Python version"

    def test_environment_digest_changes_with_deps(self):
        """Digest should change when dependencies change."""
        env1 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            dependencies={"numpy": "1.24.0"}
        )

        env2 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            dependencies={"numpy": "1.24.1"}  # Minor version change
        )

        digest1 = env1.compute_digest()
        digest2 = env2.compute_digest()

        assert digest1 != digest2, "Digest should change with dependency version"

    def test_environment_digest_with_container(self):
        """Digest should include container image."""
        env1 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64"
        )

        env2 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            container_image="sha256:abc123"
        )

        digest1 = env1.compute_digest()
        digest2 = env2.compute_digest()

        assert digest1 != digest2, "Container image should affect digest"

    def test_capture_current_environment(self):
        """Test capturing current Python environment."""
        env = EnvironmentDigest.capture_current()

        # Check Python version matches current
        expected_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        assert env.python_version == expected_version

        # Check platform is reasonable
        assert platform.system().lower() in env.platform
        assert platform.machine() in env.platform

    def test_with_dependencies(self):
        """Test updating dependencies."""
        env = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            dependencies={"numpy": "1.24.0"}
        )

        # Add more dependencies
        new_env = env.with_dependencies({
            "scipy": "1.10.0",
            "pandas": "2.0.0"
        })

        assert "numpy" in new_env.dependencies
        assert "scipy" in new_env.dependencies
        assert "pandas" in new_env.dependencies
        assert new_env.dependencies["scipy"] == "1.10.0"

        # Original should be unchanged (immutable)
        assert "scipy" not in env.dependencies

    def test_to_json(self):
        """Test JSON serialization."""
        env = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            dependencies={"numpy": "1.24.0"},
            cuda_version="11.8"
        )

        json_data = env.to_json()

        assert json_data["python_version"] == "3.11.5"
        assert json_data["platform"] == "linux-x86_64"
        assert json_data["dependencies"]["numpy"] == "1.24.0"
        assert json_data["cuda_version"] == "11.8"
        assert "digest" in json_data
        assert len(json_data["digest"]) == 64  # BLAKE2b-256 hex

    def test_thread_count_affects_digest(self):
        """Thread count should affect reproducibility digest."""
        env1 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            thread_count=1
        )

        env2 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            thread_count=4  # Different thread count
        )

        digest1 = env1.compute_digest()
        digest2 = env2.compute_digest()

        assert digest1 != digest2, "Thread count affects reproducibility"

    def test_rng_algorithm_affects_digest(self):
        """RNG algorithm should affect digest."""
        env1 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            rng_algorithm="PCG64"
        )

        env2 = EnvironmentDigest(
            python_version="3.11.5",
            platform="linux-x86_64",
            rng_algorithm="MT19937"  # Different RNG
        )

        digest1 = env1.compute_digest()
        digest2 = env2.compute_digest()

        assert digest1 != digest2, "RNG algorithm affects results"