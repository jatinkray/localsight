# Security Policy

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue in LocalVision,
please report it responsibly.

**Please DO NOT file a public GitHub issue for security vulnerabilities.**

Instead, report privately via one of:

1. **GitHub Private Vulnerability Reporting** (preferred)
   - Navigate to the Security tab → " Advisories" → "Report a vulnerability"
   - This creates a private disclosure tracked in the GitHub Security Advisories database

2. **Email** (if GitHub is unavailable)
   - Email the repository maintainers directly
   - Use a subject line: `[LocalVision Security]`

## Scope

In-scope: LocalVision codebase, its Python dependencies, Docker image, and CI/CD pipeline.

Out-of-scope: Third-party cameras/NVRs, network infrastructure, operating system hardening.

## Disclosure Timeline

| Phase | Timeline | Description |
|-------|----------|-------------|
| Initial response | 24–48h | Acknowledge report, assign severity |
| Assessment | 7 days | Confirm vulnerability, assign CVE (if applicable) |
| Fix development | 30–90 days | Issue fix (patched version or mitigation) |
| Public disclosure | After fix | Coordinated disclosure via GitHub Security Advisories |

## Security Scanner Findings

LocalVision uses automated security scanning in CI:

| Tool | Checks | Frequency |
|------|--------|-----------|
| pip-audit | PyPI/OSV dependency vulnerabilities | Every PR |
| Safety | pyup.io vulnerability database | Every PR |
| Trivy | Container image CVEs (OS + Python packages) | Every PR + main push |
| CodeQL | SAST for Python security bugs | Every PR |
| Semgrep | SAST for injection, auth, crypto misuse | Every PR |

Critical/high CVE findings block main branch pushes. Medium/low findings generate warnings.

## Secure Development Expectations

Contributors must:
- Run `ruff check .` before submitting PRs
- Never commit secrets, credentials, or API keys
- Use `python scripts/gen_env.py` for secret generation
- Ensure any new dependencies are reviewed for supply-chain risks
