& {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = "Stop"

    $BundleName = @@BUNDLE_NAME_LITERAL@@
    $BundlePath = ""  # Optional: paste the full ZIP path here when it is not in a standard folder.
    $RepositoryName = @@REPOSITORY_NAME_LITERAL@@
    $Branch = @@BRANCH_LITERAL@@
    $CommitMessage = @@COMMIT_MESSAGE_LITERAL@@
    $Visibility = @@VISIBILITY_LITERAL@@

    if ([string]::IsNullOrWhiteSpace($RepositoryName)) {
        throw "Enter a GitHub repository before running this deployment block."
    }

    foreach ($CommandName in @("git", "gh")) {
        if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
            throw "Required command '$CommandName' was not found in PATH."
        }
    }

    $DownloadsDirectory = Join-Path $env:USERPROFILE "Downloads"
    $DocumentsDirectory = [Environment]::GetFolderPath("MyDocuments")
    if ([string]::IsNullOrWhiteSpace($DocumentsDirectory)) {
        $DocumentsDirectory = Join-Path $env:USERPROFILE "Documents"
    }
    $BundleStem = [System.IO.Path]::GetFileNameWithoutExtension($BundleName)
    $BundleNamePattern = "^" + [Regex]::Escape($BundleStem) + "( \([0-9]+\))?\.zip$"

    $SearchDirectories = @(
        $DownloadsDirectory
        if ($env:OneDrive) { Join-Path $env:OneDrive "Downloads" }
        $DocumentsDirectory
        (Get-Location).Path
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } | Select-Object -Unique

    $ZipCandidate = $null
    if (-not [string]::IsNullOrWhiteSpace($BundlePath)) {
        $ExpandedBundlePath = [Environment]::ExpandEnvironmentVariables($BundlePath)
        if (-not (Test-Path -LiteralPath $ExpandedBundlePath -PathType Leaf)) {
            throw "The deployment bundle path does not exist: $ExpandedBundlePath"
        }
        $ZipCandidate = Get-Item -LiteralPath $ExpandedBundlePath
        if ($ZipCandidate.Extension -ne ".zip") {
            throw "The deployment bundle must be a ZIP file: $ExpandedBundlePath"
        }
    } else {
        $ZipCandidate = @($SearchDirectories | ForEach-Object {
            Get-ChildItem -LiteralPath $_ -Filter "$BundleStem*.zip" -File -ErrorAction SilentlyContinue
        }) |
            Where-Object { $_.Name -match $BundleNamePattern } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
    }

    if (-not $ZipCandidate) {
        $ShadeBundles = @($SearchDirectories | ForEach-Object {
            Get-ChildItem -LiteralPath $_ -Filter "*shade*.zip" -File -ErrorAction SilentlyContinue
        } | Sort-Object LastWriteTime -Descending)
        $Available = if ($ShadeBundles.Count) {
            ($ShadeBundles.FullName -join ", ")
        } else {
            "none"
        }
        $Searched = $SearchDirectories -join ", "
        throw "Could not find '$BundleName' or a numbered browser copy. Click 'Download website package' first. Searched: $Searched. Available shade ZIP files: $Available. To use another folder, set `$BundlePath at the top of this block."
    }

    $ZipPath = $ZipCandidate.FullName
    Write-Host "Using newest deployment bundle: $($ZipCandidate.Name)"

    $BundleBaseName = [System.IO.Path]::GetFileNameWithoutExtension($ZipPath)
    $ExtractTo = Join-Path $DocumentsDirectory $BundleBaseName
    if (Test-Path -LiteralPath $ExtractTo) {
        $ExtractTo = Join-Path $DocumentsDirectory ($BundleBaseName + "-deploy-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    }

    Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractTo -Force
    $DeployScript = Get-ChildItem -LiteralPath $ExtractTo -Filter "deploy_to_github.ps1" -File -Recurse |
        Select-Object -First 1
    if (-not $DeployScript) {
        throw "The ZIP was extracted to '$ExtractTo', but deploy_to_github.ps1 was not found anywhere inside it."
    }

    Push-Location -LiteralPath $DeployScript.Directory.FullName
    try {
        gh auth status
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub CLI authentication failed. Run 'gh auth login' and retry."
        }
@@REPOSITORY_VERIFICATION@@@@PUBLISH_COMMAND@@        if ($LASTEXITCODE -ne 0) {
            throw "deploy_to_github.ps1 exited with code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}
