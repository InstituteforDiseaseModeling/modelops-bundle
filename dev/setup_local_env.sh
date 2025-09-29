#!/bin/bash
# Setup local environment configuration for ModelOps Bundle development
# Creates a BundleEnvironment YAML for local development with Docker registry and Azurite

set -e

BUNDLE_ENV_DIR="$HOME/.modelops/bundle-env"
LOCAL_ENV_FILE="$BUNDLE_ENV_DIR/local.yaml"

echo "🔧 Setting up local ModelOps BundleEnvironment..."

# Create bundle-env directory
mkdir -p "$BUNDLE_ENV_DIR"

# Create local environment YAML matching BundleEnvironment contract
cat > "$LOCAL_ENV_FILE" << EOF
environment: local
timestamp: '$(date -u +"%Y-%m-%dT%H:%M:%SZ")'
registry:
  provider: docker
  login_server: localhost:5555
  requires_auth: false
storage:
  provider: azure
  container: modelops-bundles
  connection_string: DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1
EOF

echo "✅ Created local BundleEnvironment config: $LOCAL_ENV_FILE"
echo ""
echo "The local environment is configured to use:"
echo "  • Registry: localhost:5555 (Docker registry)"
echo "  • Storage: Azurite at localhost:10000 (Azure emulator)"
echo "  • Container: modelops-bundles"
echo ""
echo "Use with: mops-bundle init <project> --env local"