<#
  Creates the desktop shortcut for TTS by fakedevbrad.

  The shortcut runs launch.pyw under pythonw.exe, so there's no console window,
  and wears icon.ico. Re-run it any time to repair or move the shortcut.

      powershell -ExecutionPolicy Bypass -File install_shortcut.ps1

  -Startup additionally installs QuickRead (hotkey.pyw) into the Startup folder,
  so the Alt+Z "read my selection" hotkey is there after a reboot.

      powershell -ExecutionPolicy Bypass -File install_shortcut.ps1 -Startup
#>

param(
    [string]$Python = "",
    [string]$Name   = "TTS by fakedevbrad",
    [switch]$Startup
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Pythonw {
    # Prefer an interpreter that already has the TTS dependencies installed.
    $candidates = @()

    Get-ChildItem "$env:LOCALAPPDATA\Python" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { $candidates += Join-Path $_.FullName "pythonw.exe" }

    $onPath = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }

    foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", "$env:PROGRAMFILES")) {
        Get-ChildItem $root -Directory -Filter "Python*" -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates += Join-Path $_.FullName "pythonw.exe" }
    }

    $existing = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
    if (-not $existing) { return $null }

    # Probing writes to stderr on failure; under ErrorActionPreference=Stop that
    # would be fatal, so relax it and read the exit code instead.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        foreach ($candidate in $existing) {
            $console = Join-Path (Split-Path $candidate) "python.exe"
            if (-not (Test-Path $console)) { continue }
            & $console -c "import kokoro_onnx" *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    } finally {
        $ErrorActionPreference = $previous
    }
    return $existing[0]
}

if (-not $Python) { $Python = Find-Pythonw }
if (-not $Python -or -not (Test-Path $Python)) {
    throw "Couldn't find pythonw.exe. Pass one explicitly: -Python C:\path\to\pythonw.exe"
}

$launcher = Join-Path $here "launch.pyw"
$icon     = Join-Path $here "icon.ico"
if (-not (Test-Path $launcher)) { throw "Missing launch.pyw next to this script." }
if (-not (Test-Path $icon)) {
    Write-Host "  icon.ico missing - generating it..."
    & (Join-Path (Split-Path $Python) "python.exe") (Join-Path $here "make_icon.py")
}

function New-AppShortcut {
    param($LinkPath, $Target, $Script, $Description)

    $shell    = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($LinkPath)
    $shortcut.TargetPath       = $Target
    $shortcut.Arguments        = "`"$Script`""
    $shortcut.WorkingDirectory = $here
    $shortcut.IconLocation     = "$icon,0"
    $shortcut.Description      = $Description
    $shortcut.WindowStyle      = 7   # start minimised; neither script shows a console
    $shortcut.Save()
}

$linkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Name.lnk"
New-AppShortcut $linkPath $Python $launcher "Local AI audiobook reader"

Write-Host ""
Write-Host "  Shortcut created" -ForegroundColor Green
Write-Host "    $linkPath"
Write-Host "    -> $Python `"$launcher`""

if ($Startup) {
    $quickread = Join-Path $here "hotkey.pyw"
    if (-not (Test-Path $quickread)) { throw "Missing hotkey.pyw next to this script." }

    $startupDir  = [Environment]::GetFolderPath("Startup")
    $startupLink = Join-Path $startupDir "TTS QuickRead.lnk"
    New-AppShortcut $startupLink $Python $quickread "Read the selected text aloud"

    Write-Host ""
    Write-Host "  QuickRead will start with Windows" -ForegroundColor Green
    Write-Host "    $startupLink"
    Write-Host "    Delete that shortcut to stop it starting automatically."
}

Write-Host ""
