# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- CodeQL analysis for Python, JavaScript/TypeScript and GitHub Actions on pushes and pull requests to `main` and on a weekly schedule.
- Dependabot version updates for pip (service and Python SDK), npm (TypeScript SDK), Docker and GitHub Actions, with minor and patch updates grouped per ecosystem.
- README screenshots: an evaluation session against a local API and the API reference.
- Roadmap items tracked as GitHub issues under the v0.2.0 milestone.

### Changed

- CI workflow runs with a read-only `GITHUB_TOKEN`.
- Test counts in the README now match the suite (88 service tests, 94 Python tests in total).

## [0.1.0] - 2026-10-25

First public release of feature-flags-service.

### Added

- Projects with environments (dev / staging / prod by default), hashed server and client SDK keys with revocation and last-used tracking
- Flags of kind `boolean`, `string`, `number` or `json` with named variations; per-environment config (enabled, individual targets, rules, fallthrough, off variation) so prod and staging differ without copying flags
- Targeting clauses: `eq`, `neq`, `in`, `contains`, `starts_with`, `ends_with`, `regex`, numeric `gt/gte/lt/lte`, `semver_eq/gt/gte/lt/lte`, `exists`, `in_segment`, each negatable; list-valued attributes match on any element
- Percentage rollouts with sticky bucketing (SHA-256 of flag + salt + user key, optional `bucket_by` attribute); verified to stay within 2% of the configured split over 100,000 keys
- Segments (included / excluded keys + rules) shared across a project and referenced from rules; deletion is refused while a flag uses them
- Kill switch (`/kill`, `/restore`) that serves the off variation without losing the rules; scheduled changes applied by a background sweep or on demand
- `POST /v1/evaluate` for one or many flags with a reason (`OFF`, `TARGET_MATCH`, `RULE_MATCH` + rule id, `FALLTHROUGH` + rollout, `ERROR`); usage counters per flag, environment, variation and reason
- `GET /v1/sdk/flags` bulk payload with strong ETags and `304 Not Modified`; server keys get full targeting for local evaluation, client keys get values evaluated for their context so rules never reach a browser
- `GET /v1/sdk/stream` Server-Sent Events with coalesced `put` events and keep-alive comments
- Audit log (actor, action, before/after) for every change; HMAC-signed flag-change webhooks

[Unreleased]: https://github.com/AkibUllah/feature-flags-service/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AkibUllah/feature-flags-service/releases/tag/v0.1.0
