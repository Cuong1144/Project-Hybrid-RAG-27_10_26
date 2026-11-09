# Run from an interactive Windows desktop using PowerShell 7 and installed Word.
# Input is an immutable snapshot. The resulting PDF stays in staging for review.
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $InputFile,
    [Parameter(Mandatory)] [string] $OutputPdf
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-LocalPath([string] $Path) {
    [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path))
}

function Test-UnderPath([string] $Path, [string] $Root) {
    $Path.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparsePoints([string] $Path) {
    while ($Path) {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Reparse point is not allowed: $Path"
        }
        $Path = [IO.Path]::GetDirectoryName($Path)
    }
}

if (-not $IsWindows -or [Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') {
    throw 'Run this script with pwsh -NoProfile -STA -File on Windows.'
}

$dataRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\data'))
$snapshotsRoot = Join-Path $dataRoot 'snapshots'
$stagingRoot = Join-Path $dataRoot 'staging'
$sourcePath = Get-LocalPath $InputFile
$targetPath = Get-LocalPath $OutputPdf
$snapshotFolder = [IO.Path]::GetDirectoryName($sourcePath)
$snapshotHash = [IO.Path]::GetFileName($snapshotFolder)

if (-not (Test-UnderPath $sourcePath $snapshotsRoot) -or
    [IO.Path]::GetDirectoryName($snapshotFolder) -ine $snapshotsRoot -or
    $snapshotHash -cnotmatch '^[0-9a-f]{64}$' -or
    [IO.Path]::GetExtension($sourcePath) -inotmatch '^\.docx?$' -or
    -not [IO.File]::Exists($sourcePath)) {
    throw 'InputFile must be a DOC or DOCX snapshot under data/snapshots/<sha256>/.'
}
if (-not (Test-UnderPath $targetPath $stagingRoot) -or [IO.Path]::GetExtension($targetPath) -ine '.pdf') {
    throw 'OutputPdf must be a PDF under data/staging/.'
}
Assert-NoReparsePoints $sourcePath
Assert-NoReparsePoints $targetPath
if (Test-Path -LiteralPath $targetPath) { throw 'OutputPdf already exists.' }
if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash -ine $snapshotHash) {
    throw 'Word snapshot checksum mismatch.'
}

$targetFolder = [IO.Path]::GetDirectoryName($targetPath)
[void][IO.Directory]::CreateDirectory($targetFolder)
$pendingPdf = Join-Path $targetFolder ('.word-' + [guid]::NewGuid().ToString('N') + '.tmp.pdf')
$word = $options = $documents = $document = $null
$ownsWord = $false
$saved = [ordered]@{}

try {
    try {
        # Word is a single-use COM server. Do not attach to a running Word instance.
        $word = New-Object -ComObject Word.Application
        $documents = $word.Documents
        if ($word.Visible -or $documents.Count -ne 0) {
            throw 'Word did not provide a fresh hidden automation instance.'
        }
        $ownsWord = $true
        $options = $word.Options
        $saved.security = $word.AutomationSecurity
        $saved.openLinks = $options.UpdateLinksAtOpen
        $saved.printLinks = $options.UpdateLinksAtPrint
        $saved.printFields = $options.UpdateFieldsAtPrint
        $word.AutomationSecurity = 3 # msoAutomationSecurityForceDisable
        $word.DisplayAlerts = 0
        $options.UpdateLinksAtOpen = $false
        $options.UpdateLinksAtPrint = $false
        $options.UpdateFieldsAtPrint = $false
        $rendererVersion = [string]$word.Version

        # Explicit dummy passwords make protected documents fail instead of asking for input.
        $unusablePassword = [guid]::NewGuid().ToString('N').Substring(0, 12)
        $document = $documents.OpenNoRepairDialog(
            $sourcePath, $false, $true, $false, $unusablePassword, $unusablePassword,
            $false, $unusablePassword, $unusablePassword, 0, 65001, $false, $false, 0, $true
        )
        # PDF, whole document, print optimization, retain IRM, no automatic viewer.
        $document.ExportAsFixedFormat($pendingPdf, 17, $false, 0, 0, 1, 1, 0, $false, $true, 0, $true, $true, $false)
    }
    finally {
        try {
            if ($null -ne $document) { $document.Close(0) }
        }
        finally {
            try {
                if ($ownsWord) {
                    try {
                        if ($saved.Contains('security')) { $word.AutomationSecurity = $saved.security }
                        if ($saved.Contains('openLinks')) { $options.UpdateLinksAtOpen = $saved.openLinks }
                        if ($saved.Contains('printLinks')) { $options.UpdateLinksAtPrint = $saved.printLinks }
                        if ($saved.Contains('printFields')) { $options.UpdateFieldsAtPrint = $saved.printFields }
                    }
                    finally { $word.Quit(0) }
                }
            }
            finally {
                foreach ($object in @($document, $documents, $options, $word)) {
                    if ($null -ne $object) {
                        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($object)
                    }
                }
            }
        }
    }

    $header = [byte[]]::new(5)
    $stream = [IO.File]::OpenRead($pendingPdf)
    try {
        if ($stream.Read($header, 0, 5) -ne 5 -or [Text.Encoding]::ASCII.GetString($header) -cne '%PDF-') {
            throw 'Word did not produce a PDF.'
        }
    }
    finally { $stream.Dispose() }
    if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash -ine $snapshotHash) {
        throw 'Word snapshot changed during conversion.'
    }

    $pdfHash = (Get-FileHash -LiteralPath $pendingPdf -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::Move($pendingPdf, $targetPath) # Atomic on the same volume and refuses overwrite.
    [ordered]@{
        status = 'needs_review'
        source_sha256 = $snapshotHash
        reference_sha256 = $pdfHash
        reference_path = [IO.Path]::GetRelativePath($dataRoot, $targetPath).Replace('\', '/')
        renderer = 'Microsoft Word'
        renderer_version = $rendererVersion
    } | ConvertTo-Json -Compress
}
finally {
    if ([IO.File]::Exists($pendingPdf)) { Remove-Item -LiteralPath $pendingPdf }
}
