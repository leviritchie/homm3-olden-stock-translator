#Requires -Version 5.1
<#
.SYNOPSIS
  Convert a HoMM3 .h3m into a stock Olden Era .map (no prebuilt .exe).

.DESCRIPTION
  Default (no args): opens a WinForms GUI.
  With -Cli or conversion args: runs from the console.
  First run downloads the official CPython 3.12 Windows embeddable package from
  python.org (SHA256 verified) into .runtime\, then runs the translator from src\.
  Thirst_for_Power.map is auto-detected beside Core.zip under maps\.

.EXAMPLE
  .\Convert-Map.ps1
  # opens GUI

.EXAMPLE
  .\Convert-Map.ps1 -Cli -H3m "D:\Maps\Twins.h3m" -MapSid "vanilla_stock_twins" `
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
    [switch]$SkipBootstrap,
    [switch]$Cli
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# Needed for optional TextBox log targets and the GUI path.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

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

function Write-LogLine {
    param(
        [string]$Message,
        [System.Windows.Forms.TextBox]$LogBox = $null
    )
    if ($null -ne $LogBox) {
        if ($LogBox.InvokeRequired) {
            [void]$LogBox.Invoke(
                [Action[string]] {
                    param([string]$Line)
                    $LogBox.AppendText("$Line`r`n")
                },
                [string[]]@($Message)
            )
        }
        else {
            $LogBox.AppendText("$Message`r`n")
        }
    }
    else {
        Write-Host $Message
    }
}

function Assert-Sha256([string]$Path, [string]$Expected) {
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA256 mismatch for $Path`nExpected $Expected`nActual   $actual"
    }
}

function Get-DefaultSid([string]$H3mPath) {
    $stem = [IO.Path]::GetFileNameWithoutExtension($H3mPath)
    $slug = ($stem.ToLowerInvariant() -replace '[^a-z0-9]+', '_').Trim('_')
    if (-not $slug) { $slug = "map" }
    return "vanilla_stock_$slug"
}

function Find-SuggestedStockCore {
    if ($env:STOCK_CORE -and (Test-Path -LiteralPath $env:STOCK_CORE)) {
        return $env:STOCK_CORE
    }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip",
        "$env:ProgramFiles\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip",
        "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip",
        "V:\SteamLibrary\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip",
        "C:\Program Files (x86)\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip"
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }
    return ""
}

function Get-MapsDirBesideCore([string]$CorePath) {
    if (-not $CorePath) { return "" }
    try {
        $maps = Join-Path (Split-Path -Parent $CorePath) "maps"
        if (Test-Path -LiteralPath $maps) { return $maps }
    }
    catch { }
    return ""
}

function Ensure-Runtime {
    param([System.Windows.Forms.TextBox]$LogBox = $null)

    if ($SkipBootstrap -and (Test-Path $PythonExe)) {
        Write-LogLine "Using existing runtime at $PythonDir" -LogBox $LogBox
        return
    }
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    if (-not (Test-Path $PythonExe)) {
        Write-LogLine "Downloading official CPython $PythonVersion embeddable from python.org ..." -LogBox $LogBox
        $zipPath = Join-Path $RuntimeDir $EmbedName
        Invoke-WebRequest -Uri $EmbedUrl -OutFile $zipPath
        Assert-Sha256 $zipPath $EmbedSha256
        if (Test-Path $PythonDir) { Remove-Item -Recurse -Force $PythonDir }
        Expand-Archive -Path $zipPath -DestinationPath $PythonDir
        Remove-Item $zipPath

        $pth = Get-ChildItem $PythonDir -Filter "python*._pth" | Select-Object -First 1
        if (-not $pth) { throw "python*._pth not found under $PythonDir" }
        $pthText = @(Get-Content $pth.FullName)
        $pthText = $pthText | ForEach-Object {
            if ($_ -match '^\s*#\s*import site\s*$') { 'import site' } else { $_ }
        }
        if ($pthText -notcontains 'import site') { $pthText += 'import site' }
        if ($pthText -notcontains '..\..\src') { $pthText = @('..\..\src') + @($pthText) }
        Set-Content -Path $pth.FullName -Value $pthText -Encoding ASCII

        Write-LogLine "Installing pip into embeddable runtime ..." -LogBox $LogBox
        $getPip = Join-Path $RuntimeDir "get-pip.py"
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPip
        & $PythonExe $getPip --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { throw "get-pip.py failed with exit code $LASTEXITCODE" }
        Remove-Item $getPip -ErrorAction SilentlyContinue
        Write-LogLine "Runtime ready." -LogBox $LogBox
    }

    if (-not (Test-Path $SrcDir)) {
        throw "Translator src/ folder missing next to Convert-Map.ps1: $SrcDir"
    }
}

function Assert-ConversionInputs {
    param(
        [string]$H3mPath,
        [string]$CorePath,
        [string]$TemplatePath = ""
    )
    foreach ($pair in @(
            @{ Name = "H3m"; Path = $H3mPath },
            @{ Name = "StockCore"; Path = $CorePath }
        )) {
        if ([string]::IsNullOrWhiteSpace($pair.Path) -or -not (Test-Path -LiteralPath $pair.Path)) {
            throw "$($pair.Name) not found: $($pair.Path)"
        }
    }
    if ($TemplatePath -and -not (Test-Path -LiteralPath $TemplatePath)) {
        throw "TemplateMap not found: $TemplatePath"
    }
}

function Invoke-Conversion {
    param(
        [Parameter(Mandatory = $true)][string]$H3mPath,
        [Parameter(Mandatory = $true)][string]$CorePath,
        [Parameter(Mandatory = $true)][string]$OutDirPath,
        [Parameter(Mandatory = $true)][string]$Sid,
        [string]$TemplatePath = "",
        [string]$InstallMapsPath = "",
        [System.Windows.Forms.TextBox]$LogBox = $null
    )

    Assert-ConversionInputs -H3mPath $H3mPath -CorePath $CorePath -TemplatePath $TemplatePath
    Ensure-Runtime -LogBox $LogBox

    $tool = Join-Path $Root "tools\build_vanilla_stock_map.py"
    $argsList = @(
        $tool,
        "--h3m", $H3mPath,
        "--out-dir", $OutDirPath,
        "--map-sid", $Sid,
        "--stock-core", $CorePath
    )
    if ($TemplatePath) {
        $argsList += @("--template-map", $TemplatePath)
    }
    if ($InstallMapsPath) {
        $argsList += @("--install-maps-dir", $InstallMapsPath)
    }

    Write-LogLine "" -LogBox $LogBox
    Write-LogLine "Converting..." -LogBox $LogBox
    Write-LogLine "  H3m: $H3mPath" -LogBox $LogBox
    Write-LogLine "  Core: $CorePath" -LogBox $LogBox
    Write-LogLine "  SID: $Sid" -LogBox $LogBox
    Write-LogLine "  Out: $OutDirPath" -LogBox $LogBox
    if ($InstallMapsPath) {
        Write-LogLine "  Install maps: $InstallMapsPath" -LogBox $LogBox
    }

    $env:PYTHONPATH = $SrcDir
    $output = & $PythonExe @argsList 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in @($output)) {
        Write-LogLine ([string]$line) -LogBox $LogBox
    }

    if ($exitCode -ne 0) {
        throw "Conversion failed with exit code $exitCode"
    }

    $mapPath = Join-Path $OutDirPath "maps\$Sid.map"
    Write-LogLine "" -LogBox $LogBox
    Write-LogLine "Done." -LogBox $LogBox
    Write-LogLine "Map: $mapPath" -LogBox $LogBox
    if ($InstallMapsPath) {
        Write-LogLine "Installed copy: $(Join-Path $InstallMapsPath "$Sid.map")" -LogBox $LogBox
    }
    Write-LogLine "Open stock Olden Era and load the scenario named / SID: $Sid" -LogBox $LogBox
    return $mapPath
}

function Show-ConverterGui {
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "HoMM3 → Olden Era Map Converter"
    $form.ClientSize = New-Object System.Drawing.Size(740, 600)
    $form.MinimumSize = New-Object System.Drawing.Size(640, 520)
    $form.StartPosition = "CenterScreen"
    $form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

    function Add-FieldLabel([string]$Text, [int]$Top) {
        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Text = $Text
        $lbl.Location = New-Object System.Drawing.Point(16, $Top)
        $lbl.AutoSize = $true
        $form.Controls.Add($lbl)
    }

    function Add-TextBox([int]$Top, [int]$Width = 600) {
        $box = New-Object System.Windows.Forms.TextBox
        $box.Location = New-Object System.Drawing.Point(16, $Top)
        $box.Width = $Width
        $box.Anchor = "Top,Left,Right"
        $form.Controls.Add($box)
        return $box
    }

    function Add-BrowseButton([int]$Top) {
        $btn = New-Object System.Windows.Forms.Button
        $btn.Text = "Browse..."
        $btn.Location = New-Object System.Drawing.Point(630, $Top)
        $btn.Width = 90
        $btn.Anchor = "Top,Right"
        $form.Controls.Add($btn)
        return $btn
    }

    Add-FieldLabel "HoMM3 map (.h3m)" 16
    $txtH3m = Add-TextBox 36
    $btnH3m = Add-BrowseButton 34

    Add-FieldLabel "Olden Era Core.zip" 72
    $txtCore = Add-TextBox 92
    $btnCore = Add-BrowseButton 90

    Add-FieldLabel "Olden map SID" 128
    $txtSid = Add-TextBox 148 704

    Add-FieldLabel "Output folder" 184
    $txtOut = Add-TextBox 204
    $btnOut = Add-BrowseButton 202

    Add-FieldLabel "Install into Olden maps folder (optional)" 240
    $txtInstall = Add-TextBox 260
    $btnInstall = Add-BrowseButton 258

    $lblNote = New-Object System.Windows.Forms.Label
    $lblNote.Text = "Thirst_for_Power.map is auto-detected beside Core.zip under StreamingAssets/maps/."
    $lblNote.Location = New-Object System.Drawing.Point(16, 296)
    $lblNote.AutoSize = $true
    $lblNote.ForeColor = [System.Drawing.Color]::DimGray
    $form.Controls.Add($lblNote)

    $btnConvert = New-Object System.Windows.Forms.Button
    $btnConvert.Text = "Convert"
    $btnConvert.Location = New-Object System.Drawing.Point(16, 324)
    $btnConvert.Size = New-Object System.Drawing.Size(120, 32)
    $form.Controls.Add($btnConvert)

    $lblStatus = New-Object System.Windows.Forms.Label
    $lblStatus.Text = "Ready."
    $lblStatus.Location = New-Object System.Drawing.Point(150, 332)
    $lblStatus.AutoSize = $true
    $form.Controls.Add($lblStatus)

    $txtLog = New-Object System.Windows.Forms.TextBox
    $txtLog.Multiline = $true
    $txtLog.ScrollBars = "Both"
    $txtLog.ReadOnly = $true
    $txtLog.WordWrap = $false
    $txtLog.Font = New-Object System.Drawing.Font("Consolas", 9)
    $txtLog.Location = New-Object System.Drawing.Point(16, 368)
    $txtLog.Size = New-Object System.Drawing.Size(704, 216)
    $txtLog.Anchor = "Top,Bottom,Left,Right"
    $form.Controls.Add($txtLog)

    # Prefill from args / env / common Steam path.
    if ($H3m) { $txtH3m.Text = $H3m }
    $suggestedCore = if ($StockCore) { $StockCore } elseif ($env:STOCK_CORE) { $env:STOCK_CORE } else { Find-SuggestedStockCore }
    if ($suggestedCore) { $txtCore.Text = $suggestedCore }
    if ($MapSid) { $txtSid.Text = $MapSid }
    if ($OutDir) { $txtOut.Text = $OutDir }
    if ($InstallMapsDir) {
        $txtInstall.Text = $InstallMapsDir
    }
    elseif ($env:STOCK_MAPS_DIR) {
        $txtInstall.Text = $env:STOCK_MAPS_DIR
    }
    elseif ($suggestedCore) {
        $txtInstall.Text = Get-MapsDirBesideCore $suggestedCore
    }

    $state = @{
        SidTouched     = [bool]$MapSid
        OutTouched     = [bool]$OutDir
        InstallTouched = [bool]($InstallMapsDir -or $env:STOCK_MAPS_DIR)
        Busy           = $false
    }

    $btnH3m.Add_Click({
            $dlg = New-Object System.Windows.Forms.OpenFileDialog
            $dlg.Filter = "HoMM3 map (*.h3m)|*.h3m|All files (*.*)|*.*"
            $dlg.Title = "Select HoMM3 map"
            if ($txtH3m.Text) {
                try { $dlg.InitialDirectory = [IO.Path]::GetDirectoryName($txtH3m.Text) } catch { }
            }
            if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
                $txtH3m.Text = $dlg.FileName
            }
        }.GetNewClosure())

    $btnCore.Add_Click({
            $dlg = New-Object System.Windows.Forms.OpenFileDialog
            $dlg.Filter = "Olden Core (Core.zip)|Core.zip|Zip files (*.zip)|*.zip|All files (*.*)|*.*"
            $dlg.Title = "Select Olden Era Core.zip"
            $dlg.FileName = "Core.zip"
            if ($txtCore.Text) {
                try { $dlg.InitialDirectory = [IO.Path]::GetDirectoryName($txtCore.Text) } catch { }
            }
            if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
                $txtCore.Text = $dlg.FileName
            }
        }.GetNewClosure())

    $btnOut.Add_Click({
            $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
            $dlg.Description = "Choose output folder"
            if ($txtOut.Text -and (Test-Path -LiteralPath $txtOut.Text)) {
                $dlg.SelectedPath = $txtOut.Text
            }
            if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
                $state.OutTouched = $true
                $txtOut.Text = $dlg.SelectedPath
            }
        }.GetNewClosure())

    $btnInstall.Add_Click({
            $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
            $dlg.Description = "Olden StreamingAssets/maps folder (optional)"
            if ($txtInstall.Text -and (Test-Path -LiteralPath $txtInstall.Text)) {
                $dlg.SelectedPath = $txtInstall.Text
            }
            if ($dlg.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
                $state.InstallTouched = $true
                $txtInstall.Text = $dlg.SelectedPath
            }
        }.GetNewClosure())

    $txtSid.Add_TextChanged({ $state.SidTouched = $true }.GetNewClosure())
    $txtOut.Add_TextChanged({
            # Ignore programmatic fills; Browse sets OutTouched explicitly before Text.
        }.GetNewClosure())

    $syncFromH3m = {
        if (-not $txtH3m.Text) { return }
        if (-not $state.SidTouched) {
            $txtSid.Text = Get-DefaultSid $txtH3m.Text
            $state.SidTouched = $false
        }
        if (-not $state.OutTouched) {
            $sidValue = if ($txtSid.Text) { $txtSid.Text } else { Get-DefaultSid $txtH3m.Text }
            $txtOut.Text = Join-Path $Root "artifacts\$sidValue"
            $state.OutTouched = $false
        }
    }.GetNewClosure()

    $txtH3m.Add_TextChanged({ & $syncFromH3m }.GetNewClosure())

    $txtCore.Add_TextChanged({
            if (-not $state.InstallTouched) {
                $maps = Get-MapsDirBesideCore $txtCore.Text
                if ($maps) { $txtInstall.Text = $maps }
            }
        }.GetNewClosure())

    if ($txtH3m.Text) { & $syncFromH3m }
    elseif ($txtSid.Text -and -not $txtOut.Text) {
        $txtOut.Text = Join-Path $Root "artifacts\$($txtSid.Text)"
        $state.OutTouched = $false
    }

    $worker = New-Object System.ComponentModel.BackgroundWorker
    $worker.WorkerReportsProgress = $false
    $worker.WorkerSupportsCancellation = $false
    $worker.Add_DoWork({
            param($sender, $e)
            $p = $e.Argument
            $e.Result = Invoke-Conversion `
                -H3mPath $p.H3mPath `
                -CorePath $p.CorePath `
                -OutDirPath $p.OutPath `
                -Sid $p.Sid `
                -TemplatePath $p.TemplatePath `
                -InstallMapsPath $p.InstallPath `
                -LogBox $p.LogBox
        }.GetNewClosure())
    $worker.Add_RunWorkerCompleted({
            param($sender, $e)
            $state.Busy = $false
            $btnConvert.Enabled = $true
            if ($e.Error) {
                $err = $e.Error.Message
                Write-LogLine "ERROR: $err" -LogBox $txtLog
                $lblStatus.Text = "Failed."
                [System.Windows.Forms.MessageBox]::Show(
                    $form,
                    $err,
                    "Conversion failed",
                    [System.Windows.Forms.MessageBoxButtons]::OK,
                    [System.Windows.Forms.MessageBoxIcon]::Error
                ) | Out-Null
                return
            }
            $lblStatus.Text = "Done."
            [System.Windows.Forms.MessageBox]::Show(
                $form,
                "Conversion finished. See the log for warnings and the output path.",
                "Convert Map",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information
            ) | Out-Null
        }.GetNewClosure())

    $btnConvert.Add_Click({
            if ($state.Busy -or $worker.IsBusy) { return }
            $txtLog.Clear()
            $h3mPath = $txtH3m.Text.Trim().Trim('"')
            $corePath = $txtCore.Text.Trim().Trim('"')
            $sid = $txtSid.Text.Trim()
            $outPath = $txtOut.Text.Trim().Trim('"')
            $installPath = $txtInstall.Text.Trim().Trim('"')
            $templatePath = if ($TemplateMap) { $TemplateMap } elseif ($env:STOCK_TEMPLATE_MAP) { $env:STOCK_TEMPLATE_MAP } else { "" }

            if (-not $sid -and $h3mPath) {
                $sid = Get-DefaultSid $h3mPath
                $txtSid.Text = $sid
            }
            if (-not $outPath -and $sid) {
                $outPath = Join-Path $Root "artifacts\$sid"
                $txtOut.Text = $outPath
            }

            $state.Busy = $true
            $btnConvert.Enabled = $false
            $lblStatus.Text = "Working..."
            $worker.RunWorkerAsync(@{
                    H3mPath      = $h3mPath
                    CorePath     = $corePath
                    OutPath      = $outPath
                    Sid          = $sid
                    TemplatePath = $templatePath
                    InstallPath  = $installPath
                    LogBox       = $txtLog
                })
        }.GetNewClosure())

    [void]$form.ShowDialog()
}

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

function Invoke-CliConversion {
    $script:H3m = $H3m
    $script:OutDir = $OutDir
    $script:MapSid = $MapSid
    $script:StockCore = if ($StockCore) { $StockCore } else { $env:STOCK_CORE }
    $script:TemplateMap = if ($TemplateMap) { $TemplateMap } else { $env:STOCK_TEMPLATE_MAP }
    $script:InstallMapsDir = if ($InstallMapsDir) { $InstallMapsDir } else { $env:STOCK_MAPS_DIR }

    $needPrompt = -not $script:H3m -or -not $script:StockCore
    if ($needPrompt) {
        Write-Host ""
        Write-Host "HoMM3 -> stock Olden Era map converter (CLI)"
        Write-Host "Template map Thirst_for_Power.map is auto-detected beside Core.zip."
        Write-Host ""
        $script:H3m = Read-Default "Path to .h3m" $script:H3m
        if (-not $script:MapSid) {
            $script:MapSid = Get-DefaultSid $script:H3m
        }
        $script:MapSid = Read-Default "Olden map SID" $script:MapSid
        if (-not $script:OutDir) {
            $script:OutDir = Join-Path $Root "artifacts\$($script:MapSid)"
        }
        $script:OutDir = Read-Default "Output folder" $script:OutDir
        if (-not $script:StockCore) {
            $script:StockCore = Find-SuggestedStockCore
        }
        $script:StockCore = Read-Default "Path to stock Core.zip" $script:StockCore
        $installDefault = if ($script:InstallMapsDir) { $script:InstallMapsDir } else { Get-MapsDirBesideCore $script:StockCore }
        $script:InstallMapsDir = Read-Default "Install into Olden maps folder? (path or leave empty)" $installDefault
    }

    if (-not $script:MapSid) {
        $script:MapSid = Get-DefaultSid $script:H3m
    }
    if (-not $script:OutDir) {
        $script:OutDir = Join-Path $Root "artifacts\$($script:MapSid)"
    }

    Invoke-Conversion `
        -H3mPath $script:H3m `
        -CorePath $script:StockCore `
        -OutDirPath $script:OutDir `
        -Sid $script:MapSid `
        -TemplatePath $script:TemplateMap `
        -InstallMapsPath $script:InstallMapsDir | Out-Null
}

# Entry: GUI by default; CLI when -Cli or conversion args are supplied.
$hasConversionArgs = [bool](
    $H3m -or $OutDir -or $MapSid -or $StockCore -or $TemplateMap -or $InstallMapsDir
)
if ($Cli -or $hasConversionArgs) {
    Invoke-CliConversion
}
else {
    Show-ConverterGui
}
