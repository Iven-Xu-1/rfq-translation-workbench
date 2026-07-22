# Versioning

The project uses semantic versioning for public releases.

- `v0.1.0-alpha.1` is the first public pre-release; breaking changes remain possible.
- `v0.1.0-alpha.2` keeps the Alpha 1 runtime behavior and makes the complete public test suite portable on a clean Windows runner.
- A Git tag is created only after the source commit, checksums, SBOM, license, Notices, and release notes refer to the same reviewed asset.
- Rebuilt assets must use a new candidate identifier; do not silently replace an artifact with different bytes.
