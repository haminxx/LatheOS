@echo off
rem ===========================================================================
rem  Launch-LatheOS.bat  —  Mode B launcher for Windows (x64)
rem
rem  Runs LatheOS inside a QEMU window without rebooting. Expects a portable
rem  QEMU build under .\qemu\ next to this script. If the folder is missing,
rem  the script prints instructions instead of failing silently.
rem
rem  This is the "double-click and go" path: no install, no admin, no reboot.
rem  Mode A (boot the USB directly) is still faster; use this when you can't.
rem ===========================================================================

setlocal

set "HERE=%~dp0"
set "QEMU=%HERE%qemu\qemu-system-x86_64.exe"

if not exist "%QEMU%" (
    echo.
    echo   Portable QEMU is missing.
    echo.
    echo   Expected:  %QEMU%
    echo.
    echo   Fix:
    echo     1. Download QEMU for Windows ^(GPLv2^) from https://www.qemu.org/download/#windows
    echo     2. Extract so that qemu-system-x86_64.exe sits at:
    echo          %HERE%qemu\qemu-system-x86_64.exe
    echo     3. Double-click this .bat again.
    echo.
    pause
    exit /b 1
)

rem --- Pick the USB disk -----------------------------------------------------
rem Passing the raw USB to the VM ^(\\.\PhysicalDriveN^) gives the best fidelity:
rem any changes you make inside LatheOS are the SAME bytes on the stick, so a
rem later reboot into Mode A resumes exactly where you left off.
rem
rem Finding N: open PowerShell and run
rem   Get-Disk ^| Format-Table Number,FriendlyName,Size,BusType
rem Pick the row whose FriendlyName matches your LatheOS stick.
set /p DISKNUM="LatheOS USB disk number (from Get-Disk): "
if "%DISKNUM%"=="" (
    echo No disk number entered. Exiting.
    exit /b 1
)
set "USBDEV=\\.\PhysicalDrive%DISKNUM%"

rem --- VM resources ---------------------------------------------------------
rem Conservative defaults so the host stays responsive. A 7B model at q4_K_M
rem fits in ~6 GB; give the VM 8 GB so there is headroom for the editor.
set "RAM_MB=8192"
set "CPUS=4"

echo.
echo   Booting LatheOS from %USBDEV% in a window...
echo   Close the QEMU window to shut LatheOS down.
echo.

"%QEMU%" ^
    -name "LatheOS" ^
    -machine q35,accel=whpx,kernel-irqchip=off ^
    -cpu max ^
    -smp %CPUS% ^
    -m %RAM_MB% ^
    -drive file=%USBDEV%,format=raw,if=virtio,cache=none ^
    -bios "%HERE%qemu\share\qemu\edk2-x86_64-code.fd" ^
    -device virtio-net-pci,netdev=n0 -netdev user,id=n0 ^
    -device intel-hda -device hda-duplex ^
    -display gtk,gl=off ^
    -usb -device usb-tablet

endlocal
