#!/bin/bash
set -e

# Get project name from argument or use default
PROJECT_NAME=${1:-"epi_model"}
BASE_DIR="$(dirname "$0")/sample_projects"

echo "🚀 Creating sample epidemiological model project: $PROJECT_NAME"

# Use Python fixture as source of truth
cd "$(dirname "$0")/.."
python -m tests.fixtures.sample_project "$BASE_DIR" "$PROJECT_NAME"

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