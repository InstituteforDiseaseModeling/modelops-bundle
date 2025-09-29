"""Utilities for registry-dependent tests."""

import socket
import os
import pytest


def can_connect_to_registry(host_port: str = None) -> bool:
    """Check if we can connect to the registry.
    
    Args:
        host_port: Host:port string, defaults to REGISTRY_AVAILABLE env var
        
    Returns:
        True if connection successful, False otherwise
    """
    if host_port is None:
        host_port = "localhost:5555"
    
    try:
        host, port = host_port.split(":")
        s = socket.socket()
        s.settimeout(2.0)  # Increased timeout for CI stability
        try:
            s.connect((host, int(port)))
            return True
        except Exception:
            return False
        finally:
            s.close()
    except Exception:
        return False


def skip_if_no_registry(registry_url: str = None):
    """Skip test if registry is not reachable.
    
    Use as first line in integration tests that need a registry.
    """
    if not can_connect_to_registry(registry_url):
        pytest.skip(f"Registry not available at {registry_url or 'localhost:5555'}")