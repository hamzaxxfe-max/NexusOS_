$ErrorActionPreference = "Stop"

$QEMU = "C:\Program Files\qemu\qemu-system-x86_64.exe"
$VM_DIR = "D:\Aion\deploy\cloud-testing\vm"
$ISO = "$VM_DIR\aion-test.iso"
$DISK = "$VM_DIR\aion-disk.qcow2"
$SERIAL_LOG = "$VM_DIR\logs\serial.log"
$SERIAL_LOG_FWD = $SERIAL_LOG.Replace('\', '/')
$DISK_FWD = $DISK.Replace('\', '/')
$ISO_FWD = $ISO.Replace('\', '/')
$PID_FILE = "$VM_DIR\qemu.pid"

$SMP = 4
$MEMORY = "8G"
$VNC_DISPLAY = 1

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Aion Cloud VM Launcher" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[VM] Checking prerequisites..." -ForegroundColor Cyan

if (!(Test-Path $QEMU)) { Write-Host "[VM] QEMU not found at $QEMU" -ForegroundColor Red; exit 1 }
Write-Host "[VM] QEMU: OK" -ForegroundColor Green

if (!(Test-Path $ISO)) { Write-Host "[VM] ISO not found at $ISO" -ForegroundColor Red; exit 1 }
$isoSize = [math]::Round((Get-Item $ISO).Length / 1MB, 1)
Write-Host "[VM] ISO: OK ($isoSize MB)" -ForegroundColor Green

if (!(Test-Path $DISK)) { Write-Host "[VM] Disk not found at $DISK" -ForegroundColor Red; exit 1 }
Write-Host "[VM] Disk: OK" -ForegroundColor Green

# Ensure log directory exists and clean old serial logs
New-Item -ItemType Directory -Force -Path "$VM_DIR\logs" | Out-Null
Remove-Item -Path "$VM_DIR\logs\serial*.log" -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "[VM] Launching QEMU VM (BIOS mode for clean serial boot)..." -ForegroundColor Cyan
Write-Host "  CPUs:    $SMP"
Write-Host "  RAM:     $MEMORY"
Write-Host "  VNC:     :$VNC_DISPLAY (port $($VNC_DISPLAY + 5900))"
Write-Host "  ISO:     $ISO"
Write-Host "  Disk:    $DISK"
Write-Host "  Serial:  $SERIAL_LOG"
Write-Host "  Mode:    BIOS (ISOLINUX = clean serial output)"
Write-Host ""

# BIOS mode using GRUB hybrid ISO (requires grub-pc-bin for BIOS El Torito)
# Use -drive if=ide instead of -cdrom for reliable SeaBIOS CD-ROM detection
$processArgs = @(
    "-name", "aion-cloud"
    "-machine", "pc"
    "-accel", "tcg"
    "-cpu", "max"
    "-smp", "$SMP"
    "-m", $MEMORY
    "-drive", "if=virtio,format=qcow2,file=$DISK_FWD,discard=unmap,detect-zeroes=unmap"
    "-drive", "file=$ISO_FWD,if=ide,media=cdrom,index=0"
    "-boot", "d"
    "-netdev", "user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8080-:80"
    "-device", "virtio-net-pci,netdev=net0"
    "-device", "virtio-vga"
    "-display", "vnc=:$VNC_DISPLAY"
    "-device", "virtio-keyboard-pci"
    "-device", "virtio-mouse-pci"
    "-chardev", "file,id=serial0,path=$SERIAL_LOG_FWD"
    "-serial", "chardev:serial0"
)

$proc = Start-Process -FilePath $QEMU -ArgumentList $processArgs -PassThru -WindowStyle Hidden

# TCG emulation is slow — wait longer for GRUB/ISOLINUX to auto-boot
Write-Host "[VM] Waiting for boot (TCG emulation, may take 15-30s)..." -ForegroundColor Yellow
Start-Sleep -Seconds 20

if (!$proc.HasExited) {
    $proc.Id | Out-File -FilePath $PID_FILE -Encoding ascii -NoNewline
    Write-Host "[VM] VM started (PID: $($proc.Id))" -ForegroundColor Green
} else {
    Write-Host "[VM] VM exited immediately. Check QEMU output." -ForegroundColor Red
    exit 1
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike "*Loopback*" } | Select-Object -First 1).IPAddress
if (!$ip) { $ip = "127.0.0.1" }

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  VM Running" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Browser Viewer:" -ForegroundColor White
Write-Host "    http://${ip}:6081" -ForegroundColor Yellow
Write-Host ""
Write-Host "  VNC Direct:" -ForegroundColor White
Write-Host "    vnc://${ip}:$($VNC_DISPLAY + 5900)" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Serial Log (live):" -ForegroundColor White
Write-Host "    $SERIAL_LOG" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Boot Log Panel:" -ForegroundColor White
Write-Host "    http://${ip}:6081/boot-log" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Stop VM:" -ForegroundColor White
Write-Host "    Stop-Process -Id $($proc.Id)" -ForegroundColor Yellow
Write-Host ""
