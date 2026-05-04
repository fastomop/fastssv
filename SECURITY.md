# Security policy

Thanks for taking the time to report a security issue responsibly.

## Reporting a vulnerability

Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) for this repo:

> **[Report a vulnerability →](https://github.com/fastomop/fastSSV/security/advisories/new)**

That sends the report to the maintainers privately, opens a tracked GitHub Security Advisory, and lets us coordinate a fix and disclosure timeline with you. **Please do not file a public GitHub issue for security bugs.**

If the link above shows "this repository hasn't enabled private vulnerability reporting" or you cannot use a GitHub account, open a public issue with a stub title like *"security report — please contact"* and **no technical details**, asking a maintainer to reach out. We will move to a private channel from there.

When you report, please include — wherever applicable:

- The affected component (CLI, JSON API, htmx web UI, Docker image, a specific rule).
- The version (`fastssv --version`) and Python version.
- A minimal reproducer: SQL input, HTTP request, or steps to reach the vulnerable code path.
- The impact you observed (RCE, DoS, info disclosure, integrity, supply-chain) and any preconditions (network access, auth, specific configuration).

## Supported versions

FastSSV is pre-1.0 (`0.x.y`). The public Python API (`validate_sql_structured`, `validate_sql`, `RuleViolation`, `Severity`, the registry helpers) and the `rule_id` format `<category>.<rule_name>` are stable, but the rule set, violation wording, and individual severities are still being calibrated. Only the **latest minor release** receives security fixes.

| Version | Supported |
| --- | --- |
| Latest `0.x.y` | ✅ |
| Older `0.x.y` | ❌ — upgrade to the latest minor |
| Pre-release (`.devN`, `rcN`) | ❌ |

Pin to a minor in your dependencies (`fastssv>=0.x,<0.(x+1)`) and follow [CHANGELOG.md](CHANGELOG.md) for upgrade impact.

## Response targets

| Stage | Target |
| --- | --- |
| Acknowledgement of the report | within 7 days |
| Initial assessment and severity call | within 21 days |
| Fix released, or coordinated disclosure plan agreed | within 90 days |

These are targets, not guarantees — FastSSV is a small-team OSS project and complex issues may take longer. We will keep you in the loop and update the advisory as work progresses.

## Scope

**In scope** — issues in code that this repository ships:

- The CLI and rule engine in `src/fastssv/` (parser misuse, denial of service via crafted SQL, path traversal in CLI options, etc.).
- The optional FastAPI service in `src/fastssv/api/` — HTTP request handling, body-size and parse-timeout enforcement, rate limiting and CORS configuration, security-header defaults, info disclosure in error responses, template injection in the htmx UI.
- The Docker image under `deploy/` — process privileges, baked secrets, exposed surfaces.
- Build, packaging, and release infrastructure under `.github/workflows/` — supply-chain risks in `publish.yml` and friends.

**Out of scope:**

- Vulnerabilities in third-party dependencies (`sqlglot`, `fastapi`, `pydantic`, `gunicorn`, `slowapi`, …). Please report those upstream. We will of course bump pins and ship our own advisory when an upstream issue affects FastSSV users.
- Behaviour of *user-supplied* SQL or its query results. FastSSV is a static validator: it parses SQL into an AST without executing it against any database. SQL that is "wrong" but does not exploit the validator itself is not a security issue.
- Operational issues in deployments you run (e.g. a permissive reverse proxy in front of `fastssv serve`, secrets leaking through your own env vars or logs). Configuration hardening guidance lives in [`docs/api.md`](docs/api.md).
- Social engineering of maintainers, physical attacks, or anything outside what code in this repo controls.

## Disclosure

We coordinate disclosure with the reporter:

- Default is a **fixed-then-published** advisory through GitHub Security Advisories, with a CVE assigned where appropriate.
- Reporters are credited by name, alias, or anonymously — tell us your preference in the report.
- Embargoed disclosure for downstream packagers (Linux distros, container registries) can be arranged on request.

## Thanks

Security work is unpaid and often invisible. We appreciate everyone who takes the time to report responsibly rather than dropping a 0-day on social media.
