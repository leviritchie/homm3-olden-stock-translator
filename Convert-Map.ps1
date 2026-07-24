#Requires -Version 5.1
<#
.SYNOPSIS
  Convert a HoMM3 .h3m into a stock Olden Era .map (no prebuilt .exe).

.DESCRIPTION
  First run downloads the official CPython 3.12 Windows embeddable package from
  python.org (SHA256 verified) into .runtime\, then runs the translator from src\.
  You need your own Core.zip and .h3m. The stock template map
  (Thirst_for_Power.map) is auto-detected beside Core.zip under maps\.

.EXAMPLE
  .\Convert-Map.ps1
  # interactive prompts

.EXAMPLE
  .\Convert-Map.ps1 -H3m "D:\Maps\Twins.h3m" -MapSid "vanilla_stock_twins" `
    -StockCore "D:\Steam\...\Core.zip" `
    -OutDir ".\artifacts\twins" -InstallMapsDir "D:\Steam\...\maps"
#>
[CmdletBinding()]
param(
    [string]$H3m = "",
    [string]$OutDir = "",
    [string]$MapSid = "",
    [string]$StockCore = "",
    [string]$TemplateMap = "",
    [string]$InstallMapsDir = "",
    [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PythonVersion = "3.12.8"
$EmbedName = "python-$PythonVersion-embed-amd64.zip"
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/$EmbedName"
# Official python.org embeddable amd64 zip (pinned).
$EmbedSha256 = "8d3f33be9eb810f23c102f08475af2854e50484b8e4e06275e937be61ce3d2fb"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$RuntimeDir = Join-Path $Root ".runtime"
$PythonDir = Join-Path $RuntimeDir "python"
$PythonExe = Join-Path $PythonDir "python.exe"
$SrcDir = Join-Path $Root "src"

function Read-Default([string]$Prompt, [string]$Default) {
    if ($Default) {
        $value = Read-Host "$Prompt [$Default]"
        if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
        return $value.Trim('"')
    }
    while ($true) {
        $value = Read-Host $Prompt
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value.Trim('"') }
    }
}

function Assert-Sha256([string]$Path, [string]$Expected) {
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA256 mismatch for $Path`nExpected $Expected`nActual   $actual"
    }
}

function Ensure-Runtime {
    if ($SkipBootstrap -and (Test-Path $PythonExe)) {
        Write-Host "Using existing runtime at $PythonDir"
        return
    }
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    if (-not (Test-Path $PythonExe)) {
        Write-Host "Downloading official CPython $PythonVersion embeddable from python.org ..."
        $zipPath = Join-Path $RuntimeDir $EmbedName
        Invoke-WebRequest -Uri $EmbedUrl -OutFile $zipPath
        Assert-Sha256 $zipPath $EmbedSha256
        if (Test-Path $PythonDir) { Remove-Item -Recurse -Force $PythonDir }
        Expand-Archive -Path $zipPath -DestinationPath $PythonDir
        Remove-Item $zipPath

        # Enable site-packages / pip on embeddable builds.
        $pth = Get-ChildItem $PythonDir -Filter "python*._pth" | Select-Object -First 1
        if (-not $pth) { throw "python*._pth not found under $PythonDir" }
        $pthText = Get-Content $pth.FullName
        $pthText = $pthText | ForEach-Object {
            if ($_ -match '^\s*#\s*import site\s*$') { 'import site' } else { $_ }
        }
        if ($pthText -notcontains 'import site') { $pthText += 'import site' }
        # Ensure the translator src/ tree is importable without pip install.
        if ($pthText -notcontains '..\..\src') { $pthText = @('..\..\src') + $pthText }
        Set-Content -Path $pth.FullName -Value $pthText -Encoding ASCII

        Write-Host "Installing pip into embeddable runtime ..."
        $getPip = Join-Path $RuntimeDir "get-pip.py"
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPip
        & $PythonExe $getPip --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { throw "get-pip.py failed with exit code $LASTEXITCODE" }
        Remove-Item $getPip -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $SrcDir)) {
        throw "Translator src/ folder missing next to Convert-Map.ps1: $SrcDir"
    }
}

function Resolve-Inputs {
    $script:H3m = $H3m
    $script:OutDir = $OutDir
    $script:MapSid = $MapSid
    $script:StockCore = if ($StockCore) { $StockCore } else { $env:STOCK_CORE }
    $script:TemplateMap = if ($TemplateMap) { $TemplateMap } else { $env:STOCK_TEMPLATE_MAP }
    $script:InstallMapsDir = if ($InstallMapsDir) { $InstallMapsDir } else { $env:STOCK_MAPS_DIR }

    $interactive = -not $script:H3m -or -not $script:OutDir -or -not $script:StockCore
    if ($interactive) {
        Write-Host ""
        Write-Host "HoMM3 -> stock Olden Era map converter"
        Write-Host "This launcher uses official Python from python.org (verified checksum)."
        Write-Host "It does not ship a custom .exe of the translator."
        Write-Host "Template map Thirst_for_Power.map is auto-detected beside Core.zip."
        Write-Host ""
        $script:H3m = Read-Default "Path to .h3m" $script:H3m
        $defaultSid = if ($script:MapSid) {
            $script:MapSid
        } else {
            "vanilla_stock_" + [IO.Path]::GetFileNameWithoutExtension($script:H3m).ToLowerInvariant().Replace(' ', '_')
        }
        $script:MapSid = Read-Default "Olden map SID" $defaultSid
        $defaultOut = if ($script:OutDir) { $script:OutDir } else { Join-Path $Root "artifacts\$($script:MapSid)" }
        $script:OutDir = Read-Default "Output folder" $defaultOut
        $script:StockCore = Read-Default "Path to stock Core.zip" $script:StockCore
        $installDefault = if ($script:InstallMapsDir) { $script:InstallMapsDir } else { "" }
        $script:InstallMapsDir = Read-Default "Install into Olden maps folder? (path or leave empty)" $installDefault
    }

    foreach ($pair in @(
            @{ Name = "H3m"; Path = $script:H3m },
            @{ Name = "StockCore"; Path = $script:StockCore }
        )) {
        if (-not (Test-Path -LiteralPath $pair.Path)) {
            throw "$($pair.Name) not found: $($pair.Path)"
        }
    }
    if (-not $script:MapSid) {
        $script:MapSid = "vanilla_stock_" + [IO.Path]::GetFileNameWithoutExtension($script:H3m).ToLowerInvariant().Replace(' ', '_')
    }
    if (-not $script:OutDir) {
        $script:OutDir = Join-Path $Root "artifacts\$($script:MapSid)"
    }
}

Ensure-Runtime
Resolve-Inputs

$tool = Join-Path $Root "tools\build_vanilla_stock_map.py"
$argsList = @(
    $tool,
    "--h3m", $script:H3m,
    "--out-dir", $script:OutDir,
    "--map-sid", $script:MapSid,
    "--stock-core", $script:StockCore
)
if ($script:TemplateMap) {
    $argsList += @("--template-map", $script:TemplateMap)
}
if ($script:InstallMapsDir) {
    $argsList += @("--install-maps-dir", $script:InstallMapsDir)
}

Write-Host ""
Write-Host "Converting..."
$env:PYTHONPATH = $SrcDir
& $PythonExe @argsList
if ($LASTEXITCODE -ne 0) {
    throw "Conversion failed with exit code $LASTEXITCODE"
}

$mapPath = Join-Path $script:OutDir "maps\$($script:MapSid).map"
Write-Host ""
Write-Host "Done."
Write-Host "Map: $mapPath"
if ($script:InstallMapsDir) {
    Write-Host "Installed copy: $(Join-Path $script:InstallMapsDir "$($script:MapSid).map")"
}
Write-Host "Open stock Olden Era and load the scenario named / SID: $($script:MapSid)"
