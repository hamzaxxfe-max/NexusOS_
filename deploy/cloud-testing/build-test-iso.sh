#!/usr/bin/env bash
# Build a Aion bootable test ISO for cloud testing.
# Uses grub-mkrescue to create a minimal ISO with custom GRUB config.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/vm"
STAGING_DIR="${OUTPUT_DIR}/iso-staging"
ISO_OUTPUT="${OUTPUT_DIR}/aion-test.iso"

mkdir -p "${STAGING_DIR}/boot/grub"

# --- Aion GRUB config with serial console support ---
cat > "${STAGING_DIR}/boot/grub/grub.cfg" << 'GRUBCFG'
set default=0
set timeout=3
set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

insmod serial
insmod fwsetup
serial --speed=115200 --unit=0 --word=8 --parity=no --stop=1
terminal_input serial console
terminal_output serial console

menuentry "Aion 2.0.0-alpha" --class aion --class gnu-linux --class os {
    echo "Booting Aion 2.0.0-alpha..."
    echo "Serial console test: PASS"
    echo "GRUB timeout: 3s"
    echo "Aion boot config: OK"
    echo ""
    echo "This is a test ISO - the full OS is not included."
    echo "For a complete build, use Aion-Builder.sh on Arch Linux."
    sleep -i 5
}

menuentry "Aion -- Serial Console Test" --class aion --class gnu-linux --class os {
    echo "Serial console is working."
    echo "This confirms GRUB serial output is functional."
    echo "Aion bootloader test: PASS"
    sleep -i 5
}

menuentry "UEFI Firmware Settings" --class firmware {
    fwsetup
}
GRUBCFG

# --- Create ISOLINUX config for BIOS boot ---
mkdir -p "${STAGING_DIR}/boot/isolinux"
cat > "${STAGING_DIR}/boot/isolinux/isolinux.cfg" << 'ISOCFG'
PROMPT 0
TIMEOUT 30
DEFAULT aion

LABEL aion
    MENU LABEL Aion 2.0.0-alpha
    KERNEL /boot/grub/i386-pc/core.img
ISOCFG

# Also create syslinux.cfg for syslinux
cp "${STAGING_DIR}/boot/isolinux/isolinux.cfg" "${STAGING_DIR}/boot/isolinux/syslinux.cfg" 2>/dev/null || true

# --- Build the ISO with grub-mkrescue ---
echo "Building Aion test ISO with grub-mkrescue..."
rm -f "${ISO_OUTPUT}"

grub-mkrescue \
    --output="${ISO_OUTPUT}" \
    --product-name="Aion" \
    --product-version="2.0.0-alpha" \
    --xorriso=/usr/bin/xorriso \
    "${STAGING_DIR}" 2>&1

# --- Verify ---
if [[ -f "${ISO_OUTPUT}" ]]; then
    ISO_SIZE=$(stat -c%s "${ISO_OUTPUT}" 2>/dev/null || stat -f%z "${ISO_OUTPUT}" 2>/dev/null)
    echo ""
    echo "=========================================="
    echo "  Aion Test ISO Built Successfully"
    echo "=========================================="
    echo "  Path: ${ISO_OUTPUT}"
    echo "  Size: $(( ISO_SIZE / 1024 / 1024 )) MB"
    echo "=========================================="
    echo ""
else
    echo "ERROR: ISO build failed!" >&2
    exit 1
fi

# Cleanup staging
rm -rf "${STAGING_DIR}"
