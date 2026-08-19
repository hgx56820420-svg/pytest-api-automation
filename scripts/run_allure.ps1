Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$java = Get-ChildItem -Path (Join-Path $projectRoot ".tools") -Filter java.exe -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($null -eq $java) {
    $java = Get-Command java.exe -ErrorAction SilentlyContinue
}

if ($null -eq $java) {
    throw "Java was not found. Install Java 21 or place a JRE under .tools before running this script."
}

$env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $java.Source)
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$allure = Join-Path $projectRoot "node_modules\.bin\allure.cmd"
$results = Join-Path $projectRoot "allure-results"
$report = Join-Path $projectRoot "allure-report"

if (-not (Test-Path $python)) {
    throw "Virtual environment not found: $python"
}

if (-not (Test-Path $allure)) {
    throw "Allure command not found. Run npm install first."
}

Remove-Item -LiteralPath $results, $report -Recurse -Force -ErrorAction SilentlyContinue

& $python -m pytest tests/ --alluredir $results -v
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $allure generate $results --clean -o $report
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Start-Process -FilePath $allure -ArgumentList @("open", $report) -WorkingDirectory $projectRoot
Write-Host "Allure report generated at $report and opened in a browser."
