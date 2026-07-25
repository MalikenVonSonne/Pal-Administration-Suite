param(
    [string]$PythonPath = $env:PALADMIN_PYTHON,
    [string]$PalsavPath = $env:PALADMIN_PALSAV,
    [string]$DistPath = "",
    [string]$WorkPath = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = (Get-Command python.exe -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -and $_.Source -notlike "*\WindowsApps\*" } |
        Select-Object -First 1 -ExpandProperty Source)
}
if (-not $PalsavPath) {
    $PalsavPath = Join-Path $root "third_party\palsav-flex"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python was not found. Set PALADMIN_PYTHON or pass -PythonPath."
}
if (-not (Test-Path -LiteralPath $PalsavPath -PathType Container)) {
    throw "The palsav package was not found at '$PalsavPath'. Set PALADMIN_PALSAV or pass -PalsavPath."
}
$catalog = Join-Path $root "data\catalogs\palworld-1.0-db.json"
if (-not (Test-Path -LiteralPath $catalog -PathType Leaf)) {
    throw "The Palworld 1.0 catalog was not found at '$catalog'."
}

$specPath = Join-Path $root "build\PalAdmin.spec"
if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
    throw "The accepted primary PyInstaller specification was not found at '$specPath'."
}
if (-not $DistPath) {
    $DistPath = Join-Path $root "build\dist"
}
if (-not $WorkPath) {
    $WorkPath = Join-Path $root "build\work"
}
if (-not $LogPath) {
    $LogPath = Join-Path $root "build\logs\primary-build.log"
}
$logParent = Split-Path -Parent $LogPath
New-Item -ItemType Directory -Force -Path $logParent | Out-Null

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    $specPath
)

# PyInstaller writes normal warnings to stderr. Capture both streams as a raw
# build log and make the native process exit code the only success criterion.
$savedPreference = $ErrorActionPreference
$savedPalsavEnvironment = $env:PALADMIN_PALSAV
try {
    $env:PALADMIN_PALSAV = $PalsavPath
    $ErrorActionPreference = "Continue"
    & $PythonPath @pyinstallerArgs 2>&1 | Tee-Object -FilePath $LogPath
    $buildExitCode = $LASTEXITCODE
}
finally {
    if ($null -eq $savedPalsavEnvironment) {
        Remove-Item Env:PALADMIN_PALSAV -ErrorAction SilentlyContinue
    }
    else {
        $env:PALADMIN_PALSAV = $savedPalsavEnvironment
    }
    $ErrorActionPreference = $savedPreference
}
if ($buildExitCode -ne 0) {
    throw "Pal Admin packaging failed with exit code $buildExitCode. See '$LogPath'."
}

$executable = Join-Path (Join-Path $DistPath "PalAdmin") "PalAdmin.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "PyInstaller reported success but the expected executable was not found at '$executable'."
}

Write-Output $executable
