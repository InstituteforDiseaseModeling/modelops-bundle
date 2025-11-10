"""Minimal authentication for standalone CLI usage.

This module provides lightweight auth implementations for developers
using mops-bundle CLI directly. For production use within ModelOps,
the full auth implementations from modelops are used via dependency injection.
"""

import os
import json
import subprocess
import urllib.request
import urllib.parse
from modelops_contracts import AuthProvider, Credential


class AzureCliAuth(AuthProvider):
    """Minimal Azure CLI auth for ACR operations."""

    def get_registry_credential(self, registry: str) -> Credential:
        """Get ACR token using Azure CLI.

        Args:
            registry: Registry endpoint (e.g., "myacr.azurecr.io")

        Returns:
            Credential with ACR token

        Raises:
            AuthError: If Azure CLI authentication fails
        """
        registry_name = registry.split('.')[0]

        try:
            result = subprocess.run(
                ["az", "acr", "login", "--name", registry_name, "--expose-token"],
                capture_output=True,
                text=True,
                check=False  # Handle errors manually for better diagnostics
            )

            # Check if command succeeded
            if result.returncode != 0:
                from ..errors import AuthError
                error_msg = result.stderr.strip() or result.stdout.strip()
                raise AuthError(
                    f"Azure CLI authentication failed for registry '{registry_name}'.\n"
                    f"Error: {error_msg}\n\n"
                    f"Try:\n"
                    f"  1. az login\n"
                    f"  2. az acr login --name {registry_name}"
                )

            # Check if stdout has content before parsing
            if not result.stdout.strip():
                from ..errors import AuthError
                raise AuthError(
                    f"Azure CLI returned empty output for registry '{registry_name}'.\n"
                    f"stderr: {result.stderr}\n\n"
                    f"This usually means authentication failed. Try:\n"
                    f"  1. az login\n"
                    f"  2. az acr login --name {registry_name}"
                )

            # Parse JSON response
            try:
                token_info = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                from ..errors import AuthError
                raise AuthError(
                    f"Failed to parse Azure CLI output for registry '{registry_name}'.\n"
                    f"Parse error: {e}\n"
                    f"stdout: {result.stdout[:200]}\n"
                    f"stderr: {result.stderr[:200]}\n\n"
                    f"Try:\n"
                    f"  1. az login\n"
                    f"  2. az acr login --name {registry_name}"
                )

            # Get the refresh token and exchange it for an access token
            refresh_token = token_info.get("refreshToken")
            if not refresh_token:
                from ..errors import AuthError
                raise AuthError(
                    f"No refresh token in Azure CLI response for registry '{registry_name}'.\n"
                    f"Response keys: {list(token_info.keys())}\n\n"
                    f"Try: az acr login --name {registry_name}"
                )

            login_server = token_info.get("loginServer", f"{registry_name}.azurecr.io")

            # Exchange refresh token for access token (required for blob operations)
            access_token = self._exchange_token(login_server, refresh_token)

            return Credential(
                username="00000000-0000-0000-0000-000000000000",
                secret=access_token,
                expires_at=None  # Access tokens typically last 1 hour
            )
        except subprocess.CalledProcessError as e:
            # This shouldn't happen with check=False, but keep for safety
            from ..errors import AuthError
            raise AuthError(f"Azure CLI command failed: {e.stderr}")
        except KeyError as e:
            from ..errors import AuthError
            raise AuthError(
                f"Missing expected field in Azure CLI response: {e}\n"
                f"Try: az acr login --name {registry_name}"
            )

    def get_storage_credential(self, account: str, container: str) -> Credential:
        """Storage auth not needed for bundle CLI operations."""
        raise NotImplementedError("Storage auth handled via environment")

    def _exchange_token(self, login_server: str, refresh_token: str) -> str:
        """Exchange ACR refresh token for access token.

        Args:
            login_server: ACR login server (e.g., "myacr.azurecr.io")
            refresh_token: Refresh token from Azure CLI

        Returns:
            Access token for ACR operations

        Raises:
            AuthError: If token exchange fails
        """
        # ACR OAuth2 endpoint
        oauth_url = f"https://{login_server}/oauth2/token"

        # Prepare request
        params = {
            "grant_type": "refresh_token",
            "service": login_server,
            "scope": "repository:*:*",  # Full access scope
            "refresh_token": refresh_token
        }

        data = urllib.parse.urlencode(params).encode('utf-8')
        req = urllib.request.Request(oauth_url, data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                access_token = result.get("access_token")
                if not access_token:
                    from ..errors import AuthError
                    raise AuthError("No access token in OAuth response")
                return access_token
        except Exception as e:
            from ..errors import AuthError
            raise AuthError(f"Token exchange failed: {e}")


class StaticAuth(AuthProvider):
    """Static auth from environment variables."""

    def get_registry_credential(self, registry: str) -> Credential:
        """Get credentials from environment.

        Args:
            registry: Registry endpoint (ignored for static auth)

        Returns:
            Credential from environment variables or empty for anonymous
        """
        username = os.environ.get("REGISTRY_USERNAME", "")
        password = os.environ.get("REGISTRY_PASSWORD", "")

        if not password:
            # Return empty for anonymous/public registries
            return Credential(username="", secret="")

        return Credential(username=username, secret=password)

    def get_storage_credential(self, account: str, container: str) -> Credential:
        """Not implemented for static auth."""
        raise NotImplementedError("Storage operations not supported in static auth mode")


def get_auth_provider(registry_ref: str) -> AuthProvider:
    """Get appropriate auth provider for standalone CLI use.

    This is a minimal implementation for mops-bundle CLI. When used
    within ModelOps, auth is provided via dependency injection.

    Args:
        registry_ref: Registry reference (e.g., "myacr.azurecr.io/repo")

    Returns:
        AuthProvider implementation
    """
    # In K8s/CI, use env vars
    if os.environ.get("REGISTRY_USERNAME") and os.environ.get("REGISTRY_PASSWORD"):
        return StaticAuth()

    # Extract registry hostname
    registry_host = registry_ref.split('/')[0].lower() if '/' in registry_ref else registry_ref.lower()

    # On workstation with Azure CLI
    if ".azurecr.io" in registry_host:
        # Check if az CLI is available
        if subprocess.run(["which", "az"], capture_output=True).returncode == 0:
            return AzureCliAuth()

    # Default to static/anonymous
    return StaticAuth()


__all__ = ["get_auth_provider", "AzureCliAuth", "StaticAuth"]