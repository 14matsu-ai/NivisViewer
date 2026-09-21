[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$RunTests,
    [switch]$RunSmoke,
    [switch]$CreateZip,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildDir = Join-Path $RepoRoot "build"
$DistDir = Join-Path $RepoRoot "dist"
$BundleDir = Join-Path $DistDir "NivisViewer"
$Python = Join-Path $RepoRoot ".venv311\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = "python"
}

$PreviousBuildPath = $env:PATH
Push-Location $RepoRoot
try {
    $Architecture = & $Python -c "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{platform.architecture()[0]}')"
    if ($LASTEXITCODE -ne 0 -or $Architecture.Trim() -ne "3.11|64bit") {
        throw "Python 3.11 x64 is required. Detected: $Architecture"
    }
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
            if ($ResolvedParent -ne $RepoRoot) {
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
    if ($RunTests) {
        & $Python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
    }
    & $Python scripts\collect_licenses.py --output licenses
    if ($LASTEXITCODE -ne 0) { throw "License collection failed." }
    & $Python -m PyInstaller --noconfirm NivisViewer.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    foreach ($Name in @("portable.flag", "LICENSE", "PROJECT_LICENSE.md", "THIRD_PARTY_NOTICES.md", "README.md", "README.en.md", "README.zh-CN.md", "README.zh-TW.md")) {
        Copy-Item -LiteralPath (Join-Path $RepoRoot $Name) -Destination $BundleDir -Force
    }
    Copy-Item -LiteralPath (Join-Path $RepoRoot "licenses") -Destination $BundleDir -Recurse -Force

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
    Pop-Location
}
