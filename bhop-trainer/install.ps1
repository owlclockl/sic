# ASCII-only source so Windows PowerShell 5.1 reads it on every locale.
$ErrorActionPreference = 'Stop'

function Add-ExistingPath {
    param(
        [System.Collections.IList]$List,
        [string]$Path
    )
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $candidate = $Path.Trim().Trim('"')
    try { $candidate = [System.IO.Path]::GetFullPath($candidate) }
    catch { return }
    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) { return }
    foreach ($existing in $List) {
        if ([string]::Equals([string]$existing, $candidate, [System.StringComparison]::OrdinalIgnoreCase)) {
            return
        }
    }
    [void]$List.Add($candidate)
}

function Resolve-DeadlockRoot {
    param([string]$Candidate)
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $null }
    $clean = $Candidate.Trim().Trim('"')
    try { $item = Get-Item -LiteralPath $clean -ErrorAction Stop }
    catch { return $null }
    if (-not $item.PSIsContainer) { return $null }
    $full = $item.FullName
    if (Test-Path -LiteralPath (Join-Path $full 'game\citadel\gameinfo.gi') -PathType Leaf) {
        return $full
    }
    if ($item.Name -ieq 'game') {
        $root = Split-Path -Parent $full
        if (Test-Path -LiteralPath (Join-Path $root 'game\citadel\gameinfo.gi') -PathType Leaf) {
            return $root
        }
    }
    if ($item.Name -ieq 'citadel') {
        $game = Split-Path -Parent $full
        $root = Split-Path -Parent $game
        if (Test-Path -LiteralPath (Join-Path $root 'game\citadel\gameinfo.gi') -PathType Leaf) {
            return $root
        }
    }
    return $null
}

function Get-AppendEncoding {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [System.Text.UTF8Encoding]::new($false)
    }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 4 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE -and $bytes[2] -eq 0 -and $bytes[3] -eq 0) {
        return [System.Text.UTF32Encoding]::new($false, $false)
    }
    if ($bytes.Length -ge 4 -and $bytes[0] -eq 0 -and $bytes[1] -eq 0 -and $bytes[2] -eq 0xFE -and $bytes[3] -eq 0xFF) {
        return [System.Text.UTF32Encoding]::new($true, $false)
    }
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        return [System.Text.UTF8Encoding]::new($false)
    }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        return [System.Text.UnicodeEncoding]::new($false, $false)
    }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
        return [System.Text.UnicodeEncoding]::new($true, $false)
    }
    return [System.Text.Encoding]::Default
}

try {
    Write-Host ''
    Write-Host 'Deadlock :: Bhop Trainer - config installer' -ForegroundColor Cyan
    Write-Host 'Installs the cfg helper only; it does not install or modify the HUD VPK.'
    Write-Host ''

    $steamRoots = New-Object System.Collections.ArrayList
    $libraries = New-Object System.Collections.ArrayList

    foreach ($key in @(
        'HKCU:\Software\Valve\Steam',
        'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam',
        'HKLM:\SOFTWARE\Valve\Steam'
    )) {
        try {
            $properties = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
            Add-ExistingPath -List $steamRoots -Path $properties.SteamPath
            Add-ExistingPath -List $steamRoots -Path $properties.InstallPath
        }
        catch { }
    }

    foreach ($variable in @('ProgramFiles(x86)', 'ProgramFiles')) {
        $base = [System.Environment]::GetEnvironmentVariable($variable)
        if ($base) { Add-ExistingPath -List $steamRoots -Path (Join-Path $base 'Steam') }
    }

    $possibleSteamDirs = @('Steam', 'SteamLibrary', 'Games\Steam', 'Games\SteamLibrary', 'Program Files\Steam', 'Program Files (x86)\Steam')
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem)) {
        foreach ($relative in $possibleSteamDirs) {
            Add-ExistingPath -List $steamRoots -Path (Join-Path $drive.Root $relative)
        }
    }

    foreach ($steamRoot in $steamRoots) {
        Add-ExistingPath -List $libraries -Path $steamRoot
        foreach ($relativeVdf in @('steamapps\libraryfolders.vdf', 'config\libraryfolders.vdf')) {
            $vdf = Join-Path $steamRoot $relativeVdf
            if (-not (Test-Path -LiteralPath $vdf -PathType Leaf)) { continue }
            try { $text = [System.IO.File]::ReadAllText($vdf) }
            catch { continue }
            foreach ($match in [System.Text.RegularExpressions.Regex]::Matches($text, '"path"\s*"([^"]+)"')) {
                $library = $match.Groups[1].Value.Replace('\\', '\')
                Add-ExistingPath -List $libraries -Path $library
            }
            # Older Steam libraryfolders.vdf files store paths under numeric keys.
            foreach ($match in [System.Text.RegularExpressions.Regex]::Matches($text, '(?m)^\s*"\d+"\s*"([^"]+)"')) {
                $library = $match.Groups[1].Value.Replace('\\', '\')
                Add-ExistingPath -List $libraries -Path $library
            }
        }
    }

    $games = New-Object System.Collections.ArrayList
    foreach ($library in $libraries) {
        $candidate = Join-Path $library 'steamapps\common\Deadlock'
        $gameRoot = Resolve-DeadlockRoot -Candidate $candidate
        if ($gameRoot) { Add-ExistingPath -List $games -Path $gameRoot }
    }

    $gameDir = $null
    if ($games.Count -eq 1) {
        $gameDir = [string]$games[0]
    }
    elseif ($games.Count -gt 1) {
        Write-Host 'More than one Deadlock installation was found:' -ForegroundColor Yellow
        for ($i = 0; $i -lt $games.Count; $i++) {
            Write-Host ('  [{0}] {1}' -f ($i + 1), $games[$i])
        }
        $selection = Read-Host 'Choose a number (Enter = 1)'
        if ([string]::IsNullOrWhiteSpace($selection)) { $selection = '1' }
        if ($selection -notmatch '^\d+$') { throw 'Invalid game selection.' }
        $selectedIndex = [int]$selection
        if ($selectedIndex -lt 1 -or $selectedIndex -gt $games.Count) { throw 'Invalid game selection.' }
        $gameDir = [string]$games[$selectedIndex - 1]
    }
    else {
        Write-Host 'Deadlock was not found automatically.' -ForegroundColor Yellow
        $manual = Read-Host 'Paste the Deadlock folder path (or press Enter to cancel)'
        if ([string]::IsNullOrWhiteSpace($manual)) { throw 'Installation cancelled; Deadlock path was not supplied.' }
        $gameDir = Resolve-DeadlockRoot -Candidate $manual
        if (-not $gameDir) { throw 'That folder is not a Deadlock install (game\citadel\gameinfo.gi was not found).' }
    }

    $cfgDir = Join-Path $gameDir 'game\citadel\cfg'
    $sourceFiles = @('bhop_helper.cfg', 'bhop_helper_latin.cfg', 'bhop_helper_off.cfg')
    foreach ($name in $sourceFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) {
            throw ('Installer source file is missing: ' + $name)
        }
    }
    if (-not (Test-Path -LiteralPath $cfgDir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $cfgDir -Force)
    }

    Write-Host ''
    Write-Host ('Deadlock: ' + $gameDir)
    Write-Host ('Installing configs to: ' + $cfgDir)
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    foreach ($name in $sourceFiles) {
        $source = Join-Path $PSScriptRoot $name
        $target = Join-Path $cfgDir $name
        $same = $false
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
            $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            $same = $sourceHash -eq $targetHash
            if (-not $same) {
                $backup = $target + '.bak-' + $stamp
                Copy-Item -LiteralPath $target -Destination $backup
                Write-Host ('  Backup: ' + $backup)
            }
        }
        if ($same) {
            Write-Host ('  Up to date: ' + $name)
        }
        else {
            Copy-Item -LiteralPath $source -Destination $target -Force
            Write-Host ('  Installed: ' + $name)
        }
    }

    $autoexec = Join-Path $cfgDir 'autoexec.cfg'
    $enableAutoexec = Read-Host 'Load the helper automatically on every game start? [Y/n]'
    if ($enableAutoexec -notmatch '(?i)^n(o)?$') {
        $autoexecText = ''
        if (Test-Path -LiteralPath $autoexec -PathType Leaf) {
            $autoexecText = [System.IO.File]::ReadAllText($autoexec)
        }
        $execPattern = '(?im)^\s*exec\s+"?bhop_helper\.cfg"?\s*(?://.*)?$'
        if ($autoexecText -match $execPattern) {
            Write-Host '  autoexec.cfg already loads bhop_helper.cfg.'
        }
        else {
            if (Test-Path -LiteralPath $autoexec -PathType Leaf) {
                $backup = $autoexec + '.bak-' + $stamp
                Copy-Item -LiteralPath $autoexec -Destination $backup
                Write-Host ('  Backup: ' + $backup)
            }
            $newline = "`r`n"
            if ($autoexecText.Contains("`n") -and -not $autoexecText.Contains("`r`n")) { $newline = "`n" }
            $addition = ''
            if ($autoexecText.Length -gt 0 -and -not $autoexecText.EndsWith("`n") -and -not $autoexecText.EndsWith("`r")) {
                $addition += $newline
            }
            $addition += 'exec bhop_helper.cfg' + $newline
            $encoding = Get-AppendEncoding -Path $autoexec
            [System.IO.File]::AppendAllText($autoexec, $addition, $encoding)
            Write-Host '  Added exec bhop_helper.cfg to autoexec.cfg.'
        }
    }
    else {
        Write-Host '  Auto-load skipped. You can load it in the console with: exec bhop_helper.cfg'
    }

    Write-Host ''
    Write-Host 'Install complete. Restart Deadlock if it was open.' -ForegroundColor Green
    Write-Host 'To load it now: open the game console and run: exec bhop_helper.cfg'
    Write-Host 'J = next tip, H = checklist, M = speed, F9 = disable.'
    exit 0
}
catch {
    Write-Host ''
    Write-Host ('INSTALL FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
