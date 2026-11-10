# Optional: set up OCR (Tesseract with English, French and Japanese) for the CCIFJ Concierge on Windows.
#
# You can skip this. The chat, the agents and the dashboard work without OCR; it is only used to
# read scanned PDFs and images. Text PDFs, Word files and text files never need it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_ocr.ps1          install what is missing, then check
#   powershell -ExecutionPolicy Bypass -File scripts\install_ocr.ps1 -Check   only report; change nothing
#
# What it does:
#   1. Installs the Tesseract program with winget, Chocolatey or Scoop, whichever is available.
#   2. If the three languages are not available, downloads their files from the official
#      tesseract-ocr/tessdata_fast repository into var\tessdata inside this project. That needs no
#      admin rights; the concierge looks there by itself.
#   3. Checks the result. If a step cannot be done, it says what to do by hand and why.
#
# The PATH does not need changing: the concierge also looks in the usual install folders.
# It never changes the concierge itself, and it is safe to run again.

param([switch]$Check)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$TessData = Join-Path $Root 'var\tessdata'
$Languages = @('eng', 'fra', 'jpn')
$DataUrl = 'https://github.com/tesseract-ocr/tessdata_fast/raw/main'

function Say($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "note: $text" -ForegroundColor Yellow }
function OptionalNote {
    Write-Host ''
    Write-Host 'OCR is optional. The concierge works without it; only scanned PDFs and images are not read.'
}

function Find-Tesseract {
    $onPath = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $places = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramW6432)) {
        if ($base) { $places += (Join-Path $base 'Tesseract-OCR\tesseract.exe') }
    }
    if ($env:LOCALAPPDATA) { $places += (Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR\tesseract.exe') }
    if ($env:USERPROFILE) { $places += (Join-Path $env:USERPROFILE 'scoop\shims\tesseract.exe') }
    foreach ($place in $places) { if (Test-Path $place) { return $place } }
    return $null
}

function Test-LocalLanguages {
    foreach ($language in $Languages) {
        if (-not (Test-Path (Join-Path $TessData "$language.traineddata"))) { return $false }
    }
    return $true
}

function Get-MissingLanguages($tesseract) {
    $previous = $env:TESSDATA_PREFIX
    if (Test-LocalLanguages) { $env:TESSDATA_PREFIX = $TessData }
    try { $listed = & $tesseract --list-langs 2>&1 | ForEach-Object { "$_".Trim() } }
    catch { $listed = @() }
    finally { $env:TESSDATA_PREFIX = $previous }
    return @($Languages | Where-Object { $listed -notcontains $_ })
}

function Show-Report {
    $tesseract = Find-Tesseract
    if (-not $tesseract) {
        Write-Host 'Tesseract: not installed'
        return $false
    }
    $version = (& $tesseract --version 2>&1 | Select-Object -First 1)
    Write-Host "Tesseract: $version ($tesseract)"
    $missing = Get-MissingLanguages $tesseract
    if ($missing.Count -eq 0) {
        Write-Host 'Languages: English, French and Japanese are available'
        return $true
    }
    Write-Host "Languages missing: $($missing -join ', ')"
    return $false
}

function Install-Program {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say 'Installing Tesseract with winget'
        winget install --id UB-Mannheim.TesseractOCR -e --silent --accept-package-agreements --accept-source-agreements
    }
    elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        Say 'Installing Tesseract with Chocolatey'
        choco install tesseract -y
    }
    elseif (Get-Command scoop -ErrorAction SilentlyContinue) {
        Say 'Installing Tesseract with Scoop'
        scoop install tesseract
    }
    else {
        Note 'No package manager (winget, Chocolatey or Scoop) was found.'
        Note 'Download the installer from https://github.com/UB-Mannheim/tesseract/wiki, run it, then run this script again.'
        return $false
    }
    return [bool](Find-Tesseract)
}

function Install-Languages {
    Say 'Downloading English, French and Japanese into var\tessdata (under 10 MB, no admin rights needed)'
    try {
        New-Item -ItemType Directory -Force $TessData | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        foreach ($language in $Languages) {
            $target = Join-Path $TessData "$language.traineddata"
            if ((Test-Path $target) -and ((Get-Item $target).Length -gt 0)) { continue }
            $part = "$target.part"
            Invoke-WebRequest -Uri "$DataUrl/$language.traineddata" -OutFile $part -UseBasicParsing -ErrorAction Stop
            Move-Item -Force $part $target
        }
        return $true
    }
    catch {
        Note "Could not download the language files: $($_.Exception.Message)"
        Note "Check the internet connection, or download eng, fra and jpn .traineddata from $DataUrl and put them in $TessData"
        return $false
    }
}

if ($Check) {
    if (Show-Report) { exit 0 }
    OptionalNote
    exit 1
}

if (Show-Report) {
    Write-Host 'Nothing to do: OCR is ready.'
    exit 0
}

if (-not (Find-Tesseract)) {
    if (-not (Install-Program)) {
        Note 'Tesseract could not be installed automatically, or needs a new terminal to be found.'
        OptionalNote
        exit 1
    }
}

$tesseract = Find-Tesseract
if ((Get-MissingLanguages $tesseract).Count -gt 0) {
    if (-not (Install-Languages)) { OptionalNote; exit 1 }
}

Write-Host ''
if (Show-Report) {
    Say 'OCR is ready. Restart the concierge if it is running.'
    exit 0
}
Note 'OCR is still not complete; see the lines above.'
OptionalNote
exit 1
