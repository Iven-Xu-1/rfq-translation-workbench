# v0.1.0-alpha.1

This is the first public alpha of RFQ Translation Workbench.

The Windows asset is an online bootstrap ZIP, not an offline runtime. It installs only for the current Windows user, downloads pinned dependencies, binds the local page to `127.0.0.1:8008`, and requires the user to configure an external model provider and API key.

Known limitations:

- First installation downloads a large Python dependency set and can take significant time and disk space.
- PDF layout translation and OCR can be slow.
- Complex Office objects and legacy DOC/XLS conversion require review and may be partial.
- Pump parameter extraction is a review aid, not an engineering decision or pump selection.
- No authentication or safe LAN/public exposure is included.
- The alpha has not been validated on every Windows edition or endpoint-security product.
