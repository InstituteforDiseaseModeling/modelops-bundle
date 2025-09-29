#!/bin/bash
set -e

# Get project name from argument or use default
PROJECT_NAME=${1:-"epi_model"}
# Get absolute path to the sample_projects directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$SCRIPT_DIR/sample_projects"

echo "🚀 Creating sample epidemiological model project: $PROJECT_NAME"

# Use Python fixture as source of truth
cd "$SCRIPT_DIR/.."
uv run python -m tests.fixtures.sample_project "$BASE_DIR" "$PROJECT_NAME"

# Show what was created
echo ""
echo "📁 Project structure:"
find "$BASE_DIR/$PROJECT_NAME" -type f | sort | sed "s|.*sample_projects/||" | sed 's|^|  |'
echo ""
echo "🚀 To test the model:"
echo "  cd $BASE_DIR/$PROJECT_NAME"
echo "  pip install -r requirements.txt"
echo "  python src/model.py"
echo "  python src/targets.py"
