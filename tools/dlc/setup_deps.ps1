# glimpse_fix - the programs the DLC build (build_all.py, "Build DLC" in glimpse_config.exe) needs
#
#   setup_deps.bat              shows what is there and what is missing (changes nothing)
#   setup_deps.bat -Install     installs what is missing: the DLC tools bundle of this glimpse_fix release (one
#                               download: Python with numpy, vgmstream-cli, ffmpeg), else each program on its own;
#                               the DirectX runtime (D3DCompiler_41) from Microsoft
#   setup_deps.bat -Update      as -Install, and also replaces an older bundle / updates what is there
#   setup_deps.bat -Install -Bundle             only the bundle (no programs on their own if it cannot be had)
#   setup_deps.bat -Install -NoBundle           each program on its own, as before the bundle
#   setup_deps.bat -Install -BundleZip <zip>    the bundle from a zip downloaded by hand (checked the same way)
#   -BundleVersion <x.y.z>                      the release to take the bundle from (default: this glimpse_tools)
#   setup_deps.bat -DirectXOnly  only D3DCompiler_41 (the DirectX runtime from Microsoft) when it is missing; nothing
#                               else is downloaded, installed or replaced (the settings program's "Set up DLC" uses it)
#
# What the DLC build needs:
#   Python 3          the converters (the DLC cars and the Deadline mode need nothing else)
#   numpy             the DLC tracks (shader translation)
#   vgmstream-cli     the DLC tracks and Deadline's sounds (Xbox audio)
#   ffmpeg            the DLC tracks and Deadline's sounds (PC audio)
#   D3DCompiler_41    the DLC tracks (the shader compiler of the DirectX End-User Runtime, June 2010)
#
# Where it installs:
#   the DLC tools bundle   glimpse_fix-dlc-tools-<version>.zip, an asset of the GitHub release v<version> of
#                          glimpse_fix (github.com/frankyfife/glimpse_fix), unpacked into %LOCALAPPDATA%\glimpse_fix\tools
#                          (python\, vgmstream\, ffmpeg\, glimpse_dlc_tools.txt, the manifest, THIRD_PARTY_dlc_tools.txt).
#                          The zip is checked against the SHA-256 GitHub gives for it, every unpacked file against the
#                          bundle's manifest. build_all.py runs in its Python and uses its vgmstream-cli and ffmpeg.
#                          A release without the bundle: the newest release that has one.
#   on their own (-NoBundle, or no bundle to be had):
#     Python 3, numpy        through winget (Python) and pip (numpy, for the current user)
#     vgmstream-cli, ffmpeg  portable, into %LOCALAPPDATA%\glimpse_fix\tools\vgmstream and ...\ffmpeg
#   D3DCompiler_41.dll     Microsoft's DirectX End-User Runtime (it asks for admin rights; it only adds the
#                          old DirectX files next to what Windows has). Never part of the bundle: Microsoft passes it
#                          on only through its own installer. The game's installer usually put it in place already.
#   Nothing is added to PATH; only the DirectX runtime needs admin rights.
# Sources: the official ones only - the glimpse_fix releases on GitHub (the bundle: python.org's embeddable Python,
# numpy from PyPI, vgmstream's and FFmpeg-Builds' GitHub releases, see THIRD_PARTY_dlc_tools.txt in it), winget /
# python.org, PyPI, the vgmstream releases on GitHub, the ffmpeg release builds of gyan.dev (linked from ffmpeg.org)
# and download.microsoft.com. Every download is over HTTPS.
# At the end it shows the game folder build_all.py finds through Steam (build_all.py --find-game, read only).

[CmdletBinding()]   # an unknown switch is an error, not silently ignored
param(
    [switch]$Check,     # the default: only show what is there
    [switch]$Install,
    [switch]$Update,
    [switch]$Bundle,    # only the bundle
    [switch]$NoBundle,  # never the bundle
    [string]$BundleZip,
    [string]$BundleVersion,
    [switch]$DirectXOnly   # only D3DCompiler_41: no bundle, no Python, numpy, vgmstream-cli or ffmpeg
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is much faster without its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
if ($DirectXOnly -and ($Update -or $Bundle -or $NoBundle -or $BundleZip -or $BundleVersion)) {
    Write-Host '-DirectXOnly goes with no other switch.' -ForegroundColor Red; exit 2
}
if ($DirectXOnly) { $Install = $true }
if ($Update) { $Install = $true }
if ($Bundle -and $NoBundle) { Write-Host '-Bundle and -NoBundle exclude each other.' -ForegroundColor Red; exit 2 }

$ToolsDir = Join-Path $env:LOCALAPPDATA 'glimpse_fix\tools'
$TempDir = Join-Path $env:TEMP ('glimpse_fix_deps_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$Repo = 'frankyfife/glimpse_fix'
$BundleFile = 'glimpse_dlc_tools.txt'            # build_all.BUNDLE_VERSION
$BundleManifest = 'glimpse_dlc_tools_manifest.tsv'
$BundleFormats = @(1)                            # build_all.BUNDLE_FORMATS
$BundleItems = @('python', 'vgmstream', 'ffmpeg', $BundleFile, $BundleManifest, 'THIRD_PARTY_dlc_tools.txt')
$VgmstreamApi = 'https://api.github.com/repos/vgmstream/vgmstream/releases/latest'
$FfmpegZip = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$FfmpegVersion = 'https://www.gyan.dev/ffmpeg/builds/release-version'
$DirectXWeb = 'https://download.microsoft.com/download/1/7/1/1718CCC4-6315-4D8E-9543-8E28A4E18C4C/dxwebsetup.exe'
$Headers = @{ 'User-Agent' = 'glimpse_fix-setup_deps' }

$script:Missing = @()

function Say([string]$text, [string]$color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Ok([string]$name, [string]$what) { Say ('  [ok]      {0,-16} {1}' -f $name, $what) 'Green' }
function Miss([string]$name, [string]$what) { Say ('  [missing] {0,-16} {1}' -f $name, $what) 'Yellow'; $script:Missing += $name }
function Note([string]$name, [string]$what) { Say ('  [note]    {0,-16} {1}' -f $name, $what) 'Yellow' }

function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function Get-File([string]$url, [string]$dest) {
    Say "    downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -Headers $Headers
}

function Get-Sha256([string]$file) { return (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }

# ------------------------------------------------------------------------------------------------ the bundle
function This-Version {
    # the glimpse_fix version of this tools folder: glimpse_tools_files.tsv of a release, else src\common.h of a checkout
    if ($BundleVersion) { return $BundleVersion }
    $tsv = Join-Path $PSScriptRoot 'glimpse_tools_files.tsv'
    if (Test-Path -LiteralPath $tsv) {
        $m = [regex]::Match((Get-Content -LiteralPath $tsv -TotalCount 1), '^# glimpse_fix (\S+):')
        if ($m.Success) { return $m.Groups[1].Value }
    }
    $h = Join-Path $PSScriptRoot '..\..\src\common.h'
    if (Test-Path -LiteralPath $h) {
        $m = [regex]::Match((Get-Content -LiteralPath $h -Raw), '#define\s+GLIMPSE_VERSION\s+"([^"]+)"')
        if ($m.Success) { return $m.Groups[1].Value }
    }
    return $null
}

function Read-Bundle([string]$dir) {
    # the version file as a hashtable, or $null (none, or a format this script does not know)
    $fn = Join-Path $dir $BundleFile
    if (-not (Test-Path -LiteralPath $fn)) { return $null }
    $b = @{}
    foreach ($line in Get-Content -LiteralPath $fn -Encoding UTF8) {
        if ($line.StartsWith('#')) { continue }
        $f = $line -split "`t"
        if ($f.Count -ge 2 -and $f[0] -ne 'exe') { $b[$f[0]] = $f[1] }
    }
    if (-not ($BundleFormats -contains [int]("0" + $b['format']))) { return $null }
    return $b
}

function Test-Bundle([string]$dir) {
    # problems of an unpacked bundle against its manifest (size and SHA-256 of every file); empty = complete
    $fn = Join-Path $dir $BundleManifest
    if (-not (Test-Path -LiteralPath $fn)) { return @("no $BundleManifest") }
    $probs = @(); $n = 0
    foreach ($line in Get-Content -LiteralPath $fn -Encoding UTF8) {
        $f = $line -split "`t"
        if ($f[0] -ne 'file' -or $f.Count -lt 4) { continue }
        $n++
        $p = Join-Path $dir ($f[1] -replace '/', '\')
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { $probs += "missing: $($f[1])"; continue }
        if ((Get-Item -LiteralPath $p).Length -ne [int64]$f[2] -or (Get-Sha256 $p) -ne $f[3]) { $probs += "changed: $($f[1])" }
    }
    if ($n -eq 0) { $probs += "$BundleManifest lists no files" }
    return $probs
}

function Find-BundleAsset([string]$version) {
    # @{ Url; Sha; Name; Tag } of glimpse_fix-dlc-tools-<version>.zip in the release v<version>, else of the newest
    # release (pre-releases too) that has a glimpse_fix-dlc-tools-*.zip; $null when none has one
    $rels = @()
    if ($version) {
        try { $rels += Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/tags/v$version" -UseBasicParsing -Headers $Headers }
        catch { Say "    the release v$version is not on GitHub (or GitHub cannot be reached)" }
    }
    try { $rels += Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases?per_page=30" -UseBasicParsing -Headers $Headers }
    catch { if (-not $rels) { throw } }
    foreach ($rel in $rels) {
        if ($rel.draft) { continue }
        $want = if ($version -and $rel.tag_name -eq "v$version") { "glimpse_fix-dlc-tools-$version.zip" } else { $null }
        $a = $rel.assets | Where-Object { if ($want) { $_.name -eq $want } else { $_.name -match '^glimpse_fix-dlc-tools-.+\.zip$' } } | Select-Object -First 1
        if (-not $a) { continue }
        $own = "https://github.com/$Repo/releases/download/"   # only glimpse_fix's own release assets
        if (-not "$($a.browser_download_url)".StartsWith($own, [StringComparison]::OrdinalIgnoreCase)) {
            Say "    $($a.name) of $($rel.tag_name) is not a glimpse_fix release download ($($a.browser_download_url)); skipped" 'Yellow'; continue
        }
        $sha = $null
        if ($a.digest -and $a.digest.StartsWith('sha256:')) { $sha = $a.digest.Substring(7).ToLowerInvariant() }
        if (-not $sha) {   # the .sha256 next to it (make_dlc_tools.py writes one)
            $s = $rel.assets | Where-Object { $_.name -eq ($a.name + '.sha256') } | Select-Object -First 1
            if ($s -and "$($s.browser_download_url)".StartsWith($own, [StringComparison]::OrdinalIgnoreCase)) {
                $t = Join-Path $TempDir ($a.name + '.sha256')
                Get-File $s.browser_download_url $t
                $sha = ((Get-Content -LiteralPath $t -TotalCount 1) -split '\s+')[0].ToLowerInvariant()
            }
        }
        if (-not $sha) { Say "    $($a.name) of $($rel.tag_name) has no SHA-256 to check it against; skipped" 'Yellow'; continue }
        return @{ Url = $a.browser_download_url; Sha = $sha; Name = $a.name; Tag = $rel.tag_name }
    }
    return $null
}

function Install-Bundle([string]$zip) {
    # unpacks into a new folder next to the tools, checks every file, then replaces python\, vgmstream\, ffmpeg\ and the
    # bundle's own files in the tools folder with it (nothing else there is touched)
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    $new = Join-Path $ToolsDir ('.bundle_new_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        [IO.Compression.ZipFile]::ExtractToDirectory($zip, $new)   # refuses entries outside $new
        $b = Read-Bundle $new
        if (-not $b) { Say '    the zip is not a DLC tools bundle this script knows (no or another glimpse_dlc_tools.txt format)' 'Red'; return $false }
        $probs = @(Test-Bundle $new)
        if ($probs.Count) { Say ('    the bundle is damaged: ' + (($probs | Select-Object -First 3) -join '; ')) 'Red'; return $false }
        foreach ($item in Get-ChildItem -LiteralPath $new -Force) {
            if (-not ($BundleItems -contains $item.Name)) { Say "    unexpected $($item.Name) in the bundle; not installed" 'Red'; return $false }
        }
        foreach ($item in $BundleItems) {
            $src = Join-Path $new $item
            $dst = Join-Path $ToolsDir $item
            if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
            if (Test-Path -LiteralPath $src) { Move-Item -LiteralPath $src -Destination $dst }
        }
        Say ("    installed: DLC tools bundle {0} (Python {1}, numpy {2}, vgmstream {3}, ffmpeg {4}) in {5}" -f `
             $b['bundle'], $b['python'], $b['numpy'], $b['vgmstream'], $b['ffmpeg'], $ToolsDir) 'Cyan'
        return $true
    } finally {
        if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Do-Bundle {
    # $true when the bundle is in place (afterwards), else $false
    $version = This-Version
    $b = Read-Bundle $ToolsDir
    $good = $false
    if ($b) {
        $probs = @(Test-Bundle $ToolsDir)
        if ($probs.Count) {
            Note 'DLC tools' ("bundle $($b['bundle']) is damaged: " + (($probs | Select-Object -First 2) -join '; ') + '; -Install puts it back')
        } else {
            $good = $true
            Ok 'DLC tools' ("bundle {0}: Python {1}, numpy {2}, vgmstream {3}, ffmpeg {4}" -f $b['bundle'], $b['python'], $b['numpy'], $b['vgmstream'], $b['ffmpeg'])
            # kept unless a zip is given, or -Update and this release has another bundle version
            $current = (-not $version) -or ($b['bundle'] -eq $version)
            if ((-not $BundleZip) -and ((-not $Update) -or $current)) { return $true }
        }
    } else {
        Note 'DLC tools' ('not installed: the bundle glimpse_fix-dlc-tools' + $(if ($version) { "-$version" } else { '' }) + '.zip (Python, numpy, vgmstream, ffmpeg in one; -Install gets it)')
    }
    if (-not $Install) { return $good }
    if ($BundleZip) {
        if (-not (Test-Path -LiteralPath $BundleZip)) { Say "    $BundleZip does not exist" 'Red'; return $good }
        Say "    from $BundleZip"
        return ((Install-Bundle (Resolve-Path -LiteralPath $BundleZip).Path) -or $good)
    }
    $asset = Find-BundleAsset $version
    if (-not $asset) { Say '    no glimpse_fix release on GitHub has the DLC tools bundle' 'Yellow'; return $good }
    if ($version -and $asset.Tag -ne "v$version") { Say "    the release v$version has no bundle; taking the one of $($asset.Tag)" 'Yellow' }
    $zip = Join-Path $TempDir $asset.Name
    Get-File $asset.Url $zip
    $got = Get-Sha256 $zip
    if ($got -ne $asset.Sha) { Say "    SHA-256 $got, GitHub says $($asset.Sha): the download is damaged; not installed" 'Red'; return $good }
    return ((Install-Bundle $zip) -or $good)   # a failed update keeps the bundle that is there
}

# ------------------------------------------------------------------------------------------------ Python / numpy
# The probes below may write to stderr (a missing numpy prints a traceback). Windows PowerShell 5.1 turns that
# into an error record even with 2>$null, and 'Stop' makes it fatal, so they set 'Continue' for their own scope
# and judge by $LASTEXITCODE.
function Find-Python {
    $ErrorActionPreference = 'Continue'
    # the bundle's Python first (build_all.py runs there), else the py launcher
    $exe = Join-Path $ToolsDir 'python\python.exe'
    if ((Read-Bundle $ToolsDir) -and (Test-Path -LiteralPath $exe)) {
        $v = & $exe -I -c "import sys; print('%d.%d.%d %d' % (sys.version_info[:3] + (64 if sys.maxsize > 2**32 else 32,)))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return @{ Cmd = $exe; Args = @('-I'); Version = ($v -split ' ')[0]; Bits = ($v -split ' ')[1]; Bundle = $true } }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $v = & py -3 -c "import sys; print('%d.%d.%d %d' % (sys.version_info[:3] + (64 if sys.maxsize > 2**32 else 32,)))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return @{ Cmd = 'py'; Args = @('-3'); Version = ($v -split ' ')[0]; Bits = ($v -split ' ')[1]; Bundle = $false } }
    }
    return $null
}

function Latest-PythonWingetId {
    $ErrorActionPreference = 'Continue'
    # the newest Python.Python.3.x package winget knows
    $out = winget search --id Python.Python.3 --source winget --accept-source-agreements 2>$null | Out-String
    $ids = [regex]::Matches($out, 'Python\.Python\.3\.(\d+)') | ForEach-Object { [int]$_.Groups[1].Value } | Sort-Object -Unique
    if (-not $ids) { return $null }
    return 'Python.Python.3.' + ($ids | Select-Object -Last 1)
}

function Do-Python($python) {
    if ($python) {
        if ($python.Bundle) { Ok 'Python' ("{0} ({1} bit, the bundle's: {2})" -f $python.Version, $python.Bits, $python.Cmd); return }
        Ok 'Python' ("{0} ({1} bit, py launcher)" -f $python.Version, $python.Bits)
        if ($Update) {
            if (Has-Winget) {
                $id = Latest-PythonWingetId
                if ($id) { Say "    updating Python ($id) with winget"; winget install --id $id --source winget --silent --accept-package-agreements --accept-source-agreements | Out-Host }
            } else { Say '    winget not found: update Python from https://www.python.org/downloads/' }
        }
        return
    }
    Miss 'Python' 'Python 3 with the py launcher (https://www.python.org/downloads/)'
    if (-not $Install) { return }
    if (Has-Winget) {
        $id = Latest-PythonWingetId
        if (-not $id) { Say '    winget does not list Python: install it from https://www.python.org/downloads/' 'Red'; return }
        Say "    installing $id with winget"
        winget install --id $id --source winget --silent --accept-package-agreements --accept-source-agreements | Out-Host
        Say '    Python installed. Run this script again (in a new window) for numpy.' 'Cyan'
    } else {
        Say '    winget not found: install Python 3 from https://www.python.org/downloads/ (tick "py launcher"), then run this again.' 'Red'
    }
}

function Do-Numpy($python) {
    if (-not $python) { Miss 'numpy' 'needs Python first'; return }
    $ErrorActionPreference = 'Continue'
    $v = & $python.Cmd @($python.Args) -c "import numpy; print(numpy.__version__)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) {
        Ok 'numpy' $v
        if ($Update -and -not $python.Bundle) { Say '    updating numpy with pip'; & py -3 -m pip install --user --upgrade numpy | Out-Host }
        return
    }
    if ($python.Bundle) { Miss 'numpy' 'the bundle''s Python has no numpy: install the bundle again (-Install -Update)'; return }
    Miss 'numpy' 'py -3 -m pip install --user numpy'
    if ($Install) {
        Say '    installing numpy with pip (current user)'
        & py -3 -m pip install --user --upgrade numpy | Out-Host
    }
}

# ------------------------------------------------------------------------------------------------ vgmstream
function Find-Exe([string]$name, [string]$sub) {
    # as build_all.py: the tools folder (the bundle's or one installed on its own) before PATH
    foreach ($p in @((Join-Path $ToolsDir "$sub\$name.exe"), (Join-Path $ToolsDir "$name.exe"), (Join-Path $ToolsDir "bin\$name.exe"))) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    $c = Get-Command "$name.exe" -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

function Do-Vgmstream([bool]$bundled) {
    $exe = Find-Exe 'vgmstream-cli' 'vgmstream'
    if ($exe -and ($bundled -or -not $Update)) { Ok 'vgmstream-cli' $exe; return }
    if (-not $exe) { Miss 'vgmstream-cli' 'Xbox audio decoder (github.com/vgmstream/vgmstream)' } else { Ok 'vgmstream-cli' $exe }
    if (-not $Install) { return }
    if ($exe -and -not $exe.StartsWith($ToolsDir, [StringComparison]::OrdinalIgnoreCase)) {
        Say "    $exe is not in $ToolsDir; leaving it as it is" ; return
    }
    $rel = Invoke-RestMethod -Uri $VgmstreamApi -UseBasicParsing -Headers $Headers
    $asset = $rel.assets | Where-Object { $_.name -match '^vgmstream-win64\.zip$' } | Select-Object -First 1
    if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -match '^vgmstream-win\.zip$' } | Select-Object -First 1 }
    if (-not $asset) { Say "    no Windows build in vgmstream release $($rel.tag_name); get it from https://github.com/vgmstream/vgmstream/releases" 'Red'; return }
    Say "    vgmstream $($rel.tag_name): $($asset.name)"
    $zip = Join-Path $TempDir $asset.name
    Get-File $asset.browser_download_url $zip
    if ($asset.digest -and $asset.digest.StartsWith('sha256:') -and (Get-Sha256 $zip) -ne $asset.digest.Substring(7).ToLowerInvariant()) {
        Say '    the download does not match the SHA-256 GitHub gives; not installed' 'Red'; return
    }
    $unz = Join-Path $TempDir 'vgmstream'
    Expand-Archive -LiteralPath $zip -DestinationPath $unz -Force
    $cli = Get-ChildItem -LiteralPath $unz -Recurse -Filter 'vgmstream-cli.exe' | Select-Object -First 1
    if (-not $cli) { Say '    the download holds no vgmstream-cli.exe' 'Red'; return }
    # build_all.py looks for <tools>\vgmstream\vgmstream-cli.exe: the folder that holds the exe (with its DLLs) goes there
    $dest = Join-Path $ToolsDir 'vgmstream'
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    Move-Item -LiteralPath $cli.DirectoryName -Destination $dest
    Say "    installed: $(Join-Path $dest 'vgmstream-cli.exe')" 'Cyan'
}

# ------------------------------------------------------------------------------------------------ ffmpeg
function Do-Ffmpeg([bool]$bundled) {
    $exe = Find-Exe 'ffmpeg' 'ffmpeg'
    if ($exe -and ($bundled -or -not $Update)) { Ok 'ffmpeg' $exe; return }
    if (-not $exe) { Miss 'ffmpeg' 'audio encoder (ffmpeg.org; Windows builds by gyan.dev)' } else { Ok 'ffmpeg' $exe }
    if (-not $Install) { return }
    if ($exe -and -not $exe.StartsWith($ToolsDir, [StringComparison]::OrdinalIgnoreCase)) {
        Say "    $exe is not in $ToolsDir; leaving it as it is"; return
    }
    try {
        $c = (Invoke-WebRequest -Uri $FfmpegVersion -UseBasicParsing).Content   # served as bytes (no text type)
        $ver = if ($c -is [byte[]]) { [Text.Encoding]::ASCII.GetString($c).Trim() } else { "$c".Trim() }
    } catch { $ver = 'current release' }
    Say "    ffmpeg $ver (gyan.dev release essentials)"
    $zip = Join-Path $TempDir 'ffmpeg-release-essentials.zip'
    Get-File $FfmpegZip $zip
    $unz = Join-Path $TempDir 'ffmpeg'
    Expand-Archive -LiteralPath $zip -DestinationPath $unz -Force
    $bin = Get-ChildItem -LiteralPath $unz -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
    if (-not $bin) { Say '    the download holds no ffmpeg.exe' 'Red'; return }
    $dest = Join-Path $ToolsDir 'ffmpeg'
    $stage = "$dest.new"
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Copy-Item -Path (Join-Path $bin.DirectoryName '*') -Destination $stage
    $lic = Get-ChildItem -LiteralPath $unz -Recurse -Filter 'LICENSE*' | Select-Object -First 1
    if ($lic) { Copy-Item -LiteralPath $lic.FullName -Destination $stage }
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    Move-Item -LiteralPath $stage -Destination $dest
    Say "    installed: $(Join-Path $dest 'ffmpeg.exe')" 'Cyan'
}

# ------------------------------------------------------------------------------------------------ D3DCompiler_41
function Do-D3DCompiler($python) {
    # build_all.py loads it from System32 with the bitness of its Python: 64 bit -> System32, 32 bit -> SysWOW64
    $sys = Join-Path $env:SystemRoot 'System32\D3DCompiler_41.dll'
    $wow = Join-Path $env:SystemRoot 'SysWOW64\D3DCompiler_41.dll'
    $need = if ($python -and $python.Bits -eq '32' -and [Environment]::Is64BitOperatingSystem) { $wow } else { $sys }
    if (Test-Path -LiteralPath $need) { Ok 'D3DCompiler_41' $need; return }
    Miss 'D3DCompiler_41' 'DirectX End-User Runtime (June 2010), from Microsoft'
    if (-not $Install) { return }
    if (Has-Winget) {
        Say '    installing the DirectX End-User Runtime with winget (it asks for admin rights)'
        winget install --id Microsoft.DirectX --source winget --accept-package-agreements --accept-source-agreements | Out-Host
    } else {
        $exe = Join-Path $TempDir 'dxwebsetup.exe'
        Get-File $DirectXWeb $exe
        $sig = Get-AuthenticodeSignature -FilePath $exe
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
            Say '    the DirectX installer is not signed by Microsoft; not started' 'Red'; return
        }
        Say '    starting Microsoft''s DirectX web installer (it asks for admin rights; untick the Bing bar offer if shown)'
        Start-Process -FilePath $exe -Wait
    }
    if (Test-Path -LiteralPath $need) { Say "    installed: $need" 'Cyan' } else { Say "    still missing: $need" 'Red' }
}

# ------------------------------------------------------------------------------------------------ the game folder
function Show-Game($python) {
    # what build_all.py finds through Steam when no game folder is given (it only reads the registry and Steam's lists)
    $ba = Join-Path $PSScriptRoot 'build_all.py'
    if (-not $python -or -not (Test-Path -LiteralPath $ba)) { return }
    $ErrorActionPreference = 'Continue'   # as the probes above: a warning on stderr must not end the script
    $out = & $python.Cmd @($python.Args) -X utf8 $ba --find-game 2>$null
    $g = $out | Where-Object { $_ -like "game`t*" } | Select-Object -First 1
    if ($g) { Ok 'game folder' (($g -split "`t", 2)[1] + ' (Steam)') }
    else { Say ('  [note]    {0,-16} {1}' -f 'game folder', 'not found through Steam: give it in glimpse_config.exe (or build_all.py --game)') 'Yellow' }
}

# ------------------------------------------------------------------------------------------------ main
Say 'glimpse_fix - programs for the DLC build' 'White'
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
if ($Install) { New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null }
try {
    if ($DirectXOnly) {
        # the Python only to know its bitness (which System32 folder build_all.py loads the DLL from); nothing else is
        # checked, downloaded or replaced
        Do-D3DCompiler (Find-Python)   # says whether it is there afterwards; the caller checks the DLL itself
        exit 0
    }
    $bundled = $false
    if (-not $NoBundle) {
        $bundled = [bool](Do-Bundle)
        if (-not $bundled -and $Bundle) { $Install = $false }   # -Bundle: nothing on its own; the rest is only shown
        elseif (-not $bundled -and $Install) { Say '    installing the programs on their own instead' }
    }
    $python = Find-Python
    Do-Python $python
    if ($Install -and -not $python) { $python = Find-Python }
    Do-Numpy $python
    Do-Vgmstream $bundled
    Do-Ffmpeg $bundled
    Do-D3DCompiler $python
    Show-Game $python
} catch {
    Say ('  error: ' + $_.Exception.Message) 'Red'
    exit 2
} finally {
    if (Test-Path -LiteralPath $TempDir) { Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue }
}

if (-not $Install) {
    if ($script:Missing.Count) {
        Say ''
        Say ('Missing: ' + ($script:Missing -join ', ') + '. Run "setup_deps.bat -Install" to install them.') 'Yellow'
        Say 'Without them only the DLC tracks (and Deadline''s sounds) are left out; the cars and the mode build with Python alone.'
        exit 1
    }
    Say ''
    Say 'Everything the DLC build needs is there.' 'Green'
    exit 0
}
Say ''
Say 'Done. Run "setup_deps.bat" again to check, then "Build DLC" in glimpse_config.exe.' 'Green'
exit 0
