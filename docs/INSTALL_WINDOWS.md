# Windows installation

## Requirements

- Windows 10 or Windows 11 x64.
- A current Windows user that can write to its own LocalAppData and Desktop.
- Internet access to python.org, pypi.org/files.pythonhosted.org, and GitHub codeload during first installation.
- Python 3.12.10 is reused when present in the current-user installation location; otherwise the bootstrap downloads the signed x64 installer directly from python.org and verifies its fixed SHA-256.
- Optional LibreOffice for `.doc` and `.xls` conversion.
- Your own OpenAI-compatible API endpoint, model name, and API key.

## Install

1. Download the Windows online-bootstrap ZIP and `SHA256SUMS.txt` from the same GitHub pre-release.
2. Calculate the ZIP SHA-256 in PowerShell:

   ```powershell
   Get-FileHash .\rfq-translation-workbench-v0.1.0-alpha.2-windows-online-bootstrap.zip -Algorithm SHA256
   ```

3. Compare the value exactly with `SHA256SUMS.txt`.
4. Extract the ZIP to a normal local folder. Do not run it from a network share, mapped network drive, OneDrive placeholder, URI, or device path.
5. Run `Install.cmd`.
6. Read the download and third-party license summary. The installer requires `-AcceptThirdPartyDownloads` before it performs downloads. `Install.cmd` presents this gate; an advanced user can run the PowerShell installer directly with that switch.
8. After installation, configure your own provider without putting the key on the command line:

   ```powershell
   & "$env:LOCALAPPDATA\Programs\RFQTranslationWorkbench\ops\Configure-RFQWorkbench.ps1" `
     -ProviderBaseUrl "https://your-provider.example/v1" `
     -ProviderModel "your-model-name"
   ```

   The script prompts for the key as a Windows secure string and stores it with DPAPI.

9. Start the tool from the created desktop shortcut or run `Start-RFQWorkbench.ps1`. Open `http://127.0.0.1:8008`.

## Installation downloads

The GitHub ZIP contains project source, scripts, documentation, a template, a hash-locked requirements file, Notices, and the SBOM. It does not contain Python, wheels, OCR/ONNX models, fonts, PDFium, LibreOffice, or a prebuilt runtime.

During installation the bootstrap downloads:

- CPython 3.12.10 x64 current-user installer from python.org and verifies its fixed SHA-256.
- Exact-version Python packages from PyPI.
- The reviewed pdf2zh-next source commit from GitHub codeload and verifies its fixed SHA-256 before building a local wheel.

The resulting runtime is created on the user's computer and is not a GitHub release asset.

## Safety defaults

The installer uses the current-user profile, binds only to `127.0.0.1`, configures one worker, does not enable autostart, and does not alter Windows Firewall or network settings.
