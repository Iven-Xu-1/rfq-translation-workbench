[CmdletBinding()]
param(
    [string]$ConfigRoot = "",
    [Parameter(Mandatory = $true)][string]$ProviderBaseUrl,
    [Parameter(Mandatory = $true)][string]$ProviderModel,
    [Security.SecureString]$ApiKey
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

$defaults = Get-RFQDefaultPaths
if (-not $ConfigRoot) { $ConfigRoot = $defaults.ConfigRoot }
$ConfigRoot = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
$settings = Get-RFQSettings -ConfigRoot $ConfigRoot

$uri = $null
if (-not [Uri]::TryCreate($ProviderBaseUrl.Trim(), [UriKind]::Absolute, [ref]$uri)) {
    throw "ProviderBaseUrl must be an absolute URI."
}
if ($uri.Scheme -notin @("https", "http")) { throw "Only HTTP(S) model endpoints are supported." }
if ($uri.Scheme -eq "http" -and $uri.Host -notin @("127.0.0.1", "localhost", "::1")) {
    throw "Plain HTTP is permitted only for a loopback model endpoint."
}
if ([string]::IsNullOrWhiteSpace($ProviderModel)) { throw "ProviderModel cannot be empty." }
if ($null -eq $ApiKey) { $ApiKey = Read-Host "Enter your API Key (stored with Windows DPAPI for this user)" -AsSecureString }

$keyPath = Get-RFQKeyPath -Settings $settings
Protect-RFQSecret -SecureValue $ApiKey -Destination $keyPath

$updated = [ordered]@{}
foreach ($property in $settings.PSObject.Properties) { $updated[$property.Name] = $property.Value }
$updated.provider_base_url = $uri.AbsoluteUri.TrimEnd('/')
$updated.provider_model = $ProviderModel.Trim()
$updated.api_key_storage = "dpapi_current_user"
$updated.api_key_configured = $true
$updated.updated_at = (Get-Date).ToUniversalTime().ToString("o")
Write-RFQJson -Path (Join-Path $ConfigRoot "settings.json") -Value $updated

[pscustomobject]@{
    Status = "configured"
    ProviderBaseUrl = $updated.provider_base_url
    ProviderModel = $updated.provider_model
    ApiKeyConfigured = $true
    ApiKeyStorage = "dpapi_current_user"
    ApiKeyValueExposed = $false
}
