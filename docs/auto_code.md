# Auto Code Discovery & Bundle Config

Automatic code-dependency discovery prevents “works on my machine” bugs.
modelops-bundle now reads `.modelops-bundle/config.yaml` (optional) to
control which directories are scanned and how aggressively dependencies
are pulled in when `mops-bundle register-model` runs.

```yaml
# .modelops-bundle/config.yaml
auto_code:
  code_roots: ["src", "lib"]      # relative to repo root
  import_mode: "package"          # or "files"
  ignore:
    - "src/**/benchmarks/**"
    - "src/**/notebooks/**"
```

## Modes

| Mode      | Behaviour                                                                 |
|-----------|---------------------------------------------------------------------------|
| package   | (default) include entire first-party packages that are imported. This is
|           | safe and matches Python’s package boundaries.                             |
| files     | recursively walk imported modules and collect individual `.py` files
|           | within configured roots. Produces leaner bundles at the cost of more
|           | analysis.                                                                 |

### CLI Overrides

- `--auto-code/--no-auto-code` toggles discovery entirely.
- `--code-mode package|files` temporarily overrides the configured mode.
- `--code <path>` still appends manual paths (files or folders).

## Ignore Patterns

`ignore` uses `fnmatch` against project-relative paths. Use this to exclude
benchmarks, notebooks, or generated code from being bundled automatically.

## Registry Diff Output

Both `register-model` and `register-target` now print a concise diff showing
which entries were added (`+`), updated (`~`), or pruned (`-`). This makes CI
logs meaningful without any interactive prompts.
