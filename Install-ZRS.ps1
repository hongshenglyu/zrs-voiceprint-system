[CmdletBinding()]
param([string]$WheelDirectory)
$ErrorActionPreference = 'Stop'
$versionCheck = 'import struct,sys; sys.exit(0 if sys.version_info[:3] == (3,11,6) and struct.calcsize(chr(80)) == 8 else 1)'
& py -3.11 -c $versionCheck
if ($LASTEXITCODE -ne 0) { throw 'Install Windows x64 Python 3.11.6 with the py launcher.' }
$venv = Join-Path $PSScriptRoot '.venv'
& py -3.11 -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the ZRS virtual environment.' }
$python = Join-Path $venv 'Scripts/python.exe'
$installArgs = @('--disable-pip-version-check', '--only-binary=:all:', '-r', (Join-Path $PSScriptRoot 'requirements-zrs.txt'))
if ($WheelDirectory) {
    $installArgs += @('--no-index', '--find-links', (Resolve-Path -LiteralPath $WheelDirectory).Path)
}
& $python -m pip install @installArgs
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Review the pip output above.' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Installed dependencies are inconsistent.' }
& $python -B (Join-Path $PSScriptRoot 'tools/zrs_study.py') --check-install
if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }
