# Data handling

## Stored locally

Uploaded source files, translated files, previews, pump cards, reports, job state, caches, and application logs are stored below the current user's local application-data directory by default. They are not uploaded to a project-operated central server.

## Sent to the configured model provider

Text extracted from files selected for translation can be sent to the external model endpoint configured by the user. The exact provider, endpoint, retention policy, regional processing, and account terms are controlled by that provider and the user's account.

Do not process confidential material until you are authorized to send its extracted text to the chosen provider.

## API key

The API key is entered during local configuration and encrypted with Windows DPAPI for the current user. It must not be placed in source files, browser storage, command-line arguments, logs, manifests, SBOM files, screenshots, or support bundles.

## Deleting data

The uninstaller preserves local project data by default. A separate, explicit data-deletion choice and confirmation phrase are required to remove it. Back up any required projects first. Cached translations and logs can also contain derived business text and should be protected and deleted according to the same policy as source files.

## Diagnostics

Before sharing a diagnostic bundle, inspect it for filenames, project names, extracted text, paths, and provider information. Do not attach source documents or full logs to a public issue.
