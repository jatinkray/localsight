# Threat Model (STRIDE-aligned)

For each threat: attack → impact → likelihood → mitigation → detection → recovery.

| # | Threat | Attack | Impact | Likelihood | Mitigation | Detection | Recovery |
|---|--------|--------|--------|-----------|------------|-----------|----------|
| 1 | Compromised camera | Malicious RTP/RTSP payload | RCE via decoder | Med | Run FFmpeg non-root, seccomp/AppArmor, resource limits, least privilege, decoders isolated | decoder crash alerts, anomaly logs | restart worker; quarantine camera |
| 2 | Compromised NVR | Forged stream / creds | False footage | Low | Encrypt creds at rest, separate mgmt VLAN, egress allowlist | auth failures, unexpected source IP | rotate creds; revoke |
| 3 | Malicious authenticated user | Enroll false identity, delete evidence | False IDs / data loss | Med | RBAC server-side; `person:delete` limited; audit all | audit log review; alert on delete/export | restore from backup; revoke user |
| 4 | Stolen admin creds | Full control | Total compromise | Med | Argon2id, MFA, lockout, refresh rotation, audit | impossible-travel/IP anomaly | revoke tokens; rotate KEK |
| 5 | Malicious video file | Crafted frame | Decoder exploit | Low | Same as #1; validate container/codec; size caps | decode errors | re-ingest from source |
| 6 | Malicious RTSP URL (SSRF) | Point platform at internal svc | Internal pivot | Med | SSRF egress guard (block loopback/link-local/metadata) + network egress policy | rejected-URL audit entries | block; review allowlist |
| 7 | SSRF to cloud metadata | `169.254.169.254` | Cloud cred theft | Low | Block link-local; no IMDS by default | guard blocks | N/A |
| 8 | RCE via media processing | Shell/deserialization | Host compromise | Low | Structured argv (no shell), no `eval`, sandbox containers | IDS/SIEM | rebuild from image |
| 9 | Malicious model | Trojan weights | Wrong/biased ID | Low | Model registry + SHA-256 verification before load | hash mismatch on load | re-stage approved model |
| 10 | Database theft | Dump/backup exfil | PII + embeddings | Med | Encryption at rest, least-priv DB user, network isolation | anomalous query volume | rotate KEK; restore |
| 11 | Embedding theft | Steal vectors | Re-identification | Med | Encrypted embeddings, access control, audit | unusual read patterns | rotate KEK; revoke |
| 12 | Video theft | API/export abuse | Privacy breach | Med | RBAC `video:view/export`, signed expiring URLs, audit | export audit; rate limits | revoke; rotate |
| 13 | API abuse / DoS | Flood endpoints | Outage | High | Rate limiting, pagination limits, size/timeouts, WAF | metrics + alerts | scale; block IPs |
| 14 | Storage exhaustion | Unlimited writes | Data loss | Med | Retention policy, watermarks, alert at threshold | storage metric | apply retention; add capacity |
| 15 | GPU exhaustion | Many streams | Degraded detect | Med | Bounded inference scheduler, CPU fallback, frame drop | GPU metric | throttle; degrade gracefully |
| 16 | Supply-chain compromise | Bad dependency | Backdoor | Low | Pinned deps, SBOM, vuln scanning, signed images | CVE alerts | pin known-good |
| 17 | Insider threat | Abuse access | Leak/alter | Low | RBAC + approvals + audit + separation of duties | audit analytics | investigate; revoke |

## Cross-cutting

- **Least privilege**: API/worker run as non-root; DB user scoped; no host mounts beyond storage.
- **Defense in depth**: SSRF (app) + network egress (infra); headers + CSP + HSTS.
- **Assume hostile camera network**: it is never trusted, never exposed to the internet.
- **Privacy**: biometric recognition off by default; no automatic identity creation;
  establish lawful basis before enabling.
