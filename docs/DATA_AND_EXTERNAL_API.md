# Data and external model boundary

The web interface, project store, parsing, PDF rendering/OCR, Office reading, result previews, and exports run on the user's Windows computer.

Translation requires an external OpenAI-compatible model service selected by the user. Text extracted from selected files is sent to that endpoint. The provider may process or retain it under its own service terms. The project maintainer does not provide a shared key or proxy.

The software does not intentionally upload raw project folders, API keys, local paths, logs, parameter-card exports, or the local database to the project maintainer. A translated file can still contain sensitive source content, so users remain responsible for document classification, provider approval, retention rules, and human review.

The Windows alpha listens on loopback only. It is not designed as an unauthenticated LAN or Internet service.
