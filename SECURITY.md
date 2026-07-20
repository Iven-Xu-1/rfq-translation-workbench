# Security Policy

## Supported version

Only the newest GitHub pre-release is supported during the alpha period.

## Reporting a vulnerability

Do not include API keys, customer files, translated output, local absolute paths, or other private data in a public issue. Use GitHub private vulnerability reporting when it is enabled for the repository.

## Security boundaries

- The application is a local Windows web service bound to `127.0.0.1` by default.
- The alpha release has no account system and must not be exposed directly to a LAN or the public Internet.
- API keys are stored with Windows DPAPI for the current user.
- Release assets never contain a user API key, project database, uploaded document, translated output, cache, or log.
- The online bootstrap downloads third-party dependencies over HTTPS and verifies the pinned CPython and pdf2zh source archives plus hash-locked PyPI artifacts.

Always verify release hashes before installation and keep Windows, the external model provider, LibreOffice, and installed Python dependencies updated through a reviewed release process.
