# ModelOps Bundle Development Makefile
#
# This Makefile provides convenient targets for local development,
# testing, and Docker service management.

.DEFAULT_GOAL := help
.PHONY: help up down reset-registry ps sample-project sample-project-named clean-sample setup-azure azure-env

help: ## Show this help message
	@echo 'modelops-bundle dev commands'
	@echo '===================================='
	@echo ''
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ''
	@echo 'Examples:'
	@echo '  make up                      # Start development services'
	@echo '  make setup-azure             # Set up Azure blob storage with Azurite'
	@echo '  make down                    # Stop development services'
	@echo '  make ps                      # Show service status'
	@echo '  make sample-project          # Create a sample epidemiological model project for testing'
	@echo '  make sample-project-named NAME=my_model  # Create a named sample project'
	@echo ''
	@echo 'Storage setup:'
	@echo '  eval $$(make azure-env)      # Set Azure connection string in shell'
	@echo '  make setup-azure             # Complete Azure storage setup guide'



up: ## Start development services (Azurite, OCI Registry, Registry UI)
	@echo "🚀 Starting development services..."
	docker-compose -f dev/docker-compose.yml up -d
	@echo "✅ Services started:"
	@echo "   🟦 Azurite (Azure):     http://localhost:10000"
	@echo "   📦 OCI Registry:        http://localhost:5555"
	@echo "   🖥️  Registry UI:         http://localhost:8080"
	@echo ""
	@echo "💡 Next steps:"
	@echo "   make ps          # Check service status"

down: ## Stop development services
	@echo "🛑 Stopping development services..."
	docker-compose -f dev/docker-compose.yml down
	@echo "✅ Services stopped"

reset-registry: ## Reset the registry (removes all stored artifacts)
	@echo "🔄 Resetting registry (this will delete all stored artifacts)..."
	docker-compose -f dev/docker-compose.yml down -v
	@echo "✅ Registry reset complete"
	@echo "💡 Run 'make up' to start fresh services"

ps: ## Show development service status
	@echo "📋 Service Status:"
	docker-compose -f dev/docker-compose.yml ps
	@echo ""
	@echo "🌐 Service URLs:"
	@echo "   🟦 Azurite (Azure Blob):   http://localhost:10000"
	@echo "   📦 OCI Registry:           http://localhost:5555"
	@echo "   🖥️ Registry UI:            http://localhost:8080"


sample-project: ## Create a sample epidemiological model project for testing
	@dev/create_sample_project.sh
	@echo ""
	@echo "📊 Sample project created at: dev/sample_projects/epi_model/"
	@echo "💡 To create with custom name: make sample-project-named NAME=my_model"

sample-project-named: ## Create a named sample project (use NAME=project_name)
	@if [ -z "$(NAME)" ]; then \
		echo "❌ Error: Please specify NAME=project_name"; \
		exit 1; \
	fi
	@dev/create_sample_project.sh $(NAME)
	@echo ""
	@echo "📊 Sample project created at: dev/sample_projects/$(NAME)/"

clean-sample: ## Clean and recreate the epi_model sample project
	@echo "🧹 Cleaning sample project..."
	@rm -rf dev/sample_projects/epi_model
	@$(MAKE) sample-project

setup-azure: ## Set up Azure blob storage (Azurite) for modelops-bundle
	@echo "🔧 Setting up Azurite blob storage..."
	@# Check if Azurite is running
	@docker ps | grep -q modelops-bundles-azurite || (echo "❌ Azurite not running. Run 'make up' first"; exit 1)
	@echo "✅ Azurite is running"
	@echo ""
	@# Create the container using Azure CLI or curl
	@echo "📦 Creating modelops-bundles container in Azurite..."
	@docker exec modelops-bundles-azurite sh -c '\
		curl -X PUT "http://localhost:10000/devstoreaccount1/modelops-bundles?restype=container" \
		-H "x-ms-version: 2019-12-12" \
		-H "x-ms-date: $$(date -u +"%a, %d %b %Y %H:%M:%S GMT")" \
		2>/dev/null' || true
	@echo "✅ Container ready"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "📋 To use Azure storage in your ModelOps Bundle project:"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "1️⃣  Set the connection string in your shell:"
	@echo ""
	@echo "    $$(make -s azure-env)"
	@echo ""
	@echo "2️⃣  Initialize a new project with Azure storage:"
	@echo ""
	@echo "    modelops-bundle init localhost:5555/my-model --storage-preset azurite"
	@echo ""
	@echo "   Or with explicit configuration:"
	@echo ""
	@echo "    modelops-bundle init localhost:5555/my-model \\"
	@echo "      --storage-provider azure \\"
	@echo "      --storage-container modelops-bundles \\"
	@echo "      --storage-threshold 10  # 10MB threshold"
	@echo ""
	@echo "3️⃣  For existing projects, add to .modelops-bundle/config.yaml:"
	@echo ""
	@echo "    storage:"
	@echo "      provider: azure"
	@echo "      container: modelops-bundles"
	@echo "      threshold_bytes: 52428800  # 50MB"
	@echo ""
	@echo "💡 Quick start (copy & paste):"
	@echo "   eval \$$(make azure-env) && modelops-bundle init localhost:5555/test --storage-preset azurite"
	@echo ""

azure-env: ## Output Azure (Azurite) environment variable for shell
	@echo 'export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1"'


