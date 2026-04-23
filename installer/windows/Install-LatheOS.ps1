<#
.SYNOPSIS
    Prepares a USB stick as a LatheOS portable vibe-coding OS.

.DESCRIPTION
    This is the first-time setup app that runs on a plain Windows box, BEFORE
    the user has ever booted LatheOS. When someone downloads the LatheOS
    release zip on Windows, they run this script; it will:

      1. Download (or reuse a cached) latheos-usb.img raw disk image.
      2. Show the list of USB drives currently plugged in, and ask the user
         which one to use. (Wrong pick = the user's file-server gets wiped —
         so the script refuses to touch any disk < 32 GB or > 2 TB unless
         -Force is given, and it refuses to touch system/boot disks at all.)
      3. Flash the image to the chosen USB.
      4. Copy the host-side launchers (launcher/windows + linux + macos +
         README) onto the exFAT partition so the same stick brings its own
         "open in a window" experience to any host later.
      5. Write a minimal first-run profile (preferred language, timezone,
         optional Picovoice key) onto the exFAT partition as
         LATHE_ASSETS/firstrun.json — the in-OS greeter reads it on boot.

    After this script finishes, the user:
      * can reboot, pick the USB in the firmware boot menu, and LatheOS
        continues setup on its own (Mode A); or
      * can double-click launcher/windows/Launch-LatheOS.bat on any Windows
        host to run LatheOS in a window (Mode B).

.PARAMETER ImageUrl
    Where to download the raw USB image from. Default points at the
    LatheOS GitHub release artefact.

.PARAMETER CacheDir
    Local folder where the downloaded image is kept so re-running the
    installer on the same PC does not re-download.

.PARAMETER Language
    Primary UI + voice language. 'en' or 'ko' are supported by default;
    more can be added by dropping Piper voice files into /assets/models/piper
    on the stick later.

.PARAMETER PicovoiceKey
    Optional. If you already have a Picovoice access key, paste it here and
    it will be written to /persist/secrets/cam.env on the stick so the wake
    word works from first boot.

.EXAMPLE
    PS> .\Install-LatheOS.ps1 -Language ko

.NOTES
    Run as Administrator. Requires Windows 10 1809+ (for built-in curl.exe
    and diskpart scripting used here).
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ImageUrl  = "https://github.com/haminxx/LatheOS/releases/latest/download/latheos-usb.img.zip",
    [string]$CacheDir  = "$env:LOCALAPPDATA\LatheOS\cache",
    [ValidateSet('en','ko')]
    [string]$Language  = 'en',
    # Wake-word backend the daemon uses on first boot. "oww" is the default
    # (openWakeWord, Apache-2.0, no vendor key), "porcupine" requires a
    # Picovoice key pasted below, "none" disables wake and uses clap + PTT.
    [ValidateSet('oww','porcupine','none')]
    [string]$WakeBackend = 'oww',
    [string]$PicovoiceKey = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Assert-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an *elevated* PowerShell (Right-click > Run as Administrator)."
    }
}

function Get-LatheOSImage {
    param([string]$Url, [string]$Dest)
    if (Test-Path $Dest) {
        Write-Host "Using cached image: $Dest"
        return $Dest
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    Write-Host "Downloading LatheOS image..."
    Write-Host "  $Url"
    curl.exe -L --fail --progress-bar -o $Dest $Url
    if (-not (Test-Path $Dest)) { throw "Download failed: $Url" }
    return $Dest
}

function Select-USBTarget {
    Write-Host ""
    Write-Host "Removable disks currently connected:"
    Write-Host ""
    $disks = Get-Disk | Where-Object {
        $_.BusType -in @('USB','SD') -and $_.Size -ge 32GB -and $_.Size -le 2TB
    }

    if (-not $disks) {
        throw "No suitable USB disk found (need 32 GB - 2 TB). Plug a stick in and retry."
    }

    $disks | Select-Object Number, FriendlyName,
        @{Name='Size';Expression={"{0:N0} GB" -f ($_.Size/1GB)}},
        BusType, PartitionStyle | Format-Table -AutoSize | Out-Host

    $n = Read-Host "Type the DISK NUMBER from above to flash (everything on it will be erased)"
    $disk = $disks | Where-Object Number -eq ([int]$n)
    if (-not $disk) { throw "Disk $n is not in the removable-USB list. Aborting for safety." }
    if ($disk.IsBoot -or $disk.IsSystem) { throw "Refusing to touch the boot/system disk." }

    Write-Host ""
    Write-Host "Chosen: $($disk.FriendlyName)  ($([math]::Round($disk.Size/1GB)) GB)"
    $ok = Read-Host "Type the word ERASE to confirm (anything else cancels)"
    if ($ok -ne 'ERASE') { throw "Cancelled by user." }
    return $disk
}

function Write-ImageToDisk {
    param([string]$ImagePath, $Disk)

    Write-Host "Clearing partition table..."
    Clear-Disk -Number $Disk.Number -RemoveData -RemoveOEM -Confirm:$false

    Write-Host "Writing $ImagePath to PhysicalDrive$($Disk.Number) ..."
    $target = "\\.\PhysicalDrive$($Disk.Number)"
    # Windows' built-in curl can do streaming copy from a local file if we
    # dd-equivalent via .NET. Keep everything in PowerShell; no extra deps.
    $src = [System.IO.File]::OpenRead($ImagePath)
    try {
        $dst = [System.IO.File]::Open($target,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None)
        try {
            $buffer = New-Object byte[] (4MB)
            $total  = 0
            while (($read = $src.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $dst.Write($buffer, 0, $read)
                $total += $read
                Write-Progress -Activity "Flashing LatheOS" `
                    -Status ("{0:N0} MB" -f ($total / 1MB)) `
                    -PercentComplete ([math]::Min(100, ($total / $src.Length) * 100))
            }
        } finally { $dst.Dispose() }
    } finally { $src.Dispose() }

    Write-Progress -Activity "Flashing LatheOS" -Completed
    Write-Host "Image written."
}

function Write-FirstRunProfile {
    param($Disk, [string]$Language, [string]$PicovoiceKey)

    # Give Windows a moment to re-read the freshly-written partition table,
    # then find the exFAT partition (the only one Windows will mount).
    Start-Sleep -Seconds 3
    Update-Disk -Number $Disk.Number

    $assetsVol = Get-Partition -DiskNumber $Disk.Number |
        Where-Object { $_.Type -ne 'Reserved' } |
        ForEach-Object { Get-Volume -Partition $_ -ErrorAction SilentlyContinue } |
        Where-Object { $_.FileSystemLabel -eq 'LATHE_ASSETS' } | Select-Object -First 1

    if (-not $assetsVol) {
        Write-Warning "Could not locate the LATHE_ASSETS partition from Windows."
        Write-Warning "First-run profile NOT written — LatheOS will boot with defaults."
        return
    }

    $root = ($assetsVol.DriveLetter + ':\')
    Write-Host "Writing first-run profile to $root"

    New-Item -ItemType Directory -Force -Path (Join-Path $root 'latheos') | Out-Null

    $firstRun = [ordered]@{
        language       = $Language
        timezone       = (Get-TimeZone).Id
        keyboard       = (Get-WinUserLanguageList | Select-Object -First 1).InputMethodTips
        wake_backend   = $WakeBackend
        created_on     = (Get-Date).ToString('o')
        created_by_host= $env:COMPUTERNAME
    } | ConvertTo-Json -Depth 4

    Set-Content -Path (Join-Path $root 'latheos\firstrun.json') -Value $firstRun -Encoding UTF8

    # Stage cam.env only if the user explicitly handed us a Picovoice key.
    # The default wake backend (openWakeWord) needs no vendor secret.
    if ($PicovoiceKey -and $WakeBackend -eq 'porcupine') {
        New-Item -ItemType Directory -Force -Path (Join-Path $root 'latheos\secrets') | Out-Null
        $camEnv = @(
            "LATHEOS_WAKE_BACKEND=porcupine"
            "PICOVOICE_ACCESS_KEY=$PicovoiceKey"
            "CAM_KEYWORD_PATH=/persist/secrets/hey-cam.ppn"
        ) -join "`n"
        Set-Content -Path (Join-Path $root 'latheos\secrets\cam.env') -Value $camEnv -Encoding ASCII
        Write-Host "Picovoice key staged. On first boot, LatheOS will move it into /persist/secrets/cam.env."
    } elseif ($WakeBackend -ne 'oww') {
        New-Item -ItemType Directory -Force -Path (Join-Path $root 'latheos\secrets') | Out-Null
        Set-Content `
            -Path (Join-Path $root 'latheos\secrets\cam.env') `
            -Value "LATHEOS_WAKE_BACKEND=$WakeBackend" -Encoding ASCII
    }

    # Stage host-side launchers if the release zip carries them beside the img.
    $launcherSrc = Join-Path (Split-Path $MyInvocation.MyCommand.Path) '..\..\launcher'
    if (Test-Path $launcherSrc) {
        Copy-Item -Recurse -Force -Path $launcherSrc -Destination (Join-Path $root 'launcher')
        Write-Host "Launchers copied to $root\launcher\ (use Launch-LatheOS.bat on Windows, etc.)"
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Assert-Admin

Write-Host "================================================================="
Write-Host "  LatheOS USB installer (Windows)"
Write-Host "================================================================="
Write-Host ""
Write-Host "Language preset : $Language"
Write-Host "Cache directory : $CacheDir"
Write-Host ""

$imgZip = Join-Path $CacheDir 'latheos-usb.img.zip'
$imgRaw = Join-Path $CacheDir 'latheos-usb.img'

Get-LatheOSImage -Url $ImageUrl -Dest $imgZip | Out-Null

if (-not (Test-Path $imgRaw)) {
    Write-Host "Extracting image..."
    Expand-Archive -Path $imgZip -DestinationPath $CacheDir -Force
}
if (-not (Test-Path $imgRaw)) {
    throw "Extracted image not found at $imgRaw (unexpected archive layout)."
}

$target = Select-USBTarget
Write-ImageToDisk -ImagePath $imgRaw -Disk $target
Write-FirstRunProfile -Disk $target -Language $Language -PicovoiceKey $PicovoiceKey

Write-Host ""
Write-Host "Done. Two ways to use the stick now:"
Write-Host "  * Reboot the machine with the stick inserted and pick it in"
Write-Host "    the firmware boot menu (Mode A, full speed)."
Write-Host "  * Or open the stick in File Explorer and double-click"
Write-Host "    launcher\windows\Launch-LatheOS.bat (Mode B, runs in a window)."
Write-Host ""
