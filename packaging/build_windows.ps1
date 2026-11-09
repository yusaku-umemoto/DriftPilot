<#
.SYNOPSIS
  DriftPilot の Windows 版を作り、zip にする（ADR-0036）。リポジトリのどこから呼んでもよい。

    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1

  使う Python は -Python で変えられる（Python 3.14 系、x64）。既定は py ランチャーの 3.14。
  できるもの: dist\DriftPilot\（DriftPilot.exe・driftpilot-cli.exe）、dist\DriftPilot-<版>-windows-x64.zip
  署名はしない（ADR-0036 決定7）。作業の場所は build\ と dist\（どちらも .gitignore 済み）。
  DriftPilot を起動したままだと、上書きに失敗する（先に閉じる）。
#>
param([string]$Python = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "失敗しました（終了コード $LASTEXITCODE）: $File $($Arguments -join ' ')" }
}

if (-not $Python) {
    $Python = (& py -3.14 -c "import sys; print(sys.executable)").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Python 3.14 が見つかりません。-Python <python.exe のパス> を付けて呼んでください" }
}
Invoke-Checked $Python @("-c", "import sys; assert sys.version_info[:2] == (3, 14), sys.version")

$Venv = "build\pkg-venv"
$Py = "$Venv\Scripts\python.exe"
Write-Host "== 1/6 ビルド用の環境（$Venv）"
foreach ($p in @($Venv, "build\pyinstaller", "dist\DriftPilot")) { if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
Invoke-Checked $Python @("-m", "venv", $Venv)
Invoke-Checked $Py @("-m", "pip", "install", "-q", "--upgrade", "pip")
Invoke-Checked $Py @("-m", "pip", "install", "-q", "-c", "packaging\constraints.txt", ".[gui,llm]", "-r", "packaging\requirements-build.txt", "pip-licenses")

Write-Host "== 2/6 PyInstaller"
Invoke-Checked "$Venv\Scripts\pyinstaller.exe" @("--noconfirm", "--log-level", "WARN", "--distpath", "dist", "--workpath", "build\pyinstaller", "packaging\DriftPilot.spec")

$Out = "dist\DriftPilot"
$Cli = "$Out\driftpilot-cli.exe"
$Version = (& $Py -c "import importlib.metadata as m; print(m.version('driftpilot'))").Trim()

Write-Host "== 3/6 中身の確かめ"
& powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\packaging\smoke_test.ps1" -Cli $Cli
if ($LASTEXITCODE -ne 0) { throw "中身の確かめに失敗しました" }

Write-Host "== 4/6 ライセンスの文"
$Packages = (& $Py "packaging\runtime_packages.py").Trim() -split " "
# Windows の日本語の環境では既定が cp932 で、ライセンスの文に cp932 で表せない文字があると UnicodeEncodeError で止まる。Python の出力と PowerShell が受ける文字を UTF-8 にする（W87・ADR-0184 決定2）
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Licenses = & "$Venv\Scripts\pip-licenses.exe" --from=mixed --with-license-file --no-license-path --format=plain-vertical --packages @Packages
if ($LASTEXITCODE -ne 0) { throw "pip-licenses に失敗しました" }
$Base = (& $Py -c "import sys; print(sys.base_prefix)").Trim()
$PyVer = (& $Py -c "import platform; print(platform.python_version())").Trim()
$Text = ($Licenses -join "`n") + "`n`nPython $PyVer`nPSF License`n" + (Get-Content -Raw -Encoding UTF8 "$Base\LICENSE.txt")
[IO.File]::WriteAllText("$Root\$Out\THIRD_PARTY_LICENSES.txt", $Text, (New-Object Text.UTF8Encoding $false))
Copy-Item "packaging\README-first.txt" "$Out\はじめにお読みください.txt"

Write-Host "== 5/6 zip"
$Zip = "dist\DriftPilot-$Version-windows-x64.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
# zip の名前は UTF-8・区切りは / にする（2026-10-29 に Windows で確かめた）:
# - Compress-Archive と CreateFromDirectory は区切りを \ にする（zip の決まりは /。Mac・Linux で展開すると壊れる）
# - .NET Framework は [Text.Encoding]::UTF8 そのものを渡したときだけ「名前は UTF-8」の印（汎用フラグの bit 11）を付ける。
#   別に作った UTF8Encoding を渡すと印が付かず、日本語の名前が展開で化ける
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$Base = (Resolve-Path "$Root\dist").Path
$Stream = [IO.File]::Open("$Root\$Zip", [IO.FileMode]::CreateNew)
$Archive = New-Object IO.Compression.ZipArchive($Stream, [IO.Compression.ZipArchiveMode]::Create, $false, [Text.Encoding]::UTF8)
try {
    Get-ChildItem -Recurse -File "$Root\$Out" | ForEach-Object {
        $Name = $_.FullName.Substring($Base.Length + 1).Replace("\", "/")
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Archive, $_.FullName, $Name, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally {
    $Archive.Dispose()
    $Stream.Dispose()
}

Write-Host "== 6/6 できたもの"
"{0}  {1:N1} MB" -f $Out, ((Get-ChildItem -Recurse $Out | Measure-Object Length -Sum).Sum / 1MB)
"{0}  {1:N1} MB" -f $Zip, ((Get-Item $Zip).Length / 1MB)
