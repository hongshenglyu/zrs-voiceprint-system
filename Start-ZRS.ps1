[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'ZRS/launch.json'),
    [ValidateSet('Start','Init','SetPassword','Backup','Restore')][string]$Action = 'Start',
    [string]$Snapshot
)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Run Install-ZRS.ps1 first.' }
if (!(Test-Path -LiteralPath $Config -PathType Leaf)) { throw "Missing private launch configuration: $Config" }
$cfg = Get-Content -Raw -Encoding UTF8 -LiteralPath $Config | ConvertFrom-Json
if ($cfg.mode -cnotin @('E14', 'synthetic-demo')) { throw 'Set mode to E14 or synthetic-demo.' }
if ($cfg.study_dir -notmatch '^[a-zA-Z]:[\\/]') { throw 'Set study_dir to an absolute local Windows path.' }

$zrsArgs = @('-B', (Join-Path $PSScriptRoot 'tools/zrs_study.py'), '--study-dir', $cfg.study_dir, '--mode', $cfg.mode)
switch ($Action) {
    'Init' { $zrsArgs += '--init' }
    'SetPassword' { $zrsArgs += '--set-operator-password' }
    'Backup' {
        if (!$Snapshot) { throw 'Provide -Snapshot for Backup.' }
        $zrsArgs += @('--backup', $Snapshot)
    }
    'Restore' {
        if (!$Snapshot) { throw 'Provide -Snapshot for Restore.' }
        $zrsArgs += @('--restore', $Snapshot)
    }
    'Start' {
        $port = if ($null -ne $cfg.port) { $cfg.port } else { 8765 }
        $zrsArgs += @('--port', $port)
        if ($cfg.mode -ceq 'E14') {
            if ($cfg.e14_config -notmatch '^[a-zA-Z]:[\\/]') { throw 'Set e14_config to an absolute path to model.json.' }
            $zrsArgs += @('--e14-config', $cfg.e14_config)
            if ($cfg.policy_approval) {
                if ($cfg.policy_approval -notmatch '^[a-zA-Z]:[\\/]') { throw 'Set policy_approval to an absolute path.' }
                $zrsArgs += @('--policy-approval', $cfg.policy_approval)
            }
        }
    }
}
& $python @zrsArgs
if ($LASTEXITCODE -ne 0) { throw "ZRS $Action failed. Review the reason above." }
