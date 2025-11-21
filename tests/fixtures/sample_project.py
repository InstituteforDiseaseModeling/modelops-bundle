"""Sample project fixture - source of truth for test data."""

from pathlib import Path
from typing import Dict, Any


# Sample file contents
MODEL_PY = '''"""Basic SIR Epidemiological Models using modelops-calabaria"""
import numpy as np
import pandas as pd
from typing import Dict, Any
import yaml
import modelops_calabaria as cb


class StochasticSIR(cb.BaseModel):
    """Stochastic SIR model with randomized transmission events."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.beta = config['parameters']['transmission_rate']
        self.gamma = config['parameters']['recovery_rate']
        self.population = config['parameters']['population']

    def simulate(self, params: Dict[str, Any], seed: int = None) -> pd.DataFrame:
        """Run stochastic SIR model simulation.

        Args:
            params: Parameter overrides
            seed: Random seed for reproducibility

        Returns:
            DataFrame with simulation results
        """
        if seed is not None:
            np.random.seed(seed)

        # Allow parameter overrides
        beta = params.get('beta', self.beta)
        gamma = params.get('gamma', self.gamma)
        days = params.get('days', 365)
        initial_infected = params.get('initial_infected', 10)

        S, I, R = self.population - initial_infected, initial_infected, 0

        results = []
        for day in range(days):
            # Add stochastic noise
            noise = np.random.normal(1.0, 0.1)
            new_infections = noise * beta * S * I / self.population
            new_recoveries = gamma * I

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

    def extract_prevalence(self, raw_output: pd.DataFrame, timepoint: int = None) -> float:
        """Extract prevalence at a given timepoint."""
        if timepoint is None:
            return raw_output['infected'].max()
        return raw_output.loc[raw_output['day'] == timepoint, 'infected'].iloc[0]


class DeterministicSIR(cb.BaseModel):
    """Deterministic SIR model without randomness."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.beta = config['parameters']['transmission_rate']
        self.gamma = config['parameters']['recovery_rate']
        self.population = config['parameters']['population']

    def simulate(self, params: Dict[str, Any], seed: int = None) -> pd.DataFrame:
        """Run deterministic SIR model simulation.

        Args:
            params: Parameter overrides
            seed: Random seed (ignored for deterministic model)

        Returns:
            DataFrame with simulation results
        """
        # Deterministic model ignores seed
        beta = params.get('beta', self.beta)
        gamma = params.get('gamma', self.gamma)
        days = params.get('days', 365)
        initial_infected = params.get('initial_infected', 10)

        S, I, R = self.population - initial_infected, initial_infected, 0

        results = []
        for day in range(days):
            new_infections = beta * S * I / self.population
            new_recoveries = gamma * I

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

    def extract_incidence(self, raw_output: pd.DataFrame) -> pd.Series:
        """Extract daily incidence from simulation."""
        # Calculate new infections per day
        incidence = -raw_output['susceptible'].diff()
        incidence[0] = 0  # No new infections on day 0
        return incidence


class NetworkSIR(StochasticSIR):
    """Network-based SIR model that extends StochasticSIR."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.network_effect = config.get('parameters', {}).get('network_effect', 1.2)

    def simulate(self, params: Dict[str, Any], seed: int = None) -> pd.DataFrame:
        """Run network SIR with modified transmission based on network structure."""
        # Get base simulation from parent
        results = super().simulate(params, seed)

        # Apply network effect to infections
        results['infected'] *= self.network_effect

        # Ensure population conservation
        total_pop = results['susceptible'] + results['infected'] + results['recovered']
        results['infected'] = results['infected'] * (self.population / total_pop)

        return results


def main():
    """Run all models with config"""
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    # Run stochastic model
    stochastic = StochasticSIR(config)
    stoch_results = stochastic.simulate({}, seed=42)

    # Run deterministic model
    deterministic = DeterministicSIR(config)
    det_results = deterministic.simulate({})

    print("Stochastic Model:")
    print(f"  Peak infections: {stoch_results['infected'].max():.0f}")
    print(f"  Final recovered: {stoch_results['recovered'].iloc[-1]:.0f}")

    print("\\nDeterministic Model:")
    print(f"  Peak infections: {det_results['infected'].max():.0f}")
    print(f"  Final recovered: {det_results['recovered'].iloc[-1]:.0f}")

    # Save results
    stoch_results.to_csv('output/stochastic_results.csv', index=False)
    det_results.to_csv('output/deterministic_results.csv', index=False)
    print("\\nResults saved to output/")

if __name__ == '__main__':
    main()
'''

TARGETS_PY = '''"""Model targets and metrics calculation"""
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
'''

DATA_CSV = '''date,cases,deaths,recovered,population
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
2024-01-15,2916,63,709,100000'''

CONFIG_YAML = '''# Epidemiological Model Configuration

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
  max_reproduction_number: 5.0'''

REQUIREMENTS_TXT = '''numpy>=1.21.0
pandas>=1.3.0
pyyaml>=6.0
matplotlib>=3.5.0
scipy>=1.7.0
modelops-calabaria>=0.1.0'''

PYPROJECT_TOML = '''[project]
name = "test-bundle"
version = "0.1.0"
dependencies = ["modelops-calabaria"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
'''

README_MD = '''# Sample Epidemiological Models

SIR (Susceptible-Infected-Recovered) epidemiological models using modelops-calabaria.

## Models

The project includes three model variants, all inheriting from `cb.BaseModel`:

- **StochasticSIR**: Stochastic model with randomized transmission events
- **DeterministicSIR**: Deterministic model without randomness
- **NetworkSIR**: Network-based model extending StochasticSIR

## Files

- `src/model.py` - Three SIR model implementations using modelops-calabaria
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

# Run all models
python src/model.py

# Calculate targets
python src/targets.py
```

## Registering Models with ModelOps Bundle

These models are designed to work with modelops-bundle's model registry:

```bash
# Auto-discover and register all BaseModel subclasses
mops-bundle register-model src/model.py \\
  --data data/data.csv \\
  --data config.yaml

# Or register specific models only
mops-bundle register-model src/model.py \\
  --class StochasticSIR \\
  --class DeterministicSIR \\
  --data data/data.csv
```'''


def create_sample_project(root: Path, name: str = "epi_model") -> Path:
    """
    Create a sample epidemiological model project.
    
    Args:
        root: Root directory to create project in
        name: Name of the project directory
        
    Returns:
        Path to created project directory
    """
    project_dir = root / name
    project_dir.mkdir(parents=True, exist_ok=True)
    
    # Create directory structure
    (project_dir / "src").mkdir(exist_ok=True)
    (project_dir / "data").mkdir(exist_ok=True)
    (project_dir / "output").mkdir(exist_ok=True)
    
    # Write files
    (project_dir / "src" / "model.py").write_text(MODEL_PY)
    (project_dir / "src" / "targets.py").write_text(TARGETS_PY)
    (project_dir / "data" / "data.csv").write_text(DATA_CSV)
    (project_dir / "config.yaml").write_text(CONFIG_YAML)
    (project_dir / "requirements.txt").write_text(REQUIREMENTS_TXT)
    (project_dir / "README.md").write_text(README_MD)
    (project_dir / "pyproject.toml").write_text(PYPROJECT_TOML)

    return project_dir


def get_expected_files() -> Dict[str, str]:
    """
    Get expected file paths and their content for validation.
    
    Returns:
        Dict mapping relative paths to file contents
    """
    return {
        "src/model.py": MODEL_PY,
        "src/targets.py": TARGETS_PY,
        "data/data.csv": DATA_CSV,
        "config.yaml": CONFIG_YAML,
        "requirements.txt": REQUIREMENTS_TXT,
        "README.md": README_MD,
    }


if __name__ == "__main__":
    # Allow running as a script
    import sys
    
    if len(sys.argv) > 1:
        output_dir = Path(sys.argv[1])
    else:
        output_dir = Path(".")
    
    name = sys.argv[2] if len(sys.argv) > 2 else "epi_model"
    
    project_dir = create_sample_project(output_dir, name)
    print(f"✅ Sample project created at: {project_dir}")
