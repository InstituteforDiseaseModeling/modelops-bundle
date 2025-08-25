# ModelOps Bundle Development Makefile
#
# This Makefile provides convenient targets for local development,
# testing, and Docker service management.

.DEFAULT_GOAL := help
.PHONY: help up down reset-registry ps sample-project sample-project-named

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
	@echo '  make down                    # Stop development services'
	@echo '  make ps                      # Show service status'
	@echo '  make sample-project          # Create a sample epidemiological model project for testing'
	@echo '  make sample-project-named NAME=my_model  # Create a named sample project'



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


