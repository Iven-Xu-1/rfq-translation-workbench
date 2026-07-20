# Windows uninstall and data retention

Run the installed `Uninstall-RFQWorkbench.ps1` script from the current user's install directory.

The default uninstall removes application files and shortcuts but preserves project data and configuration. Data deletion is never implicit. Removing data requires the script's explicit `-RemoveData` option, the expected data root, a verified local backup, and the fixed confirmation phrase required by the installer contract.

Before uninstalling, stop the service and back up any projects you want to keep. Do not use a network share as an install, data, restore, or backup target.
