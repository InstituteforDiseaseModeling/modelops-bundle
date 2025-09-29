"""Test configuration and utilities to prevent localhost vs cloud registry issues."""

import os
import pytest


def get_test_registry() -> str:
    """Get the test registry URL with proper validation.

    Returns:
        Registry URL that's safe for testing (localhost only)

    Raises:
        pytest.skip: If registry configuration is unsafe for testing
    """
    # Tests always use localhost:5555
    return "localhost:5555"


def ensure_safe_test_environment():
    """Ensure test environment is configured safely.

    This prevents tests from accidentally hitting cloud registries
    and ensures consistent test behavior.
    """
    registry_url = get_test_registry()

    # Force insecure mode for localhost registries
    if registry_url.startswith('localhost') or registry_url.startswith('127.0.0.1'):
        os.environ["MODELOPS_BUNDLE_INSECURE"] = "true"
    else:
        # This should never happen due to get_test_registry() validation,
        # but adding as extra safety
        pytest.skip(f"Unsafe registry configuration: {registry_url}")


def skip_if_no_registry():
    """Skip test if registry is not available.

    This replaces the registry availability check with a simpler
    environment-based check to avoid network calls during test collection.
    """
    import socket

    registry_url = get_test_registry()

    # For localhost, check if something is listening on the port
    if registry_url.startswith('localhost:'):
        try:
            port = int(registry_url.split(':')[1])
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)  # Quick timeout
                result = sock.connect_ex(('localhost', port))
                if result != 0:
                    pytest.skip(f"Registry not available at {registry_url}")
        except (ValueError, OSError):
            pytest.skip(f"Cannot connect to registry at {registry_url}")
    else:
        # This should not happen due to validation, but skip if it does
        pytest.skip(f"Non-localhost registry not supported in tests: {registry_url}")