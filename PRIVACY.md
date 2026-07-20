# Privacy and Data Handling

RFQ Translation Workbench runs locally on Windows. Uploaded files and generated project output are stored under the current user's local data directory unless that user changes the approved local path.

The program is not a cloud storage service. However, translation is not fully offline: text selected for translation is sent to the OpenAI-compatible endpoint configured by the user. The endpoint operator may retain or process that text under its own terms and privacy policy.

The release does not include analytics, advertising, a shared API key, or a central project database. It does not intentionally transmit project lists, local paths, logs, or parameter-card exports to the project maintainer.

Users are responsible for confirming that they are authorized to send a document's text to their selected model provider. Avoid confidential, regulated, personal, customer, or employer material unless the applicable policy permits that transfer.

API keys are stored using Windows DPAPI for the current Windows user. They are not written to the repository, release manifest, web page, or normal logs.
