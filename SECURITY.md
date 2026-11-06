# Security Policy

## Supported code

Security fixes target the latest code on the `main` branch. Older revisions are not maintained separately.

## Reporting vulnerabilities

Please **do not** post real credentials, memory files, private data, or detailed exploitation steps in public issues.

If GitHub private vulnerability reporting is enabled for this repository, use **Security → Report a vulnerability**. Otherwise, open a minimal issue asking the maintainer for a private reporting channel **without including sensitive details**.

These MCP servers execute locally with the permissions of their Python process. Their input validation and resource limits reduce risk but are not a complete operating-system sandbox, and secret detection is heuristic.
