[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$RunTests,
    [switch]$RunSmoke,
    [switch]$CreateZip,
    [switch]$Force,
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]+$')]
    [string]$OutputName = ("candidate-python313-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildRoot = Join-Path $RepoRoot "build"
$DistRoot = Join-Path $RepoRoot "dist"
$BuildDir = Join-Path $BuildRoot $OutputName
$DistDir = Join-Path $DistRoot $OutputName
$BundleDir = Join-Path $DistDir "NivisViewer"
$Python = Join-Path $RepoRoot ".venv313\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Independent .venv313 runtime not found. Run scripts\setup_python.ps1 first."
}

$PreviousBuildPath = $env:PATH
$PreviousLicensesDir = $env:NIVIS_LICENSES_DIR
Push-Location $RepoRoot
try {
    $RuntimeDetails = & $Python scripts\python_runtime_policy.py --require-venv --release-dependencies requirements-release.txt
    if ($LASTEXITCODE -ne 0) { throw "Build runtime does not meet the Python 3.13.16+ x64 baseline." }
    & $Python -c "import PyInstaller; print(PyInstaller.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is missing. Run: python -m pip install -r requirements-build.txt"
    }
    $ResolvedPython = (Get-Command $Python -ErrorAction Stop).Source
    $PythonBase = & $ResolvedPython -c "import sys; print(sys.base_prefix)"
    if ($LASTEXITCODE -ne 0) { throw "Cannot resolve Python runtime directory." }
    $Python = $ResolvedPython
    # Ambient toolchains (for example Poppler) may supply an incompatible ICU
    # under the same DLL name as Windows. Do not let Analysis collect those.
    $env:PATH = @(
        (Split-Path -Parent $Python),
        $PythonBase.Trim(),
        (Join-Path $PythonBase.Trim() "DLLs"),
        (Join-Path $env:SystemRoot "System32"),
        $env:SystemRoot
    ) -join ";"
    if ($Clean) {
        foreach ($Target in @($BuildDir, $DistDir)) {
            $ResolvedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $Target))
            if ($ResolvedParent -ne $BuildRoot -and $ResolvedParent -ne $DistRoot) {
                throw "Refusing to clean outside repository: $Target"
            }
            if (Test-Path -LiteralPath $Target) {
                Remove-Item -LiteralPath $Target -Recurse -Force
            }
        }
    }
    elseif ((Test-Path -LiteralPath $BundleDir) -and -not $Force) {
        throw "Build output already exists. Use -Clean or explicitly use -Force."
    }
    New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
    $RuntimeDetails | Set-Content -LiteralPath (Join-Path $BuildDir "runtime.json") -Encoding utf8
    & $Python -m pip freeze --all | Set-Content -LiteralPath (Join-Path $BuildDir "environment-lock.txt") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Environment inventory failed." }
    if ($RunTests) {
        & $Python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
    }
    $ReleaseLicensesDir = Join-Path $BuildDir ("licenses-" + [guid]::NewGuid().ToString("N"))
    & $Python scripts\collect_licenses.py --output $ReleaseLicensesDir
    if ($LASTEXITCODE -ne 0) { throw "License collection failed." }
    $env:NIVIS_LICENSES_DIR = $ReleaseLicensesDir
    & $Python -m PyInstaller --noconfirm --workpath $BuildDir --distpath $DistDir NivisViewer.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    foreach ($Name in @("portable.flag", "LICENSE", "PROJECT_LICENSE.md", "THIRD_PARTY_NOTICES.md", "README.md")) {
        Copy-Item -LiteralPath (Join-Path $RepoRoot $Name) -Destination $BundleDir -Force
    }

    $SmokeResult = Join-Path $BuildDir "frozen-smoke.json"
    if ($RunSmoke) {
        $SmokeProfile = Join-Path $BuildDir "smoke-profile"
        $PreviousQpa = $env:QT_QPA_PLATFORM
        $env:QT_QPA_PLATFORM = "offscreen"
        try {
            $SmokeProcess = Start-Process `
                -FilePath (Join-Path $BundleDir "NivisViewer.exe") `
                -ArgumentList @("--no-single-instance", "--profile-dir", "`"$SmokeProfile`"", "--smoke-test-output", "`"$SmokeResult`"") `
                -WindowStyle Hidden `
                -Wait `
                -PassThru
            if ($SmokeProcess.ExitCode -ne 0) {
                throw "Frozen smoke failed with exit code $($SmokeProcess.ExitCode)."
            }
            if (-not (Test-Path -LiteralPath $SmokeResult -PathType Leaf)) {
                throw "Frozen smoke did not create its result JSON."
            }
        }
        finally {
            $env:QT_QPA_PLATFORM = $PreviousQpa
        }
    }
    $VerifyArguments = @("scripts\verify_portable_build.py", $BundleDir)
    $RuntimeVersion = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    if ($LASTEXITCODE -ne 0) { throw "Cannot identify build runtime version." }
    $VerifyArguments += @("--expected-python", $RuntimeVersion.Trim(), "--native-report", (Join-Path $BuildDir "native-audit.json"))
    if ($RunSmoke) { $VerifyArguments += @("--smoke-result", $SmokeResult) }
    & $Python @VerifyArguments
    if ($LASTEXITCODE -ne 0) { throw "Portable bundle verification failed." }

    $Version = & $Python -c "from app.version import __version__; print(__version__)"
    if ($CreateZip) {
        $ZipPath = Join-Path $DistDir "NivisViewer_portable_win64_$Version.zip"
        $PackageArguments = @("scripts\package_portable.py", $BundleDir, $ZipPath)
        if ($Clean -or $Force) { $PackageArguments += "--force" }
        & $Python @PackageArguments
        if ($LASTEXITCODE -ne 0) { throw "Packaging failed." }
        Write-Host "ZIP: $ZipPath"
        Write-Host "SHA-256: $ZipPath.sha256"
    }
    Write-Host "Portable build: $BundleDir"
}
finally {
    $env:PATH = $PreviousBuildPath
    $env:NIVIS_LICENSES_DIR = $PreviousLicensesDir
    Pop-Location
}
