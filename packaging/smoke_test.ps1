<#
.SYNOPSIS
  できたコマンド版（driftpilot-cli.exe）の中身を確かめる（ADR-0036 決定6）。Windows 用。Mac は smoke_test.sh。
    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\smoke_test.ps1 -Cli <driftpilot-cli.exe のパス>
  通信しない。キーを読み書きしない。値はすべて架空（tests\fixtures）。
#>
param([Parameter(Mandatory)][string]$Cli)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Fx = "$Root\tests\fixtures"
function Fail([string]$What) { Write-Host "確かめ失敗: $What"; exit 1 }

# 外部コマンドを呼ぶときだけ止まらない設定にする。Windows PowerShell 5.1 は、外部コマンドが標準エラーに書いた行を
# リダイレクトすると NativeCommandError として扱い、Stop だとそこで止まる（2026-10-29 に Windows で起きた）。
# 成否は終了コード（$LASTEXITCODE）で見る
function Invoke-Native([scriptblock]$Block) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $saved }
}

# JSON は UTF-8 で出る（ADR-0023 決定1）ので、JSON を読むときだけ UTF-8 で受け取る。
# 人が読む文（selfcheck・llm-check）はコンソールの文字コードで出る（決定2）ので、ほかでは変えない
function Invoke-Json([scriptblock]$Block) {
    $saved = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
    try { (Invoke-Native $Block) -join "`n" | ConvertFrom-Json } finally { [Console]::OutputEncoding = $saved }
}

# 1. 自己診断（REQ-38 の selfcheck。無い版では飛ばす）
Invoke-Native { & $Cli selfcheck --help *> $null }
if ($LASTEXITCODE -eq 0) {
    Invoke-Native { & $Cli selfcheck 2>&1 | ForEach-Object { "$_" } }
    if ($LASTEXITCODE -ne 0) { Fail "selfcheck" }
} else { Write-Host "（selfcheck が無い版なので飛ばします）" }

# 2. 比較（開発環境と同じ結果になること。2026-10-29 の値）
$d = Invoke-Json { & $Cli compare "$Fx\hier_config_check\running.txt" "$Fx\hier_config_check\intended.txt" --format json 2>$null }
$got = "$($d.status),$($d.structured.diffs.Count),$($d.raw.diffs.Count)"
if ($got -ne "difference,4,9") { Fail "比較の結果が違う: $got" }
Write-Host "比較: OK ($got)"

# 3. FortiGate（D型）を同じもの同士で比べて差分が無いこと
$s = (Invoke-Json { & $Cli compare "$Fx\fortinet\running.txt" "$Fx\fortinet\running.txt" --platform fortinet --format json 2>$null }).status
if ($s -ne "no_difference") { Fail "FortiGate: $s" }
Write-Host "FortiGate: OK"

# 4. 接続テストは、設定もキーも無い一時の APPDATA で、送らずに終了コード 2 で終わること
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("dp-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
$saved = @{ APPDATA = $env:APPDATA; KEY = $env:DRIFTPILOT_LLM_API_KEY }
try {
    $env:APPDATA = $tmp
    Remove-Item Env:DRIFTPILOT_LLM_API_KEY -ErrorAction SilentlyContinue
    Invoke-Native { "" | & $Cli llm-check *> $null }
    $code = $LASTEXITCODE
} finally {
    $env:APPDATA = $saved.APPDATA
    if ($saved.KEY) { $env:DRIFTPILOT_LLM_API_KEY = $saved.KEY }
    Remove-Item -Recurse -Force $tmp
}
if ($code -ne 2) { Fail "llm-check の終了コードが $code（2 のはず）" }
Write-Host "llm-check: OK（送らずに 2）"

# 5. pywebview の画面の部品（REQ-103）。selfcheck の「pywebview <版>」の項目が OK であること。
#    gui_web と webview が読み込めて、static\ のファイルが入っていることになる。窓は開かない
Invoke-Native { & $Cli selfcheck --help *> $null }
if ($LASTEXITCODE -eq 0) {
    $all = Invoke-Json { & $Cli selfcheck --format json 2>$null }
    $web = @($all | Where-Object { $_.detail -like "pywebview *" })
    if ($web.Count -ne 1 -or -not $web[0].ok) { Fail "pywebview の画面の部品" }
    Write-Host "画面の部品（web）: OK ($($web[0].detail))"
}
Write-Host "確かめ: すべて OK"
exit 0
