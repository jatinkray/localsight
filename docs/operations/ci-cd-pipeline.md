# CI/CD Pipeline

LocalVision uses a multi-stage GitHub Actions pipeline that enforces quality,
security, and operational standards on every pull request and merge.

## Overview

The pipeline runs on every push to `main`, `develop`, or any `feat/**` branch, and
on every pull request. It is composed of nine jobs that can run in parallel, then
a final quality gate that aggregates the results and blocks merge on hard failures.

```
   ┌─────────────────────────────────────────────────────────────────┐
   │                       Git Push / PR                                │
   └─────────────────────────────┬─────────────────────────────────────┘
                                 │
   ┌─────────────────────────────┴─────────────────────────────┐
   │                  9-Job Quality Pipeline                       │
   │                                                                │
   │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
   │   │    lint     │  │  unit tests │  │ integration │          │
   │   │ (ruff+mypy) │  │   (SQLite)  │  │  (postgres) │          │
   │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘          │
   │          │                │                │                   │
   │   ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐          │
   │   │   pip-audit │  │  CodeQL     │  │  Semgrep    │          │
   │   │   + Safety  │  │   SAST      │  │   SAST      │          │
   │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘          │
   │          │                │                │                   │
   │   ┌──────┴────────────────────────────┐  │                   │
   │   │   Trivy container scan (SARIF)    │  │                   │
   │   └──────┬────────────────────────────┘  │                   │
   │          │                │                │                   │
   │   ┌──────┴────────────────────────────┐  │                   │
   │   │   Docker build + push (main only) │  │                   │
   │   └──────┬────────────────────────────┘  │                   │
   │          │                                │                   │
   │   ┌──────┴────────────────────────────────┴────────────┐    │
   │   │            Quality Gate (aggregator)                │    │
   │   │   Fails merge if lint or tests fail.                │    │
   │   └─────────────────────────────────────────────────────┘    │
   └─────────────────────────────────────────────────────────────────┘
```

## Job-by-job reference

### 1. `lint` — Static analysis

| Check | Tool | Purpose | Action on failure |
|-------|------|---------|-------------------|
| Linter | `ruff` (latest) | Style + import-order + common bug checks | Warning (continue-on-error) |
| Type check | `mypy` | Static type validation | Warning (continue-on-error) |

Both run with `continue-on-error: true` until the codebase is fully clean. After
that, the workflow can be tightened to fail the build on any lint issue.

### 2. `test` — Unit tests (SQLite)

Runs the full pytest suite against an isolated SQLite database. The conftest
(`conftest.py`) provisions a fresh DB, secret keys, and admin user per session.

| Aspect | Configuration |
|--------|---------------|
| Python | 3.12 |
| Database | SQLite (in-process, no service needed) |
| Coverage gate | ≥ 50% (raise as coverage grows) |
| Codecov upload | On PRs (optional, requires `CODECOV_TOKEN` secret) |
| Test artifacts | `coverage.xml`, `test_localvision.db`, `.pytest_cache/` |

The job installs `ffmpeg` system-wide because the recorder tests exercise the
segmentation logic. All 66 tests run in ~17 seconds.

### 3. `integration` — PostgreSQL integration

Same tests but against a real PostgreSQL + pgvector service. Uses
`psycopg[binary]==3.2.3`. Marked `continue-on-error: true` while the test
suite migrates from SQLite-only assertions.

| Aspect | Configuration |
|--------|---------------|
| Python | 3.12 |
| Database | PostgreSQL 16 + pgvector |
| Service | `pgvector/pgvector:pg16` (health-checked) |
| Mark | `not unit` (only DB-layer tests) |

### 4. `security-deps` — Dependency vulnerability audit

Two free-tier tools run in parallel and write JSON reports to artifacts:

| Tool | Database | Output |
|------|----------|--------|
| `pip-audit` | OSV / PyPI advisory feed | `pip-audit.json` |
| `safety` | pyup.io safety database | `safety-report.json` |

Both have `continue-on-error: true` so audit failures generate warnings rather
than block merges. Tighten this when you want to enforce zero high/critical CVEs.

### 5. `sast-codeql` — CodeQL static analysis

GitHub's CodeQL engine runs the `security-extended` query suite against the
Python codebase. Results appear in the **Security** tab of the repository.
Requires the `security-events: write` permission at the job level.

### 6. `sast-semgrep` — Semgrep community + security rules

Runs `semgrep scan` with four rule packs:
- `p/security-audit` — General security best practices
- `p/owasp-top-ten` — OWASP Top 10 detection patterns
- `p/python` — Python-specific anti-patterns
- `p/secrets` — Hardcoded credentials / API keys

Results uploaded as `semgrep-report.json` artifact (30-day retention).

### 7. `container-scan` — Trivy SARIF scan

Builds the Docker image and runs Trivy against it, writing SARIF to the GitHub
Security tab.

| Check | Severity threshold |
|-------|-------------------|
| OS package CVEs | CRITICAL, HIGH |
| Python package CVEs | CRITICAL, HIGH |
| Misconfigurations | All (warning only) |
| Embedded secrets | All (warning only) |

### 8. `docker` — Build + push to GHCR

Multi-platform build (`linux/amd64`, `linux/arm64`) with:
- GHA cache (in + out)
- Build args from workflow `env`
- SP-style metadata labels via `docker/metadata-action`
- Trivy SBOM (SPDX JSON) as a build artifact

Pushes to `ghcr.io/<org>/<repo>` with the following tags:
- `<branch>-<sha>` for every push
- `latest` on the default branch
- `vX.Y.Z` for semver tags

### 9. `quality-gate` — Aggregator

Runs after all other jobs. Fails the workflow if any of:
- `lint` failed
- `test` failed
- `integration` failed

Security scan failures (`security-deps`, `sast-*`, `container-scan`) are
warnings only — they don't block the merge. This lets the team iterate on
fixing security findings without blocking product work.

## Release workflow

A separate workflow (`.github/workflows/release.yml`) is triggered by version
tags (`v*`). It:

1. Creates a GitHub Release with auto-generated notes
2. Builds + pushes a semver-tagged Docker image
3. Generates a Trivy SPDX SBOM
4. Uploads the SBOM as a release asset

To release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

## Dependabot

`.github/dependabot.yml` is configured to:
- Open weekly PRs for `pip` (Python dependencies) and `github-actions`
- Open monthly PRs for the Docker base image
- Group minor/patch updates to reduce PR noise

Each PR is auto-labeled and runs the full CI pipeline.

## CODEOWNERS

`.github/CODEOWNERS` defines path-based code owners. When a PR touches a
sensitive file (e.g. `packages/security/`), GitHub automatically requests a
review from the owner.

## Free CVE / vulnerability sources used

All sources are free for public GitHub repositories. None require a paid
subscription.

| Source | Database | Coverage | Used by |
|--------|----------|----------|---------|
| **pip-audit** | OSV / PyPI | Python package CVEs | `security-deps` |
| **Safety** | pyup.io | Python package CVEs | `security-deps` |
| **Trivy** | GHSA / TSL / NVD | OS + Python package CVEs in container images | `container-scan`, `docker` SBOM |
| **CodeQL** | GitHub code scanning | SAST for Python | `sast-codeql` |
| **Semgrep** | Semgrep registry | SAST for Python / OWASP / secrets | `sast-semgrep` |

## Local equivalents

Developers can run each gate locally before pushing:

```bash
# Lint
ruff check . --target-version=py312

# Type check
mypy packages apps --ignore-missing-imports

# Unit tests (with coverage)
rm -f test_localvision.db
pytest tests/ -v --cov=packages --cov=apps --cov-fail-under=50

# Dependency audit
pip-audit

# Container scan
docker build -t localvision:test -f infrastructure/docker/Dockerfile .
trivy image --severity CRITICAL,HIGH localvision:test
```

## Adding a new quality gate

1. Add a new job to `.github/workflows/ci.yml` with the appropriate `runs-on`
   and any required `permissions` / `services`.
2. Add the job to the `quality-gate` `needs:` list and a new `env:` entry for
   the result.
3. Add a `if` check in the quality-gate shell script if the gate should block merges.
4. Add the local equivalent to `CONTRIBUTING.md`.
5. Update this file with a new section under "Job-by-job reference".

## Why this pipeline

LocalVision is a security/privacy product. The CI/CD pipeline enforces:

- **No insecure code**: CodeQL + Semgrep catch injection, crypto misuse, and
  auth bypass patterns.
- **No vulnerable dependencies**: pip-audit + Safety + Dependabot catch supply-chain
  issues within hours of disclosure.
- **No broken releases**: Multi-platform Docker build + Trivy scan ensures the
  shipped image works on both `amd64` (servers) and `arm64` (Jetson, RPi).
- **No silent failures**: Every gate produces a report artifact that's reviewed
  even when the build passes.
- **No platform lock-in**: 100% free-tier tools; no Snyk/GitHub Advanced Security
  dependency.
