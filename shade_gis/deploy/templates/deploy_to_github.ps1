param(
    [Alias("TargetRepo")]
    [string]$RepositoryName = @@REPOSITORY_NAME_LITERAL@@,
    [string]$Visibility = "private",
    [ValidateSet("create", "existing")]
    [string]$Mode = "create",
    [string]$RepositoryUrl = "",
    [string]$Branch = "main",
    [string]$CommitMessage = @@COMMIT_MESSAGE_LITERAL@@,
    [switch]$Yes,
    [switch]$AllowPublicTarget
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or not on PATH."
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI is not installed or not on PATH."
}

function Invoke-Native {
    param(
        [string]$Command,
        [string[]]$Arguments
    )
    & $Command @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Invoke-NativeOutput {
    param(
        [string]$Command,
        [string[]]$Arguments
    )
    $output = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
    return $output
}

function Assert-DeploymentBundle {
    $manifestPath = Join-Path (Get-Location) "deployment_manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "deployment_manifest.json is missing. Download a fresh deployment package from Shade-GIS."
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    } catch {
        throw "deployment_manifest.json is invalid: $($_.Exception.Message)"
    }
    if ([int]$manifest.schema_version -ne 1) {
        throw "Unsupported deployment manifest version '$($manifest.schema_version)'. Download a fresh package."
    }
    $expectedRepository = ($RepositoryName.Trim() -replace "\.git$", "")
    if ([string]$manifest.repository -ne $expectedRepository) {
        throw "This bundle targets '$($manifest.repository)', not '$expectedRepository'. Download a package for the selected repository."
    }
    if ([string]$manifest.deploy_mode -ne $Mode) {
        throw "This bundle was created for '$($manifest.deploy_mode)' mode, not '$Mode'. Download a matching package."
    }
    if ([string]$manifest.commit_message -ne $CommitMessage) {
        throw "This bundle was created with a different commit message. Download a package using the current deployment settings."
    }
    $requiredFiles = @(
        "app.py", "public_voting.py", "shade_study_stops.csv",
        "shade_study_config.json", "requirements.txt", "README.md",
        "deploy_to_github.ps1", ".gitignore", ".streamlit/config.toml",
        ".streamlit/secrets.toml.example", ".env.example",
        "migrations/001_public_voting.sql",
        "migrations/least_privilege_roles.sql.example",
        "scripts/verify_database.py", "scripts/migrate_database.py",
        "DEPLOYMENT.md", "static/shade_gis_identity.json"
    )
    foreach ($requiredFile in $requiredFiles) {
        if (-not $manifest.files.PSObject.Properties[$requiredFile]) {
            throw "The deployment package manifest is incomplete; '$requiredFile' is not content-addressed."
        }
    }
    foreach ($fileProperty in $manifest.files.PSObject.Properties) {
        $relativePath = [string]$fileProperty.Name
        if ([IO.Path]::IsPathRooted($relativePath) -or $relativePath -match '(^|[\/])\.\.([\/]|$)') {
            throw "Unsafe file path in deployment manifest: $relativePath"
        }
        if (-not (Test-Path -LiteralPath $relativePath -PathType Leaf)) {
            throw "The deployment package is incomplete; '$relativePath' is missing."
        }
        $actualHash = (Get-FileHash -LiteralPath $relativePath -Algorithm SHA256).Hash.ToLowerInvariant()
        $expectedHash = ([string]$fileProperty.Value).ToLowerInvariant()
        if ($actualHash -ne $expectedHash) {
            throw "The deployment package is stale or damaged; '$relativePath' does not match its manifest hash."
        }
    }
    if (-not (Test-StreamlitStaticServing -ConfigPath ".streamlit/config.toml")) {
        throw "The deployment package must enable Streamlit static file serving."
    }
    if ([string]$manifest.bundle_id -notmatch '^[0-9a-f]{64}$') {
        throw "The deployment package has an invalid bundle identity."
    }
    $identityJson = [string]$manifest.identity_json
    if (-not $identityJson.Trim()) {
        throw "The deployment package has no canonical bundle identity."
    }
    try {
        $identity = $identityJson | ConvertFrom-Json
    } catch {
        throw "The deployment package canonical bundle identity is invalid: $($_.Exception.Message)"
    }
    foreach ($propertyName in @("schema_version", "study_id", "project_name", "repository", "deploy_mode", "commit_message", "entrypoint")) {
        if ([string]$identity.$propertyName -ne [string]$manifest.$propertyName) {
            throw "The deployment package canonical identity does not match '$propertyName'."
        }
    }
    if (
        [string]$identity.dataset.file -ne [string]$manifest.dataset.file -or
        [string]$identity.dataset.rows -ne [string]$manifest.dataset.rows -or
        [string]$identity.dataset.sha256 -ne [string]$manifest.dataset.sha256 -or
        (@($identity.dataset.columns) -join "`n") -ne (@($manifest.dataset.columns) -join "`n")
    ) {
        throw "The deployment package canonical identity does not match its dataset."
    }
    foreach ($fileProperty in $identity.files.PSObject.Properties) {
        $manifestFile = $manifest.files.PSObject.Properties[[string]$fileProperty.Name]
        if ($null -eq $manifestFile -or [string]$manifestFile.Value -ne [string]$fileProperty.Value) {
            throw "The deployment package canonical identity does not match file '$($fileProperty.Name)'."
        }
    }
    $identityFileCount = @($identity.files.PSObject.Properties).Count
    $manifestIdentityFileCount = @($manifest.files.PSObject.Properties | Where-Object Name -ne "README.md").Count
    if ($identityFileCount -ne $manifestIdentityFileCount) {
        throw "The deployment package canonical identity has a different file set."
    }
    $identityBytes = [Text.Encoding]::UTF8.GetBytes($identityJson)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $calculatedBundleId = ([BitConverter]::ToString($sha256.ComputeHash($identityBytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
    if ($calculatedBundleId -ne [string]$manifest.bundle_id) {
        throw "The deployment package bundle identity does not match its manifest."
    }
    Write-Host "Validated deployment bundle $($manifest.bundle_id) for $($manifest.repository)."
    Write-Host "Project snapshot: $($manifest.project_name) [$($manifest.study_id)]"
}

function Get-RemoteUrl {
    if ($RepositoryUrl.Trim()) {
        return $RepositoryUrl.Trim()
    }
    if ($RepositoryName -match "^https?://") {
        return $RepositoryName
    }
    return "https://github.com/$RepositoryName.git"
}

function Get-RepositorySlugFromValue {
    param(
        [string]$Candidate,
        [switch]$AllowBare
    )
    $candidate = $Candidate.Trim()
    if ($candidate -match "^(?:(?:https?|ssh)://(?:[^@/]+@)?|git@)?github\.com[:/](?<owner>[A-Za-z0-9_.-]+)/(?<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$") {
        return "$($Matches.owner)/$($Matches.repo)"
    }
    if ($AllowBare -and $candidate -match "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$") {
        return ($candidate -replace "\.git$", "")
    }
    return ""
}

function Get-RepositorySlug {
    if ($RepositoryUrl.Trim()) {
        return Get-RepositorySlugFromValue -Candidate $RepositoryUrl
    }
    return Get-RepositorySlugFromValue -Candidate $RepositoryName -AllowBare
}

function Assert-PrivateExistingRepository {
    $expectedSlug = Get-RepositorySlugFromValue -Candidate $RepositoryName -AllowBare
    if (-not $expectedSlug) {
        throw "RepositoryName must identify one GitHub repository as OWNER/REPO."
    }
    $repoSlug = Get-RepositorySlug
    if (-not $repoSlug) {
        throw "RepositoryUrl must identify a GitHub repository when it is provided."
    }
    if ($repoSlug -ne $expectedSlug) {
        throw "RepositoryUrl targets '$repoSlug', but this bundle is configured for '$expectedSlug'."
    }
    try {
        $repoVisibility = (Invoke-NativeOutput "gh" @("repo", "view", $repoSlug, "--json", "visibility", "--jq", ".visibility") | Out-String).Trim().ToLowerInvariant()
    } catch {
        throw "Could not access GitHub repository '$repoSlug'. Confirm the OWNER/REPO spelling, that the repository exists, and that 'gh auth status' is authenticated to an account with access. Original error: $($_.Exception.Message)"
    }
    if ($repoVisibility -ne "private" -and -not $AllowPublicTarget) {
        throw "Target repository $repoSlug is '$repoVisibility'. Re-run with a private repository or add -AllowPublicTarget to publish there intentionally."
    }
    Write-Host "Verified target repository visibility: $repoVisibility"
}

function Confirm-Publish {
    param([string]$Message)
    if ($Yes) {
        return
    }
    $answer = Read-Host "$Message Type PUBLISH to continue"
    if ($answer -ne "PUBLISH") {
        throw "Publishing cancelled."
    }
}

function Show-ProtectedFileWarnings {
    $protectedPaths = @(
        ".git",
        ".github",
        "README.md",
        "LICENSE",
        ".env",
        "secrets.toml",
        ".streamlit/secrets.toml"
    )
    foreach ($path in $protectedPaths) {
        if (Test-Path $path) {
            Write-Host "Protected file will not be copied in existing-repository mode: $path"
        }
    }
    Get-ChildItem -Path . -Force -File -Filter ".env.*" | ForEach-Object {
        Write-Host "Protected file will not be copied in existing-repository mode: $($_.Name)"
    }
}

function Test-LegacyRootPublishedApp {
    param([string]$Destination)
    $rootApp = Join-Path $Destination "app.py"
    if (-not (Test-Path -LiteralPath $rootApp -PathType Leaf)) {
        return $false
    }
    $source = Get-Content -LiteralPath $rootApp -Raw
    if ($source -match '(?m)^\s*(from\s+builder_app\s+import|import\s+builder_app\b)' -or $source -match 'builder_app\.main') {
        return $false
    }
    if (-not $source.Contains("shade_study_config.json") -or -not $source.Contains("shade_study_stops.csv")) {
        return $false
    }
    $manifestPaths = @(
        (Join-Path $Destination "deployment_manifest.json"),
        (Join-Path $Destination "@@PREVIEW_DIRECTORY@@/deployment_manifest.json")
    )
    foreach ($manifestPath in $manifestPaths) {
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            continue
        }
        try {
            $candidate = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
            $appHash = $candidate.files.PSObject.Properties["app.py"]
            if (
                [int]$candidate.schema_version -eq 1 -and
                -not [string]::IsNullOrWhiteSpace([string]$candidate.study_id) -and
                -not [string]::IsNullOrWhiteSpace([string]$candidate.repository) -and
                [string]$candidate.deploy_mode -in @("create", "existing") -and
                $null -ne $appHash
            ) {
                $actualHash = (Get-FileHash -LiteralPath $rootApp -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($actualHash -eq ([string]$appHash.Value).ToLowerInvariant()) {
                    return $true
                }
            }
        } catch {
            continue
        }
    }
    return $false
}

function Test-StreamlitStaticServing {
    param([string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        return $false
    }
    $section = ""
    foreach ($line in Get-Content -LiteralPath $ConfigPath) {
        if ($line -match '^\s*\[([^]]+)\]\s*(?:#.*)?$') {
            $section = $Matches[1].Trim().ToLowerInvariant()
            continue
        }
        if ($section -eq "server" -and $line -match '^\s*enableStaticServing\s*=\s*(true|false)\s*(?:#.*)?$') {
            return $Matches[1].ToLowerInvariant() -eq "true"
        }
        if (-not $section -and $line -match '^\s*server\.enableStaticServing\s*=\s*(true|false)\s*(?:#.*)?$') {
            return $Matches[1].ToLowerInvariant() -eq "true"
        }
    }
    return $false
}

function Copy-SafeBundleFiles {
    param([string]$Destination)
    $previewDirectory = Join-Path $Destination "@@PREVIEW_DIRECTORY@@"
    $refreshLegacyRootRuntime = Test-LegacyRootPublishedApp -Destination $Destination
    $oldManifest = $null
    $oldManifestPath = Join-Path $previewDirectory "deployment_manifest.json"
    if (Test-Path -LiteralPath $oldManifestPath -PathType Leaf) {
        try {
            $candidateManifest = Get-Content -LiteralPath $oldManifestPath -Raw | ConvertFrom-Json
            $expectedRepository = ($RepositoryName.Trim() -replace "\.git$", "")
            if (
                [int]$candidateManifest.schema_version -eq 1 -and
                [string]$candidateManifest.repository -eq $expectedRepository -and
                [string]$candidateManifest.deploy_mode -eq "existing"
            ) {
                $oldManifest = $candidateManifest
            }
        } catch {
            $oldManifest = $null
        }
    }
    $rootConfigName = ".streamlit/config.toml"
    $rootConfigPath = Join-Path $Destination $rootConfigName
    $rootConfigOwned = $false
    if ($null -ne $oldManifest -and @($oldManifest.deployed_paths) -contains $rootConfigName) {
        $rootConfigHash = $oldManifest.files.PSObject.Properties[$rootConfigName]
        if ($null -ne $rootConfigHash -and (Test-Path -LiteralPath $rootConfigPath -PathType Leaf)) {
            $actualRootConfigHash = (Get-FileHash -LiteralPath $rootConfigPath -Algorithm SHA256).Hash.ToLowerInvariant()
            $rootConfigOwned = $actualRootConfigHash -eq ([string]$rootConfigHash.Value).ToLowerInvariant()
        }
    }
    $manageRootConfig = -not (Test-Path -LiteralPath $rootConfigPath -PathType Leaf) -or $rootConfigOwned
    if (-not $manageRootConfig -and -not (Test-StreamlitStaticServing -ConfigPath $rootConfigPath)) {
        throw "The repository's .streamlit/config.toml does not enable static file serving. Set server.enableStaticServing = true there before publishing so Shade-GIS can verify the hosted study."
    }
    function Assert-OwnedDestination {
        param(
            [string]$DestinationPath,
            [string]$BundlePath,
            [switch]$AllowLegacy
        )
        if (-not (Test-Path -LiteralPath $DestinationPath -PathType Leaf) -or $AllowLegacy) {
            return
        }
        if ($BundlePath -eq "deployment_manifest.json" -and $null -ne $oldManifest) {
            return
        }
        $hashProperty = if ($null -ne $oldManifest) {
            $oldManifest.files.PSObject.Properties[$BundlePath]
        } else {
            $null
        }
        if ($null -ne $hashProperty) {
            $actualHash = (Get-FileHash -LiteralPath $DestinationPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualHash -eq ([string]$hashProperty.Value).ToLowerInvariant()) {
                return
            }
        }
        throw "Publishing would overwrite repository file '$DestinationPath' that is not owned by this Shade-GIS deployment."
    }
    $items = @(
        "app.py",
        "public_voting.py",
        "shade_study_stops.csv",
        "shade_study_raw_labels.csv",
        "shade_study_config.json",
        "deployment_manifest.json",
        "requirements.txt",
        "static/shade_gis_identity.json"
    )
    Show-ProtectedFileWarnings
    if (-not (Test-Path $previewDirectory)) {
        New-Item -ItemType Directory -Path $previewDirectory -Force | Out-Null
    }
    foreach ($item in $items) {
        $destinationPath = Join-Path $previewDirectory $item
        Assert-OwnedDestination -DestinationPath $destinationPath -BundlePath $item
        if (Test-Path $item -PathType Leaf) {
            $destinationParent = Split-Path -Parent $destinationPath
            if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
                New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
            }
            if (Test-Path $destinationPath) {
                Write-Host "Updating generated preview file: @@PREVIEW_DIRECTORY@@/$item"
            } else {
                Write-Host "Adding generated preview file: @@PREVIEW_DIRECTORY@@/$item"
            }
            Copy-Item -LiteralPath $item -Destination $destinationPath -Force
        }
    }
    $optionalRawLabels = Join-Path $previewDirectory "shade_study_raw_labels.csv"
    if (-not (Test-Path "shade_study_raw_labels.csv" -PathType Leaf) -and (Test-Path $optionalRawLabels)) {
        Assert-OwnedDestination -DestinationPath $optionalRawLabels -BundlePath "shade_study_raw_labels.csv"
        Remove-Item -LiteralPath $optionalRawLabels -Force
        Write-Host "Removed stale generated preview file: @@PREVIEW_DIRECTORY@@/shade_study_raw_labels.csv"
    }
    $rootDataItems = @(
        "shade_study_stops.csv",
        "shade_study_raw_labels.csv",
        "shade_study_config.json"
    )
    foreach ($item in $rootDataItems) {
        $destinationPath = Join-Path $Destination $item
        Assert-OwnedDestination -DestinationPath $destinationPath -BundlePath $item -AllowLegacy:$refreshLegacyRootRuntime
        if (Test-Path $item -PathType Leaf) {
            if (Test-Path $destinationPath) {
                Write-Host "Updating generated root data file: $item"
            } else {
                Write-Host "Adding generated root data file: $item"
            }
            Copy-Item -LiteralPath $item -Destination $destinationPath -Force
        }
    }
    $rootRawLabels = Join-Path $Destination "shade_study_raw_labels.csv"
    if (-not (Test-Path "shade_study_raw_labels.csv" -PathType Leaf) -and (Test-Path $rootRawLabels)) {
        Assert-OwnedDestination -DestinationPath $rootRawLabels -BundlePath "shade_study_raw_labels.csv" -AllowLegacy:$refreshLegacyRootRuntime
        Remove-Item -LiteralPath $rootRawLabels -Force
        Write-Host "Removed stale generated root data file: shade_study_raw_labels.csv"
    }
    if ($refreshLegacyRootRuntime) {
        foreach ($item in @("app.py", "public_voting.py", "requirements.txt", "static/shade_gis_identity.json")) {
            if (Test-Path $item -PathType Leaf) {
                $destinationPath = Join-Path $Destination $item
                $destinationParent = Split-Path -Parent $destinationPath
                if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
                    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
                }
                Copy-Item -LiteralPath $item -Destination $destinationPath -Force
                Write-Host "Updated active legacy root runtime: $item"
            }
        }
    }
    if ($manageRootConfig) {
        $rootConfigParent = Split-Path -Parent $rootConfigPath
        if (-not (Test-Path -LiteralPath $rootConfigParent -PathType Container)) {
            New-Item -ItemType Directory -Path $rootConfigParent -Force | Out-Null
        }
        Copy-Item -LiteralPath $rootConfigName -Destination $rootConfigPath -Force
        Write-Host "Installed generated Streamlit static-serving configuration."
    }
    $deployedPaths = [Collections.Generic.List[string]]::new()
    foreach ($item in $items) {
        if (Test-Path -LiteralPath (Join-Path $previewDirectory $item) -PathType Leaf) {
            $deployedPaths.Add("@@PREVIEW_DIRECTORY@@/$item")
        }
    }
    foreach ($item in $rootDataItems) {
        if (Test-Path -LiteralPath (Join-Path $Destination $item) -PathType Leaf) {
            $deployedPaths.Add($item)
        }
    }
    if ($refreshLegacyRootRuntime) {
        foreach ($item in @("app.py", "public_voting.py", "requirements.txt", "static/shade_gis_identity.json")) {
            if (Test-Path -LiteralPath (Join-Path $Destination $item) -PathType Leaf) {
                $deployedPaths.Add($item)
            }
        }
    }
    if ($manageRootConfig) {
        $deployedPaths.Add($rootConfigName)
    }
    $publishedManifestPath = Join-Path $previewDirectory "deployment_manifest.json"
    $publishedManifest = Get-Content -LiteralPath $publishedManifestPath -Raw | ConvertFrom-Json
    $publishedManifest | Add-Member -NotePropertyName "deployed_paths" -NotePropertyValue ($deployedPaths.ToArray()) -Force
    $publishedManifestJson = $publishedManifest | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText($publishedManifestPath, $publishedManifestJson, [Text.UTF8Encoding]::new($false))
}

function Stage-PublishFiles {
    param([string[]]$Paths)
    $existingPaths = @($Paths | Where-Object { Test-Path $_ })
    if (-not $existingPaths.Count) {
        return
    }
    & git add -- $existingPaths
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stage generated deployment files."
    }
}

function Commit-And-Push {
    param(
        [string]$TargetBranch,
        [string[]]$Paths,
        [switch]$SkipPush
    )
    Write-Host "Repository status before commit:"
    Invoke-Native "git" @("status")
    Write-Host "Working tree diff summary:"
    Invoke-Native "git" @("diff", "--stat")
    Stage-PublishFiles -Paths $Paths
    Write-Host "Repository status after staging:"
    Invoke-Native "git" @("status")
    Write-Host "Staged diff summary:"
    Invoke-Native "git" @("diff", "--cached", "--stat")
    & git diff --cached --quiet
    $diffExitCode = $LASTEXITCODE
    if ($diffExitCode -eq 0) {
        Write-Host "No changes to publish."
        return $false
    }
    if ($diffExitCode -ne 1) {
        throw "git diff failed with exit code $diffExitCode."
    }
    Confirm-Publish "Review the status and diff summary for branch '$TargetBranch'."
    Invoke-Native "git" @("commit", "-m", $CommitMessage)
    if (-not $SkipPush) {
        Invoke-Native "git" @("push", "origin", $TargetBranch)
    }
    Write-Host "Repository status after commit/push:"
    Invoke-Native "git" @("status", "--short", "--branch")
    return $true
}

Assert-DeploymentBundle

if ($Mode -eq "existing") {
    Assert-PrivateExistingRepository
    $remoteUrl = Get-RemoteUrl
    $publishDir = Join-Path $env:TEMP ("_shade_gis_publish_" + [guid]::NewGuid().ToString("N"))
    $existingPublishFiles = @(
        "@@PREVIEW_DIRECTORY@@",
        "shade_study_stops.csv",
        "shade_study_raw_labels.csv",
        "shade_study_config.json",
        "app.py",
        "public_voting.py",
        "requirements.txt",
        "static",
        ".streamlit/config.toml"
    )
    try {
        if ($RepositoryUrl.Trim() -or $RepositoryName -match "^https?://") {
            Invoke-Native "git" @("clone", $remoteUrl, $publishDir)
        } else {
            Invoke-Native "gh" @("repo", "clone", $RepositoryName, $publishDir)
        }
        if (-not (Test-Path $publishDir)) {
            throw "Clone command completed but publish directory was not created: $publishDir"
        }
        Push-Location $publishDir
        try {
            try {
                Invoke-Native "git" @("checkout", $Branch)
            } catch {
                Invoke-Native "git" @("checkout", "-b", $Branch)
            }
        } finally {
            Pop-Location
        }
        Copy-SafeBundleFiles -Destination $publishDir
        Push-Location $publishDir
        try {
            $publishedChanges = Commit-And-Push -TargetBranch $Branch -Paths $existingPublishFiles
        } finally {
            Pop-Location
        }
        if ($publishedChanges) {
            Write-Host "Published changes to $RepositoryName on branch $Branch."
        } else {
            Write-Host "Existing repository already matches the generated deployment; nothing was pushed."
        }
    } finally {
        if ($publishDir -and (Test-Path $publishDir)) {
            Remove-Item -LiteralPath $publishDir -Recurse -Force
        }
    }
    exit 0
}

# Visibility is a create-only option. Validate it after the existing workflow
# exits so PowerShell does not reject an irrelevant value during parameter binding.
if ($Visibility -notin @("public", "private")) {
    throw "Visibility must be 'public' or 'private' when creating a repository."
}

if ($Visibility -eq "public" -and -not $AllowPublicTarget) {
    throw "Refusing to create a public repository without -AllowPublicTarget. Re-run with -Visibility private or add -AllowPublicTarget."
}

if (-not (Test-Path ".git")) {
    Invoke-Native "git" @("init")
    Invoke-Native "git" @("branch", "-M", $Branch)
}

$newRepoFiles = @(
    "app.py",
    "public_voting.py",
    "shade_study_stops.csv",
    "shade_study_raw_labels.csv",
    "shade_study_config.json",
    "deployment_manifest.json",
    "DEPLOYMENT.md",
    "migrations/001_public_voting.sql",
    "migrations/least_privilege_roles.sql.example",
    "scripts/verify_database.py",
    "scripts/migrate_database.py",
    ".streamlit/secrets.toml.example",
    ".env.example",
    "requirements.txt",
    "README.md",
    "deploy_to_github.ps1",
    ".gitignore",
    ".streamlit/config.toml",
    "static/shade_gis_identity.json"
)
$createdCommit = Commit-And-Push -TargetBranch $Branch -Paths $newRepoFiles -SkipPush
if (-not $createdCommit) {
    throw "No generated deployment changes were staged for the new repository."
}
Invoke-Native "gh" @("repo", "create", $RepositoryName, "--$Visibility", "--source=.", "--remote=origin", "--push")
Write-Host "Created and published repository $RepositoryName on branch $Branch."
