[CmdletBinding()]
param(
    [string]$PythonPath = "python",
    [string]$Wheelhouse = "",
    [string]$Constraints = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvironmentDir = Join-Path $RepoRoot ".venv313"
$Policy = Join-Path $PSScriptRoot "python_runtime_policy.py"
$ResolvedPython = (Get-Command $PythonPath -ErrorAction Stop).Source
& $ResolvedPython $Policy
if ($LASTEXITCODE -ne 0) { throw "Select a standard Python 3.13.16+ x64 interpreter explicitly with -PythonPath." }
if (Test-Path -LiteralPath $EnvironmentDir) {
    throw "Environment already exists; preserving it: $EnvironmentDir"
}
& $ResolvedPython -m venv $EnvironmentDir
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
$EnvironmentPython = Join-Path $EnvironmentDir "Scripts\python.exe"
& $EnvironmentPython $Policy --require-venv
if ($LASTEXITCODE -ne 0) { throw "New runtime verification failed." }
$InstallArguments = @("-m", "pip", "install", "--only-binary=:all:", "-r", (Join-Path $RepoRoot "requirements-release.txt"), "-r", (Join-Path $RepoRoot "requirements-dev.txt"))
if ($Wheelhouse) { $InstallArguments += @("--no-index", "--find-links", $Wheelhouse) }
if ($Constraints) { $InstallArguments += @("-c", $Constraints) }
& $EnvironmentPython @InstallArguments
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed; the new environment was retained for inspection." }
& $EnvironmentPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency verification failed." }
Write-Host "Independent environment: $EnvironmentDir"
