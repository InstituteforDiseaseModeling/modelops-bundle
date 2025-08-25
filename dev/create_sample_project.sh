#!/bin/bash
set -e

# Get project name from argument or use default
PROJECT_NAME=${1:-"epi_model"}
BASE_DIR="$(dirname "$0")/sample_projects"
PROJECT_DIR="$BASE_DIR/$PROJECT_NAME"

echo "🚀 Creating sample epidemiological model project: $PROJECT_NAME"

# Create/recreate project directory
rm -rf "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR"/{src,data}

# Create src/model.py
cat > "$PROJECT_DIR/src/model.py" << 'EOF'
"""Basic SIR Epidemiological Model"""
import numpy as np
import pandas as pd
from typing import Tuple, Dict, Any
import yaml

class SIRModel:
    def __init__(self, config: Dict[str, Any]):
        self.beta = config['parameters']['transmission_rate']
        self.gamma = config['parameters']['recovery_rate']
        self.population = config['parameters']['population']
    
    def run_simulation(self, days: int, initial_infected: int = 1) -> pd.DataFrame:
        """Run SIR model simulation"""
        S, I, R = self.population - initial_infected, initial_infected, 0
        
        results = []
        for day in range(days):
            new_infections = self.beta * S * I / self.population
            new_recoveries = self.gamma * I
            
            S -= new_infections
            I += new_infections - new_recoveries  
            R += new_recoveries
            
            results.append({
                'day': day,
                'susceptible': max(0, S),
                'infected': max(0, I),
                'recovered': max(0, R)
            })
        
        return pd.DataFrame(results)

def main():
    """Run model with config"""
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    model = SIRModel(config)
    results = model.run_simulation(config['simulation']['days'])
    
    print(f"Peak infections: {results['infected'].max():.0f}")
    print(f"Final recovered: {results['recovered'].iloc[-1]:.0f}")
    
    results.to_csv('output/results.csv', index=False)
    print("Results saved to output/results.csv")

if __name__ == '__main__':
    main()
EOF

# Create src/targets.py  
cat > "$PROJECT_DIR/src/targets.py" << 'EOF'
"""Model targets and metrics calculation"""
import pandas as pd
from typing import Dict, Any

class ModelTargets:
    """Calculate key epidemiological targets"""
    
    @staticmethod
    def calculate_targets(results: pd.DataFrame) -> Dict[str, Any]:
        """Calculate target metrics from model results"""
        return {
            'peak_infections': results['infected'].max(),
            'peak_day': results.loc[results['infected'].idxmax(), 'day'],
            'total_infected': results['recovered'].iloc[-1],
            'attack_rate': results['recovered'].iloc[-1] / (
                results['susceptible'].iloc[0] + 
                results['infected'].iloc[0] + 
                results['recovered'].iloc[0]
            ) * 100,
            'reproduction_number': results['infected'].iloc[1] / results['infected'].iloc[0] if len(results) > 1 else 1.0
        }
    
    @staticmethod
    def validate_targets(targets: Dict[str, Any]) -> bool:
        """Validate target values are reasonable"""
        return (
            0 <= targets['attack_rate'] <= 100 and
            targets['peak_infections'] >= 0 and
            targets['reproduction_number'] >= 0
        )

def main():
    """Calculate targets from results"""
    try:
        results = pd.read_csv('output/results.csv')
        targets = ModelTargets.calculate_targets(results)
        
        print("📊 Model Targets:")
        for key, value in targets.items():
            print(f"  {key}: {value:.2f}")
        
        if ModelTargets.validate_targets(targets):
            print("✅ All targets are valid")
        else:
            print("❌ Some targets are invalid")
            
    except FileNotFoundError:
        print("❌ No results file found. Run model.py first.")

if __name__ == '__main__':
    main()
EOF

# Create data/data.csv
cat > "$PROJECT_DIR/data/data.csv" << 'EOF'
date,cases,deaths,recovered,population
2024-01-01,10,0,0,100000
2024-01-02,15,0,2,100000
2024-01-03,22,0,5,100000
2024-01-04,34,1,8,100000
2024-01-05,51,1,12,100000
2024-01-06,76,2,18,100000
2024-01-07,114,3,27,100000
2024-01-08,171,4,41,100000
2024-01-09,256,6,62,100000
2024-01-10,384,9,93,100000
2024-01-11,576,13,140,100000
2024-01-12,864,19,210,100000
2024-01-13,1296,28,315,100000
2024-01-14,1944,42,473,100000
2024-01-15,2916,63,709,100000
EOF

# Create config.yaml
cat > "$PROJECT_DIR/config.yaml" << 'EOF'
# Epidemiological Model Configuration

model:
  name: "SIR_Basic"
  version: "1.0.0"
  description: "Basic SIR epidemiological model"

parameters:
  transmission_rate: 0.3      # beta - rate of transmission
  recovery_rate: 0.1          # gamma - rate of recovery  
  population: 100000          # total population size

simulation:
  days: 365                   # simulation duration
  initial_infected: 10        # initial number of infected

data:
  input_file: "data/data.csv"
  output_dir: "output/"

validation:
  max_attack_rate: 80.0       # maximum acceptable attack rate %
  min_reproduction_number: 0.5
  max_reproduction_number: 5.0
EOF

# Create requirements.txt
cat > "$PROJECT_DIR/requirements.txt" << 'EOF'
numpy>=1.21.0
pandas>=1.3.0
pyyaml>=6.0
matplotlib>=3.5.0
scipy>=1.7.0
EOF

# Create README.md
cat > "$PROJECT_DIR/README.md" << 'EOF'
# Sample Epidemiological Model

A minimal SIR (Susceptible-Infected-Recovered) epidemiological model for testing purposes.

## Files

- `src/model.py` - Core SIR model implementation
- `src/targets.py` - Target metrics calculation
- `data/data.csv` - Sample epidemiological data
- `config.yaml` - Model configuration
- `requirements.txt` - Python dependencies

## Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Create output directory
mkdir -p output

# Run model
python src/model.py

# Calculate targets
python src/targets.py
```

## Model Parameters

- **transmission_rate (β)**: Rate at which susceptible individuals become infected
- **recovery_rate (γ)**: Rate at which infected individuals recover
- **population**: Total population size

## Outputs

- `output/results.csv` - Daily simulation results
- Key metrics: peak infections, attack rate, reproduction number
EOF

# Create output directory
mkdir -p "$PROJECT_DIR/output"

echo "✅ Sample project created at: $PROJECT_DIR"
echo ""
echo "📁 Project structure:"
find "$PROJECT_DIR" -type f | sort | sed 's|.*sample_projects/||' | sed 's|^|  |'
echo ""
echo "🚀 To test the model:"
echo "  cd $PROJECT_DIR"
echo "  pip install -r requirements.txt"
echo "  python src/model.py"
echo "  python src/targets.py"