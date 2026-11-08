<#
Kokoro Voice for Claude Code: installer for Windows.

  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice/install.ps1))) -Hotkeys

Options:
  -Hotkeys    start the hotkey helper (Ctrl+Alt+T/S/N/R/V) now and at every sign-in
  -VoiceOff   install, but leave the voice switched off (turn it on with: tts on)
  -NoTest     skip the spoken test line at the end

Safe to re-run. It backs up %USERPROFILE%\.claude\settings.json and never removes
your other hooks. Everything lands in %USERPROFILE%\.claude\tts.
Uninstall: & "$env:USERPROFILE\.claude\tts\uninstall.ps1"
#>
param(
    [switch]$Hotkeys,
    [switch]$VoiceOff,
    [switch]$NoTest
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is ~10x slower with the progress bar

$Raw = if ($env:KOKORO_VOICE_RAW) { $env:KOKORO_VOICE_RAW } else { 'https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice' }
$ModelUrl = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0'
$Models = @{
    'kokoro-v1.0.int8.onnx' = '6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb'
    'voices-v1.0.bin'       = 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d'
}
$KokoroOnnxVersion = '0.6.1'

$Claude = Join-Path $env:USERPROFILE '.claude'
$Dest = Join-Path $Claude 'tts'
$Bin = Join-Path $Dest 'bin'
$Settings = Join-Path $Claude 'settings.json'
$Startup = [Environment]::GetFolderPath('Startup')
$StartupFile = Join-Path $Startup 'kokoro-voice-hotkeys.vbs'

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "warning: $msg" -ForegroundColor Yellow }
function Die($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

# ── 1. Preflight ─────────────────────────────────────────────────────────────
if ($env:OS -ne 'Windows_NT') { Die 'This installer is for Windows. On macOS use install.sh.' }

# Find Python 3.10+. Skips the Microsoft Store "python" stub, which prints nothing useful.
$Py = $null
$candidates = @(
    @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('py', '-3.10'), @('py', '-3'),
    @('python'), @('python3')
)
foreach ($c in $candidates) {
    if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
    try {
        $ok = & $c[0] @($c[1..($c.Count)] | Where-Object { $_ }) -c 'import sys; print(sys.version_info >= (3, 10))' 2>$null
        if ($ok -eq 'True') { $Py = $c; break }
    } catch { }
}
if (-not $Py) { Die 'Python 3.10 or later is required. Install it with: winget install Python.Python.3.12' }
$PyArgs = @($Py[1..($Py.Count)] | Where-Object { $_ })
Say ("Using " + (& $Py[0] @PyArgs --version))

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Warn "Claude Code ('claude') is not on your PATH. The hooks will still be installed."
}

New-Item -ItemType Directory -Force -Path $Dest, (Join-Path $Dest 'queue'), $Bin | Out-Null

# ── 2. Kit files (from a local checkout when run from one, else from GitHub) ─
$Here = if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'speak.py'))) { $PSScriptRoot } else { $null }
Say "Installing kit files into $Dest"
foreach ($f in @('speak.py', 'ttsd_win.py', 'uninstall.ps1')) {
    $target = Join-Path $Dest $f
    if ($Here) { Copy-Item (Join-Path $Here $f) $target -Force }
    else { Invoke-WebRequest -UseBasicParsing -Uri "$Raw/$f" -OutFile $target }
}

# ── 3. Python environment + Kokoro ───────────────────────────────────────────
$VenvPy = Join-Path $Dest 'venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    Say 'Creating a Python environment'
    & $Py[0] @PyArgs -m venv (Join-Path $Dest 'venv')
    if ($LASTEXITCODE -ne 0) { Die 'Could not create the Python environment.' }
}
Say "Installing kokoro-onnx $KokoroOnnxVersion (this takes a minute the first time)"
& $VenvPy -m pip install -q --disable-pip-version-check --upgrade pip
& $VenvPy -m pip install -q --disable-pip-version-check "kokoro-onnx==$KokoroOnnxVersion" soundfile
if ($LASTEXITCODE -ne 0) { Die 'pip could not install kokoro-onnx. 64-bit Python is required.' }

foreach ($name in $Models.Keys) {
    $path = Join-Path $Dest $name
    $want = $Models[$name]
    if ((Test-Path $path) -and ((Get-FileHash $path -Algorithm SHA256).Hash.ToLower() -eq $want)) { continue }
    Say "Downloading $name"
    Invoke-WebRequest -UseBasicParsing -Uri "$ModelUrl/$name" -OutFile "$path.part"
    if ((Get-FileHash "$path.part" -Algorithm SHA256).Hash.ToLower() -ne $want) {
        Remove-Item "$path.part" -Force
        Die "Checksum mismatch for $name. Try again, or report it on GitHub."
    }
    Move-Item "$path.part" $path -Force
}

# ── 4. Claude Code hooks (merged, never replaced) ────────────────────────────
# Exec form (command + args): no shell runs, so it works the same whether Claude
# Code uses Git Bash or PowerShell, and no "~" has to expand. -X utf8 because
# hook input is UTF-8.
Say "Adding the Claude Code hooks to $Settings"
$merge = @'
import json, os, shutil, sys, time
path, py, script = sys.argv[1], sys.argv[2], sys.argv[3]
settings = {}
if os.path.exists(path):
    text = open(path, encoding="utf-8-sig").read().strip()
    settings = json.loads(text) if text else {}
    shutil.copy2(path, f"{path}.bak-kokoro-{time.strftime('%Y%m%d-%H%M%S')}")
wanted = [("Stop", None, "hook", 10), ("UserPromptSubmit", None, "prompt", 5), ("PreToolUse", "ExitPlanMode", "plan", 10)]
hooks = settings.setdefault("hooks", {})
for event, matcher, mode, timeout in wanted:
    groups = hooks.setdefault(event, [])
    def ours(h):
        blob = (h.get("command", "") + " " + " ".join(h.get("args", []))).replace("\\", "/")
        return "tts/speak.py" in blob and blob.rstrip().endswith(" " + mode)
    if any(ours(h) for g in groups for h in g.get("hooks", [])):
        continue
    hook = {"type": "command", "command": py, "args": ["-X", "utf8", script, mode], "timeout": timeout}
    group = {"hooks": [hook]}
    if matcher:
        group = {"matcher": matcher, **group}
    groups.append(group)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
'@
$mergeFile = Join-Path $Dest 'merge_hooks.py'
Set-Content -Path $mergeFile -Value $merge -Encoding UTF8
$fwd = { param($p) $p -replace '\\', '/' }
& $VenvPy $mergeFile $Settings (& $fwd $VenvPy) (& $fwd (Join-Path $Dest 'speak.py'))
if ($LASTEXITCODE -ne 0) { Die "Could not update $Settings (is it valid JSON?)." }
Remove-Item $mergeFile -Force

# ── 5. `tts` and `shh` commands ──────────────────────────────────────────────
$Pyw = Join-Path $Dest 'venv\Scripts\python.exe'
Set-Content -Path (Join-Path $Bin 'tts.cmd') -Encoding ASCII -Value @"
@echo off
rem Kokoro Voice for Claude Code: tts on ^| off ^| skip ^| shh ^| replay ^| last ^| clip ^| status
set S="$Pyw" -X utf8 "$Dest\speak.py"
if "%1"=="on" ( type nul > "$Claude\tts-on" & echo voice on & goto :eof )
if "%1"=="off" ( del /q "$Claude\tts-on" 2>nul & %S% shh & echo voice off & goto :eof )
if "%1"=="skip" ( %S% skip & goto :eof )
if "%1"=="shh" ( %S% shh & goto :eof )
if "%1"=="stop" ( %S% shh & goto :eof )
if "%1"=="replay" ( %S% replay & goto :eof )
if "%1"=="last" ( %S% last & goto :eof )
if "%1"=="clip" ( %S% clip & goto :eof )
if exist "$Claude\tts-on" ( echo voice on ) else ( echo voice off )
"@
Set-Content -Path (Join-Path $Bin 'shh.cmd') -Encoding ASCII -Value "@echo off`r`ncall `"%~dp0tts.cmd`" shh"

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $Bin)) {
    [Environment]::SetEnvironmentVariable('Path', (($userPath, $Bin) -join ';').Trim(';'), 'User')
    Say "Added $Bin to your PATH (open a new terminal to use 'tts' and 'shh')"
}

if ($VoiceOff) { Remove-Item (Join-Path $Claude 'tts-on') -ErrorAction SilentlyContinue }
else { New-Item -ItemType File -Force -Path (Join-Path $Claude 'tts-on') | Out-Null }

# ── 6. Optional: global hotkeys ──────────────────────────────────────────────
if ($Hotkeys) {
    $pythonw = Join-Path $Dest 'venv\Scripts\pythonw.exe'
    $ttsd = Join-Path $Dest 'ttsd_win.py'
    # A hidden launcher in the Startup folder: no console window, no admin rights.
    Set-Content -Path $StartupFile -Encoding ASCII -Value @"
' Kokoro Voice for Claude Code: global hotkeys (remove with uninstall.ps1)
CreateObject("WScript.Shell").Run """$pythonw"" ""$ttsd""", 0, False
"@
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*ttsd_win.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (-not $env:KOKORO_VOICE_NO_START) { Start-Process -FilePath $pythonw -ArgumentList "`"$ttsd`"" -WindowStyle Hidden }
    Say 'Hotkeys are on: Ctrl+Alt+T on/off, Ctrl+Alt+S stop, Ctrl+Alt+N skip, Ctrl+Alt+R newest reply, Ctrl+Alt+V read the clipboard'
}

# ── 7. Done ──────────────────────────────────────────────────────────────────
if (-not $NoTest) {
    Say 'Speaking a test line'
    'Kokoro voice is installed.' | & $VenvPy -X utf8 (Join-Path $Dest 'speak.py') read
}

Write-Host @"

Kokoro Voice is installed. Start a new Claude Code session to hear replies.
  tts on / tts off     turn the voice on or off
  shh                  stop talking now
  tts last             hear the newest reply again
  Uninstall            & "$Dest\uninstall.ps1"

If this saves you time, you can support it at https://openluxe.co/developers/tools/kokoro-voice
"@
