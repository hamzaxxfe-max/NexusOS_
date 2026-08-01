#!/usr/bin/env bash
# =============================================================================
# Aion — Canonical storage layout (SINGLE SOURCE OF TRUTH)
# =============================================================================
# Every build/install/update/immount tool MUST source this file and use these
# variables instead of hard-coding subvolume names. If you change a name here,
# it changes everywhere. Never hard-code @A, @B, @root, @alt or @var-log again.
#
# Layout:
#   @        active root          (booted subvolume)
#   @alt     inactive root slot   (used by OTA as the alternate)
#   @home    /home                (writable, preserved across updates)
#   @var     /var                 (writable)
#   @snapshots  /.snapshots       (rollback snapshots)
#   @swap    /swap                (optional swapfile location)
#   @tmp     /tmp                 (kept as subvolume for zstd compression)
#   @etc-aion /etc/aion           (persistent Aion config)
# =============================================================================

readonly LABEL_AION="AION_ROOT"

readonly SUBVOL_ROOT="@"
readonly SUBVOL_ALT="@alt"
readonly SUBVOL_HOME="@home"
readonly SUBVOL_VAR="@var"
readonly SUBVOL_SNAP="@snapshots"
readonly SUBVOL_SWAP="@swap"
readonly SUBVOL_TMP="@tmp"
readonly SUBVOL_ETC="@etc-aion"

# --- Derived paths (rootfs paths) -----------------------------------------
# All subvolumes in one list, in canonical order, for iteration.
readonly SUBVOL_ALL=(
    "${SUBVOL_ROOT}" "${SUBVOL_ALT}" "${SUBVOL_HOME}" "${SUBVOL_VAR}"
    "${SUBVOL_SNAP}" "${SUBVOL_SWAP}" "${SUBVOL_TMP}" "${SUBVOL_ETC}"
)
