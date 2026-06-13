# Dependency Management — RAG Pipeline
## How to Capture, Reproduce, and Maintain Python Dependencies

---

## The Core Problem

Your Lambda package has two kinds of dependencies:
- **Runtime dependencies** — installed in `package/` folder, zipped with Lambda code
- **Dev/local dependencies** — installed in your local Python env for running scripts like `langsmith_eval.py`, `create_index.py`

Both need to be captured separately.

---

## Step 1 — Capture What's Currently Installed

### Your Local Environment (scripts, eval, dev tools)

```powershell
# Activate whatever env you're using (venv, conda, or global), then:
pip freeze > requirements-local.txt
```

This captures every package + exact version currently installed locally.

### What's in the Lambda package/ folder

The `package/` folder contains Linux arm64 binaries installed specifically for Lambda. To see what's in there:

```powershell
# List all dist-info folders inside package/ — each is one installed package
Get-ChildItem .\package\ -Filter "*.dist-info" -Directory | 
  ForEach-Object { $_.Name -replace '-\d.*', '' + " " + ($_.Name -split '-')[1] }
```

Or more cleanly — pip can introspect the folder directly:

```powershell
pip list --path .\package\ --format=freeze > requirements-lambda.txt
```

This gives you a clean `package==version` list of exactly what's in the Lambda zip.

---

## Step 2 — Understand the Two requirements Files You Need

### `requirements-lambda.txt` — what goes in the Lambda zip
```
langchain==0.3.x
langchain-aws==0.2.x
langchain-community==0.3.x
langgraph==0.2.x
langsmith==0.1.x
opensearch-py==2.x.x
boto3==1.x.x
numpy==1.x.x
pypdf==4.x.x
cohere==5.x.x
```
These are installed with the Linux arm64 flag and must match what's in `package/`.

### `requirements-dev.txt` — what you install locally to run scripts
```
# Everything in requirements-lambda.txt, PLUS:
boto3==1.x.x        # often already installed via AWS CLI
awscli==2.x.x
pytest==8.x.x
python-dotenv==1.x.x
```

---

## Step 3 — Generate Both Files Right Now

Run these in your project root (where `package/` lives):

```powershell
# 1. Capture lambda package contents
pip list --path .\package\ --format=freeze > requirements-lambda.txt

# 2. Capture your full local environment
pip freeze > requirements-dev.txt

# 3. Verify they look reasonable
Get-Content requirements-lambda.txt
```

Commit both files to your repo immediately:
```powershell
git add requirements-lambda.txt requirements-dev.txt
git commit -m "chore: capture dependency snapshots for reproducibility"
git push
```

---

## Step 4 — How to Reproduce Later (Full Reinstall)

### Reinstall Lambda Dependencies (the arm64 Linux build)

```powershell
# Clean the package folder first
Remove-Item -Recurse -Force .\package\
New-Item -ItemType Directory -Name package

# Reinstall from the frozen requirements file
pip install -r requirements-lambda.txt `
  --target .\package\ `
  --platform manylinux2014_aarch64 `
  --implementation cp `
  --python-version 3.11 `
  --only-binary=:all: `
  --upgrade
```

⚠️ The `--platform manylinux2014_aarch64` flag is critical — it installs Linux arm64 binaries, not Windows binaries. Without it, the Lambda will fail with import errors even if the zip looks fine.

### Reinstall Local Dev Environment

```powershell
# Create a fresh virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install from frozen requirements
pip install -r requirements-dev.txt
```

---

## Step 5 — Ongoing Maintenance Pattern

Whenever you `pip install` a new package:

```powershell
# If it's a Lambda dependency (goes in package/):
pip install <new-package> --target .\package\ --platform manylinux2014_aarch64 --implementation cp --python-version 3.11 --only-binary=:all:
pip list --path .\package\ --format=freeze > requirements-lambda.txt  # update immediately

# If it's a dev/local dependency:
pip install <new-package>
pip freeze > requirements-dev.txt  # update immediately
```

Update the requirements file immediately after installing — don't let it drift.

---

## Handy Commands Reference

```powershell
# See what's in your Lambda package folder
pip list --path .\package\

# See what's installed locally
pip list

# Find a specific package version
pip show langchain

# Check if a package is in the Lambda package folder
pip show --path .\package\ langchain

# See dependency tree (install pipdeptree first)
pip install pipdeptree
pipdeptree --path .\package\

# Find what version of Python you're using
python --version

# Find if there are any version conflicts
pip check
```

---

## What to Commit to Your Repo

```
rag-pipeline/
├── requirements-lambda.txt    ← Lambda package deps (arm64 Linux builds)
├── requirements-dev.txt       ← Local dev + script deps
├── package/                   ← Add to .gitignore (binary files, large)
└── .gitignore
```

`.gitignore` entry:
```
package/
*.zip
__pycache__/
.venv/
```

The `package/` folder itself should NOT be committed — it's binary files that can be 200MB+. The `requirements-lambda.txt` file is what lets you recreate it.

---

## Quick Sanity Check — Is Your requirements-lambda.txt Accurate?

```powershell
# Compare what's in package/ vs what's in requirements-lambda.txt
$inPackage = (pip list --path .\package\ --format=freeze) | Sort-Object
$inFile = (Get-Content requirements-lambda.txt) | Sort-Object
Compare-Object $inPackage $inFile
# Empty output = they match. Any differences = update the requirements file.
```
