# Security & Privacy (OWASP / GDPR posture)

How the Timesheet Intelligence Portal protects accounts and data, and the
controls mapped to the OWASP Top 10 and GDPR principles.

## Authentication & sessions
- **Password + mandatory 2FA for every role, including admins.** After a correct
  password, a second factor is required — email **OTP** (6-digit, single-use,
  expiring, attempt- and resend-limited) or a **CAPTCHA** challenge. There is no
  admin bypass.
- **Passwords** are hashed with **bcrypt** (per-user salt); never stored or logged
  in plaintext. New/changed passwords must be ≥ 8 characters.
- **JWT access tokens** are short-lived, carry the role + a unique `jti`, and are
  signed with `JWT_SECRET`. The app **refuses to start in production** with a
  weak/default secret.
- **Server-side logout / revocation:** logout denylists the token's `jti` in
  Redis until it would expire, so a leaked token can't be reused after sign-out.
- **Login hardening:** per-username+IP sliding-window **rate limiting**,
  short-lived login token bound to a **device fingerprint**, constant-time code
  comparison, no username enumeration, OTP codes only surfaced in non-prod.

## Authorization (RBAC) — three roles
| Role | Read | Write (create/update/delete) | Admin (users, AI config) |
|------|------|------------------------------|--------------------------|
| `admin`  | ✅ | ✅ | ✅ |
| `user`   | ✅ | ✅ | ❌ |
| `viewer` | ✅ | ❌ | ❌ |

Enforced at the **router level** (`require_write`): viewers are blocked on any
non-safe HTTP method (POST/PUT/PATCH/DELETE) → `403`, regardless of which UI
button they reach. Admin routes additionally require the admin role. This is
defence-in-depth: the security boundary is the API, not the frontend.

## Transport & headers (OWASP A05)
- Baseline security headers on every response: `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy`, `Permissions-Policy`, and **HSTS** in production.
- **CORS** is restricted to explicit origins (never `*`) with a fixed method and
  header allow-list.
- TLS to RDS enforced (`?ssl=require`); see `docs/AWS_SETUP.md`.

## Secrets & data at rest (OWASP A02)
- AI provider API keys in `app_config` are **encrypted at rest** (Fernet, key
  derived from the app secret).
- `.env` is git-ignored; production secrets belong in env vars / AWS Secrets
  Manager. Least-privilege DB user and S3 IAM policy (see `docs/AWS_SETUP.md`).

## GDPR alignment
- **Data minimisation:** only the fields needed to match and file timesheets are
  stored; OTP lifecycle state lives in Redis (ephemeral), not the DB.
- **Right to erasure:** an admin can delete a user (`DELETE /admin/users/{id}`);
  timesheet/pipeline records and their S3 files can be deleted via the app.
- **Confidentiality:** emails are masked in user-facing messages; OTP codes are
  never returned in production; no PII in access logs.
- **Storage location & retention:** relational data in RDS, files in S3, raw
  retry copies auto-pruned once a file succeeds/resolves (see
  `docs/DATA_STORAGE.md`). Pick the AWS region to meet data-residency needs.

## OWASP Top 10 quick map
| Risk | Control |
|------|---------|
| A01 Broken Access Control | Router-level RBAC; admin-only routes; viewer write-block |
| A02 Cryptographic Failures | bcrypt, Fernet-encrypted keys, prod secret guard, TLS |
| A03 Injection | SQLAlchemy parameterised queries; no string-built SQL |
| A04 Insecure Design | Mandatory 2FA, rate limits, fingerprint-bound login |
| A05 Security Misconfiguration | Security headers, scoped CORS, least-priv IAM/DB |
| A07 Auth Failures | 2FA, lockouts, single-use OTP, token revocation |
| A09 Logging Failures | No secrets/PII in logs; OTP only in non-prod |

## Operator checklist before production
- [ ] Change all `DEFAULT_ADMIN_*` and set a deliverable `DEFAULT_ADMIN_EMAIL`
      (admin needs 2FA email) — or set the admin to CAPTCHA mode.
- [ ] Set a strong `JWT_SECRET` (the app refuses to boot otherwise in prod).
- [ ] Real Redis (revocation + rate limits + cache need it) — container or ElastiCache.
- [ ] Private RDS + scoped S3 IAM (see `docs/AWS_SETUP.md`), TLS on.
- [ ] `EMAIL_PROVIDER=graph` configured so OTP emails actually deliver.
