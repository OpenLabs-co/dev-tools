<#
Kokoro Voice for Claude Code: uninstaller for Windows.
  & "$env:USERPROFILE\.claude\tts\uninstall.ps1"         remove everything (~120 MB of model files too)
  & "$env:USERPROFILE\.claude\tts\uninstall.ps1" -Keep   remove hooks and helpers, keep the kit folder
#>
param([switch]$Keep)

$ErrorActionPreference = 'Stop'
$Claude = Join-Path $env:USERPROFILE '.claude'
$Dest = Join-Path $Claude 'tts'
$Bin = Join-Path $Dest 'bin'
$Settings = Join-Path $Claude 'settings.json'

# Stop the hotkey helper and any speech in progress.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -like '*\.claude\tts\*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'kokoro-voice-hotkeys.vbs') -ErrorAction SilentlyContinue

# Remove only our hooks; keep a backup of settings.json.
if (Test-Path $Settings) {
    Copy-Item $Settings "$Settings.bak-kokoro-uninstall-$(Get-Date -Format yyyyMMdd-HHmmss)"
    $json = Get-Content $Settings -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($json.hooks) {
        foreach ($evt in @($json.hooks.PSObject.Properties.Name)) {
            $kept = @()
            foreach ($group in @($json.hooks.$evt)) {
                $group.hooks = @($group.hooks | Where-Object {
                    $blob = ("$($_.command) " + (@($_.args) -join ' ')) -replace '\\', '/'
                    $blob -notlike '*tts/speak.py*'
                })
                if ($group.hooks.Count -gt 0) { $kept += $group }
            }
            if ($kept.Count -gt 0) { $json.hooks.$evt = $kept } else { $json.hooks.PSObject.Properties.Remove($evt) }
        }
        if (@($json.hooks.PSObject.Properties).Count -eq 0) { $json.PSObject.Properties.Remove('hooks') }
    }
    $out = $json | ConvertTo-Json -Depth 32
    [IO.File]::WriteAllText($Settings, $out + "`n", (New-Object Text.UTF8Encoding $false))
    Write-Host "Removed the Kokoro hooks from $Settings (a backup is next to it)."
}

# PATH entry for tts / shh.
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
    $parts = $userPath -split ';' | Where-Object { $_ -and $_ -ne $Bin }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}
Remove-Item (Join-Path $Claude 'tts-on') -ErrorAction SilentlyContinue

if (-not $Keep) {
    Remove-Item $Dest -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Removed $Dest."
}
Write-Host 'Kokoro Voice is uninstalled.'
