# glimpse_fix - the programs the DLC build (build_all.py, "Build DLC" in glimpse_config.exe) needs
#
#   setup_deps.bat              shows what is there and what is missing (changes nothing)
#   setup_deps.bat -Install     installs what is missing, each in its current version
#   setup_deps.bat -Update      as -Install, and also updates what is there to the current version
#
# What the DLC build needs:
#   Python 3          the converters (the DLC cars and the Deadline mode need nothing else)
#   numpy             the DLC tracks (shader translation)
#   vgmstream-cli     the DLC tracks and Deadline's sounds (Xbox audio)
#   ffmpeg            the DLC tracks and Deadline's sounds (PC audio)
#   D3DCompiler_41    the DLC tracks (the shader compiler of the DirectX End-User Runtime, June 2010)
#
# Where it installs:
#   Python 3, numpy        through winget (Python) and pip (numpy, for the current user)
#   vgmstream-cli, ffmpeg  portable, into %LOCALAPPDATA%\glimpse_fix\tools\vgmstream and ...\ffmpeg, where
#                          build_all.py looks for them; nothing is added to PATH, nothing needs admin rights
#   D3DCompiler_41.dll     Microsoft's DirectX End-User Runtime (it asks for admin rights; it only adds the
#                          old DirectX files next to what Windows has)
# Sources: the official ones only - winget / python.org, PyPI, the vgmstream releases on GitHub
# (github.com/vgmstream/vgmstream), the ffmpeg release builds of gyan.dev (linked from ffmpeg.org) and
# download.microsoft.com. Every download is over HTTPS.

[CmdletBinding()]   # an unknown switch is an error, not silently ignored
param(
    [switch]$Check,     # the default: only show what is there
    [switch]$Install,
    [switch]$Update
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is much faster without its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
if ($Update) { $Install = $true }

$ToolsDir = Join-Path $env:LOCALAPPDATA 'glimpse_fix\tools'
$TempDir = Join-Path $env:TEMP ('glimpse_fix_deps_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$VgmstreamApi = 'https://api.github.com/repos/vgmstream/vgmstream/releases/latest'
$FfmpegZip = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$FfmpegVersion = 'https://www.gyan.dev/ffmpeg/builds/release-version'
$DirectXWeb = 'https://download.microsoft.com/download/1/7/1/1718CCC4-6315-4D8E-9543-8E28A4E18C4C/dxwebsetup.exe'

$script:Missing = @()

function Say([string]$text, [string]$color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Ok([string]$name, [string]$what) { Say ('  [ok]      {0,-16} {1}' -f $name, $what) 'Green' }
function Miss([string]$name, [string]$what) { Say ('  [missing] {0,-16} {1}' -f $name, $what) 'Yellow'; $script:Missing += $name }

function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function Get-File([string]$url, [string]$dest) {
    Say "    downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -Headers @{ 'User-Agent' = 'glimpse_fix-setup_deps' }
}

# ------------------------------------------------------------------------------------------------ Python / numpy
# The probes below may write to stderr (a missing numpy prints a traceback). Windows PowerShell 5.1 turns that
# into an error record even with 2>$null, and 'Stop' makes it fatal, so they set 'Continue' for their own scope
# and judge by $LASTEXITCODE.
function Find-Python {
    $ErrorActionPreference = 'Continue'
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $v = & py -3 -c "import sys; print('%d.%d.%d %d' % (sys.version_info[:3] + (64 if sys.maxsize > 2**32 else 32,)))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return @{ Cmd = 'py'; Args = @('-3'); Version = ($v -split ' ')[0]; Bits = ($v -split ' ')[1] } }
    }
    return $null
}

function Latest-PythonWingetId {
    # the newest Python.Python.3.x package winget knows
    $ErrorActionPreference = 'Continue'
    $out = winget search --id Python.Python.3 --source winget --accept-source-agreements 2>$null | Out-String
    $ids = [regex]::Matches($out, 'Python\.Python\.3\.(\d+)') | ForEach-Object { [int]$_.Groups[1].Value } | Sort-Object -Unique
    if (-not $ids) { return $null }
    return 'Python.Python.3.' + ($ids | Select-Object -Last 1)
}

function Do-Python($python) {
    if ($python) {
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
    $v = & py -3 -c "import numpy; print(numpy.__version__)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) {
        Ok 'numpy' $v
        if ($Update) { Say '    updating numpy with pip'; & py -3 -m pip install --user --upgrade numpy | Out-Host }
        return
    }
    Miss 'numpy' 'py -3 -m pip install --user numpy'
    if ($Install) {
        Say '    installing numpy with pip (current user)'
        & py -3 -m pip install --user --upgrade numpy | Out-Host
    }
}

# ------------------------------------------------------------------------------------------------ vgmstream
function Find-Exe([string]$name, [string]$sub) {
    $c = Get-Command "$name.exe" -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @((Join-Path $ToolsDir "$name.exe"), (Join-Path $ToolsDir "$sub\$name.exe"), (Join-Path $ToolsDir "bin\$name.exe"))) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Do-Vgmstream {
    $exe = Find-Exe 'vgmstream-cli' 'vgmstream'
    if ($exe -and -not $Update) { Ok 'vgmstream-cli' $exe; return }
    if (-not $exe) { Miss 'vgmstream-cli' 'Xbox audio decoder (github.com/vgmstream/vgmstream)' } else { Ok 'vgmstream-cli' $exe }
    if (-not $Install) { return }
    if ($exe -and -not $exe.StartsWith($ToolsDir, [StringComparison]::OrdinalIgnoreCase)) {
        Say "    $exe is not in $ToolsDir; leaving it as it is" ; return
    }
    $rel = Invoke-RestMethod -Uri $VgmstreamApi -UseBasicParsing -Headers @{ 'User-Agent' = 'glimpse_fix-setup_deps' }
    $asset = $rel.assets | Where-Object { $_.name -match '^vgmstream-win64\.zip$' } | Select-Object -First 1
    if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -match '^vgmstream-win\.zip$' } | Select-Object -First 1 }
    if (-not $asset) { Say "    no Windows build in vgmstream release $($rel.tag_name); get it from https://github.com/vgmstream/vgmstream/releases" 'Red'; return }
    Say "    vgmstream $($rel.tag_name): $($asset.name)"
    $zip = Join-Path $TempDir $asset.name
    Get-File $asset.browser_download_url $zip
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
function Do-Ffmpeg {
    $exe = Find-Exe 'ffmpeg' 'ffmpeg'
    if ($exe -and -not $Update) { Ok 'ffmpeg' $exe; return }
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

# ------------------------------------------------------------------------------------------------ main
Say 'glimpse_fix - programs for the DLC build' 'White'
if ($Install) { New-Item -ItemType Directory -Force -Path $ToolsDir, $TempDir | Out-Null }
try {
    $python = Find-Python
    Do-Python $python
    if ($Install -and -not $python) { $python = Find-Python }
    Do-Numpy $python
    Do-Vgmstream
    Do-Ffmpeg
    Do-D3DCompiler $python
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
