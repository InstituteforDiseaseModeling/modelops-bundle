# ============================================================================
# ModelOps Bundle - Unified Development Makefile
# ============================================================================

.DEFAULT_GOAL := help
.PHONY: help start stop reset status test quickstart clean

# ============================================================================
# CORE WORKFLOW - Simple, unified commands
# ============================================================================

help: ## Show this help message
	@echo 'ModelOps Bundle Development'
	@echo '============================'
	@echo ''
	@echo 'Quick Start:'
	@echo '  make quickstart    # Complete setup and run test (recommended for first time)'
	@echo ''
	@echo 'Daily Workflow:'
	@echo '  make start         # Start all services'
	@echo '  make test          # Run sample push/pull test'
	@echo '  make status        # Check service status'
	@echo '  make stop          # Stop all services'
	@echo ''
	@echo 'Maintenance:'
	@echo '  make reset         # Reset everything (data + services)'
	@echo '  make clean         # Clean all generated files and data'
	@echo ''
	@echo 'Advanced targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -v "^[A-Z]" | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ============================================================================
# PRIMARY COMMANDS - What most users need
# ============================================================================

quickstart: ## 🚀 Complete setup from scratch (first time users)
	@echo "🚀 ModelOps Bundle Quick Start"
	@echo "=============================="
	@make reset
	@make start
	@sleep 2  # Give services time to start
	@make test
	@echo ""
	@echo "✨ Quick start complete! Services are running."
	@echo "📚 See README.md for next steps"

start: ## ▶️  Start all services (registry + storage)
	@echo "▶️  Starting services..."
	@docker-compose -f dev/docker-compose.yml up -d
	@sleep 1
	@make _ensure-azure-container > /dev/null 2>&1
	@bash dev/setup_local_env.sh > /dev/null 2>&1
	@echo "✅ Services started!"
	@make status

stop: ## ⏹️  Stop all services
	@echo "⏹️  Stopping services..."
	@docker-compose -f dev/docker-compose.yml down
	@echo "✅ Services stopped"

reset: ## 🔄 Reset everything (stops services, clears data)
	@echo "🔄 Resetting environment..."
	@docker-compose -f dev/docker-compose.yml down -v 2>/dev/null || true
	@rm -rf dev/sample_projects/* 2>/dev/null || true
	@echo "✅ Environment reset"

status: ## 📊 Show service status
	@echo "📊 Service Status"
	@echo "================"
	@echo ""
	@docker-compose -f dev/docker-compose.yml ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "❌ Services not running"
	@echo ""
	@echo "📌 Service URLs:"
	@echo "  • OCI Registry:  http://localhost:5555"
	@echo "  • Registry UI:   http://localhost:8080"
	@echo "  • Azure Storage: http://localhost:10000"
	@echo ""
	@if docker ps | grep -q modelops-bundles-registry; then \
		echo "✅ Local registry running at localhost:5555"; \
	else \
		echo "⚠️  Local registry not running - run: make start"; \
	fi

test: ## 🧪 Run end-to-end test with sample project (OCI only, no blob storage)
	@echo "🧪 Running end-to-end test (OCI layers only)..."
	@echo "⚠️  Note: Blob storage testing requires real Azure ACR"
	@make _ensure-services
	@echo "📦 Creating test project..."
	@rm -rf dev/sample_projects/test_e2e 2>/dev/null || true
	@dev/create_sample_project.sh test_e2e > /dev/null
	@echo "🔧 Initializing with mops-bundle..."
	@cd dev/sample_projects/test_e2e && \
		uv run mops-bundle init --env local > /dev/null
	@echo "📝 Adding regular files to track..."
	@cd dev/sample_projects/test_e2e && \
		uv run mops-bundle add src/model.py src/targets.py config.yaml requirements.txt README.md data/data.csv > /dev/null 2>&1
	@echo "🗂️  Creating large file (60MB) to trigger blob storage..."
	@cd dev/sample_projects/test_e2e && \
		dd if=/dev/urandom of=data/large_dataset.bin bs=1M count=60 2>/dev/null && \
		echo "  Created 60MB test file: data/large_dataset.bin"
	@echo "📝 Adding large file to bundle..."
	@cd dev/sample_projects/test_e2e && \
		uv run mops-bundle add data/large_dataset.bin
	@echo "📤 Testing push (OCI layers + blob storage)..."
	@cd dev/sample_projects/test_e2e && \
		uv run mops-bundle push
	@echo "📥 Testing pull with file restoration..."
	@cd dev/sample_projects/test_e2e && \
		echo "  Removing files to test restoration..." && \
		rm -f src/model.py data/large_dataset.bin && \
		uv run mops-bundle pull --restore-deleted && \
		if [ -f src/model.py ] && [ -f data/large_dataset.bin ]; then \
			echo "  ✓ Both small and large files successfully restored"; \
			ls -lh data/large_dataset.bin | awk '{print "  ✓ Large file size:", $$5}'; \
		else \
			echo "  ✗ Files not restored - checking status..."; \
			uv run mops-bundle status; \
			exit 1; \
		fi
	@echo "✅ Test passed (OCI + blob storage)!"

test-blob: ## 🗄️  Test blob storage specifically with large files (local only - won't actually use blob)
	@echo "🗄️  Testing large file handling (local registry - no real blob storage)..."
	@echo "⚠️  This only tests the file size logic, not actual blob storage"
	@make _ensure-services
	@echo "📦 Creating test project..."
	@rm -rf dev/sample_projects/test_blob 2>/dev/null || true
	@dev/create_sample_project.sh test_blob > /dev/null
	@echo "🔧 Initializing bundle..."
	@cd dev/sample_projects/test_blob && \
		uv run mops-bundle init blob-test --env local > /dev/null
	@echo "🗂️  Creating multiple large files to test blob storage..."
	@cd dev/sample_projects/test_blob && \
		echo "  Creating 60MB training data..." && \
		dd if=/dev/urandom of=data/training_data.bin bs=1M count=60 2>/dev/null && \
		echo "  Creating 75MB model weights..." && \
		dd if=/dev/urandom of=data/model_weights.bin bs=1M count=75 2>/dev/null && \
		echo "  Creating 10MB config (under threshold)..." && \
		dd if=/dev/urandom of=data/small_config.bin bs=1M count=10 2>/dev/null && \
		ls -lh data/*.bin | awk '{print "  ", $$5, $$9}'
	@echo "📝 Adding all files..."
	@cd dev/sample_projects/test_blob && \
		uv run mops-bundle add . > /dev/null 2>&1
	@echo "📤 Pushing with mixed storage (OCI + blob)..."
	@cd dev/sample_projects/test_blob && \
		uv run mops-bundle push 	@echo "🔍 Verifying files were uploaded..."
	@cd dev/sample_projects/test_blob && \
		rm -rf data/*.bin && \
		uv run mops-bundle pull --env local > /dev/null && \
		if [ -f data/training_data.bin ] && [ -f data/model_weights.bin ] && [ -f data/small_config.bin ]; then \
			echo "  ✓ All files restored successfully"; \
			echo "  File sizes after restoration:"; \
			ls -lh data/*.bin | awk '{print "    ", $$5, $$9}'; \
		else \
			echo "  ✗ Some files missing!"; \
			ls -la data/; \
			exit 1; \
		fi
	@echo "✅ Blob storage test passed!"

test-azure: ## 🌩️  Test with real Azure ACR (includes blob storage)
	@echo "🌩️  Testing with Azure ACR (real blob storage)..."
	@if ! az account show > /dev/null 2>&1; then \
		echo "❌ Not logged into Azure. Run: az login"; \
		exit 1; \
	fi
	@if [ -z "$${ACR_NAME}" ]; then \
		echo "❌ ACR_NAME environment variable not set"; \
		echo "   Run: export ACR_NAME=<your-acr-name>"; \
		exit 1; \
	fi
	@echo "📦 Creating test project..."
	@rm -rf dev/sample_projects/test_azure 2>/dev/null || true
	@dev/create_sample_project.sh test_azure > /dev/null
	@echo "🔧 Initializing with ACR..."
	@cd dev/sample_projects/test_azure && \
		uv run mops-bundle init test-blob --env dev > /dev/null
	@echo "🗂️  Creating large file (60MB) for blob storage..."
	@cd dev/sample_projects/test_azure && \
		dd if=/dev/urandom of=data/large_model.bin bs=1M count=60 2>/dev/null && \
		echo "  Created 60MB file: data/large_model.bin"
	@echo "📝 Adding files..."
	@cd dev/sample_projects/test_azure && \
		uv run mops-bundle add src/model.py data/data.csv data/large_model.bin
	@echo "🔐 Authenticating with ACR..."
	@az acr login --name $${ACR_NAME}
	@echo "📤 Pushing to ACR (watch for blob storage)..."
	@cd dev/sample_projects/test_azure && \
		uv run mops-bundle push
	@echo "📥 Testing pull..."
	@cd dev/sample_projects/test_azure && \
		rm -f data/large_model.bin && \
		uv run mops-bundle pull && \
		if [ -f data/large_model.bin ]; then \
			echo "  ✓ Large file restored from blob storage"; \
			ls -lh data/large_model.bin | awk '{print "  Size:", $$5}'; \
		else \
			echo "  ✗ Large file not restored!"; \
			exit 1; \
		fi
	@echo "✅ Azure ACR test passed (OCI + blob storage)!"

# ============================================================================
# CONVENIENCE COMMANDS
# ============================================================================

logs: ## 📜 Show service logs
	@docker-compose -f dev/docker-compose.yml logs -f

clean: ## 🧹 Clean everything (reset + remove Docker images)
	@echo "🧹 Deep cleaning..."
	@make reset
	@docker-compose -f dev/docker-compose.yml down --rmi local 2>/dev/null || true
	@docker volume prune -f 2>/dev/null || true
	@echo "✅ Deep clean complete"

# ============================================================================
# SAMPLE PROJECT MANAGEMENT (Advanced)
# ============================================================================

sample: ## Create sample epidemiological model with blob storage test
	@make sample-create NAME=epi_model
	@echo ""
	@echo "🗂️  Creating large dataset (60MB) to test blob storage..."
	@dd if=/dev/urandom of=dev/sample_projects/epi_model/data/simulation_cache.bin bs=1M count=60 2>/dev/null
	@ls -lh dev/sample_projects/epi_model/data/simulation_cache.bin | awk '{print "  Created large file:", $$5, $$9}'
	@echo ""
	@echo "📌 To test blob storage, run:"
	@echo "  cd dev/sample_projects/epi_model"
	@echo "  mops-bundle init --registry localhost:5555/epi_model"
	@echo "  mops-bundle add .  # Add all files including large one"
	@echo "  mops-bundle push   # Will use blob storage for large file"

sample-create: ## Create named sample project (use NAME=xxx)
	@if [ -z "$(NAME)" ]; then \
		echo "❌ Usage: make sample-create NAME=my_project"; \
		exit 1; \
	fi
	@dev/create_sample_project.sh $(NAME)
	@echo "✅ Created: dev/sample_projects/$(NAME)/"
	@echo ""
	@echo "Next steps:"
	@echo "  cd dev/sample_projects/$(NAME)"
	@echo "  mops-bundle init --registry localhost:5555/$(NAME)"
	@echo "  mops-bundle push"

sample-clean: ## Remove all sample projects
	@rm -rf dev/sample_projects/*
	@echo "✅ Sample projects cleaned"

# ============================================================================
# INTERNAL HELPERS (not shown in help)
# ============================================================================

_ensure-services:
	@docker ps | grep -q modelops-bundles-registry || (echo "❌ Services not running. Run 'make start' first"; exit 1)

_ensure-azure-container:
	@docker exec modelops-bundles-azurite sh -c '\
		curl -X PUT "http://localhost:10000/devstoreaccount1/modelops-bundles?restype=container" \
		-H "x-ms-version: 2019-12-12" \
		-H "x-ms-date: $$(date -u +"%a, %d %b %Y %H:%M:%S GMT")" \
		2>/dev/null' || true