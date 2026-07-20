# Troubleshooting

## The page does not open

Run the installed status script and confirm that one listener exists on `127.0.0.1:8008`. Start the service again with the desktop shortcut. Do not change the host to `0.0.0.0`.

## Python is missing

Install Python 3.12 for the current user with Microsoft winget, or rerun the bootstrap with `-InstallPythonWithWinget`.

## Dependency download fails

Confirm that HTTPS access to python.org, files.pythonhosted.org/PyPI, and GitHub codeload is allowed. The installer refuses an archive or wheel whose hash is not in the reviewed lock.

## PDF output is partial

Scanned, mixed, or vector/image PDFs may use local OCR and layout reconstruction. Low-confidence OCR is reported as partial and requires human review. Never treat an automatically translated document as an approved engineering deliverable without review.

## DOC or XLS is skipped

Install LibreOffice and ensure its command-line executable is discoverable. DOCX, XLSX, and XLSM do not require LibreOffice conversion.

## Translation fails

Verify the provider URL, model, account balance, network policy, and API key. Do not paste a key into an issue, log, screenshot, or command line.

## Reset or uninstall

Use the provided stop, rollback, status, and uninstall scripts. Do not manually delete the data directory. See `UNINSTALL_WINDOWS.md`.
