# Security & Privacy (OWASP / GDPR posture)

How the Timesheet Intelligence Portal protects accounts and data, and the
controls mapped to the OWASP Top 10 and GDPR principles.

## Authentication & sessions
- **Password + mandatory 2FA for every role, including admins.** After a correct
  password, exactly one second factor is required, chosen per user by an admin —
  email **OTP** (6-digit, single-use, expiring, attempt- and resend-limited), an
  authenticator-app **TOTP** code, or a **CAPTCHA** challenge. There is no admin
  bypass.
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
- **Employee-portal login is rate-limited too.** `POST /portal/auth/login` is
  single-factor (password only), so the limit is its only brute-force defence. It
  uses its own bucket (`rl:portal-login`, keyed username + client IP, same
  `LOGIN_RATE_MAX` / `LOGIN_RATE_WINDOW_SECONDS` as internal login) so the two
  user populations behind one office IP can't exhaust each other's attempts.
  Over the limit → `429` before any database lookup.
- **Nothing about the account is revealed before the second factor.** While a
  login is still *pending* (password accepted, OTP / CAPTCHA / authenticator
  code not yet verified), `/auth/login` returns only the next-step challenge and
  the login token — no `user` object (name, email, role). The user details are
  returned only once the login is complete.

## Authorization (RBAC) — four roles
| Role | What it can do |
|------|----------------|
| `admin` | Everything: all reads and writes, user & AI-config admin, reminders, system health, extraction debug, **Auto Extract Start/Stop**, delete. |
| `user` | All business reads and writes (inbox, pipeline, upload, bulk upload, matcher, vault, chat), **including delete**. No admin pages, cannot start/stop Auto Extract. |
| `viewer` | **Read-only** everywhere, including downloading files. Every non-GET request is rejected with `403`. |
| `vault_matcher` | Restricted operator: read + create/edit on the **Employee Matcher** and **File Vault** only (upload, rename, move, new folders). **Cannot delete** vault files or folders. Every other business route — inbox, pipeline, upload, bulk upload, timesheets, dashboard, chat — returns `403`, even for GET. |

Who can delete File Vault content (`DELETE /files/file`, `DELETE /files/folder`):
`admin` and `user` only. `viewer` is stopped by the write guard and `vault_matcher`
by `require_full_access` on those two routes; the Files page also hides every
delete control for `vault_matcher`. The Employee Matcher has no delete endpoint
for any role (employees are deactivated via `PATCH /employee-matcher/{pk}/status`).

How it is enforced — at the **router level**, in `api/deps.py`:

| Dependency | Rule |
|------------|------|
| `require_user` | any authenticated user |
| `require_write` | blocks non-safe methods (POST/PUT/PATCH/DELETE) for `viewer` |
| `require_full_access` | blocks `vault_matcher` entirely (GET too), then `require_write` |
| `require_admin` | `admin` only |

Matcher and vault routers use `require_write`; inbox, timesheets, employees,
upload, bulk upload, pipeline and chat use `require_full_access`. Individual
routes can be tightened further — the Auto Extract Start/Stop endpoints
(`POST /inbox/auto-extract/start|stop`) take `require_admin`, while
`GET /inbox/auto-extract/status` stays readable by anyone with inbox access, and
the Auto Extract widget is hidden for non-admins. The system notice is open to every
role for reading and clicking OK (`GET /notice`, `POST /notice/ack` — a user can only
acknowledge as themselves); editing it and seeing who clicked OK are admin-only
(`PUT /notice`, `GET /notice/acks`). This is defence-in-depth: the
security boundary is the API, not the frontend. The employee portal uses a
separate token namespace and dependency tree (`api/portal_deps.py`).

## Untrusted input from emails
- **Attachment filenames are sanitised at the source.** A malformed or truncated
  MIME header (RFC 2047 encoded-words, RFC 2231 `filename*` continuations) can
  decode into a filename containing a NUL byte. Postgres `json`/`jsonb` accepts
  `\u0000` on write but cannot convert it back to text on any later read, which
  poisoned the stored extraction record. NULs are stripped in `eml_parser`,
  `file_processor` and `thread_extract` before they reach `extraction_meta`.
- Text and images are PII-scrubbed before every model call (see `docs/SYSTEM.md`),
  and everything a document or email says is treated as data, never instructions.

## Transport & headers (OWASP A05)
- Baseline security headers on every response: `X-Content-Type-Options`,
  `X-Frame-Options: DENY` (file/attachment preview routes use `SAMEORIGIN` so
  in-app PDF/image iframes work), `Referrer-Policy: no-referrer`,
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
| A01 Broken Access Control | Router-level RBAC (4 roles); admin-only routes incl. Auto Extract Start/Stop; viewer write-block; vault_matcher confined to Matcher + Vault with no delete |
| A02 Cryptographic Failures | bcrypt, Fernet-encrypted keys, prod secret guard, TLS |
| A03 Injection | SQLAlchemy parameterised queries; no string-built SQL |
| A04 Insecure Design | Mandatory 2FA, rate limits (internal **and** employee-portal login), fingerprint-bound login, no account details before the 2nd factor |
| A05 Security Misconfiguration | Security headers, scoped CORS, least-priv IAM/DB |
| A07 Auth Failures | 2FA, lockouts, rate-limited logins (internal + portal), single-use OTP, token revocation |
| A09 Logging Failures | No secrets/PII in logs; OTP only in non-prod |

## Operator checklist before production
- [ ] Change all `DEFAULT_ADMIN_*` and set a deliverable `DEFAULT_ADMIN_EMAIL`
      (admin needs 2FA email) — or set the admin to CAPTCHA mode.
- [ ] Set a strong `JWT_SECRET` (the app refuses to boot otherwise in prod).
- [ ] Real Redis (revocation + rate limits + cache need it) — container or ElastiCache.
- [ ] Private RDS + scoped S3 IAM (see `docs/AWS_SETUP.md`), TLS on.
- [ ] `EMAIL_PROVIDER=graph` configured so OTP emails actually deliver.
