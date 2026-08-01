#!/usr/bin/env bash
# Aion Icon Manager — unified game icon extraction, glow, and indexing.
set -euo pipefail

readonly VERSION="1.0.0"
readonly GAME_DIRS=("/opt/aion/games")
readonly ICON_DIR="${HOME}/.local/share/aion/icons"
readonly INDEX_FILE="${HOME}/.config/aion/game-grid.json"
readonly DEFAULT_ICON="${HOME}/.local/share/aion/icons/_default_gamepad.png"
readonly TEMP_DIR=$(mktemp -d /tmp/aion-icons.XXXXXX)
readonly GLOW_COLOR="#00D2FF"
readonly GLOW_RADIUS="4"
readonly ICON_SIZE="512"

log()  { echo "[icon-manager] $*"; }
err()  { echo "[icon-manager] ERROR: $*" >&2; }
die()  { err "$@"; exit 1; }

cleanup() { rm -rf "${TEMP_DIR}"; }
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
check_deps() {
    local missing=()
    for cmd in convert unzip identify python3 jq; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        die "Missing dependencies: ${missing[*]}"
    fi
}

# ---------------------------------------------------------------------------
# Convert any image to 512x512 PNG via ImageMagick
# ---------------------------------------------------------------------------
convert_to_png() {
    local src="$1"
    local dst="$2"
    convert "$src" \
        -resize "${ICON_SIZE}x${ICON_SIZE}" \
        -gravity center \
        -background none \
        -extent "${ICON_SIZE}x${ICON_SIZE}" \
        -quality 95 \
        PNG:"${dst}" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Apply Electric Cyan glow border: 4px #00D2FF with gaussian blur
# ---------------------------------------------------------------------------
apply_glow() {
    local src="$1"
    local dst="$2"
    local tmp_inner="${TEMP_DIR}/glow_inner_$$.png"
    local tmp_border="${TEMP_DIR}/glow_border_$$.png"

    local dim
    dim=$(identify -format "%wx%h" "$src" 2>/dev/null)

    convert "$src" \
        -bordercolor "$GLOW_COLOR" \
        -border "${GLOW_RADIUS}x${GLOW_RADIUS}" \
        "${tmp_border}" 2>/dev/null

    convert "${tmp_border}" \
        -gaussian-blur 0x3 \
        -modulate 120,150,100 \
        "${tmp_inner}" 2>/dev/null

    convert "${tmp_inner}" "$src" \
        -gravity center \
        -composite \
        -resize "${ICON_SIZE}x${ICON_SIZE}" \
        PNG:"${dst}" 2>/dev/null

    rm -f "${tmp_inner}" "${tmp_border}"
}

# ---------------------------------------------------------------------------
# Extract icon from APK (Android package)
# ---------------------------------------------------------------------------
extract_apk_icon() {
    local apk="$1"
    local dst="$2"
    local apk_temp="${TEMP_DIR}/apk_extract"

    mkdir -p "${apk_temp}"

    unzip -q -o "$apk" "res/mipmap-xxxhdpi*/icon.png" \
          -d "${apk_temp}" 2>/dev/null || \
    unzip -q -o "$apk" "res/mipmap-xxhdpi*/icon.png" \
          -d "${apk_temp}" 2>/dev/null || \
    unzip -q -o "$apk" "res/drawable-xxxhdpi*/icon.png" \
          -d "${apk_temp}" 2>/dev/null || \
    unzip -q -o "$apk" "res/drawable*/icon.png" \
          -d "${apk_temp}" 2>/dev/null || \
    unzip -q -o "$apk" "AndroidManifest.xml" \
          -d "${apk_temp}" 2>/dev/null

    local found_icon=""
    found_icon=$(find "${apk_temp}" -name "icon.png" -type f | head -1)

    if [[ -z "$found_icon" ]]; then
        found_icon=$(find "${apk_temp}" -name "*.png" -type f | head -1)
    fi

    if [[ -n "$found_icon" ]]; then
        convert_to_png "$found_icon" "$dst"
        rm -rf "${apk_temp}"
        return 0
    fi

    rm -rf "${apk_temp}"
    return 1
}

# ---------------------------------------------------------------------------
# Generate default gamepad placeholder icon from SVG
# ---------------------------------------------------------------------------
generate_default_icon() {
    mkdir -p "$(dirname "$DEFAULT_ICON")"
    if [[ -f "${HOME}/.local/share/aion/icons/default-gamepad.svg" ]]; then
        convert "${HOME}/.local/share/aion/icons/default-gamepad.svg" \
            -resize "${ICON_SIZE}x${ICON_SIZE}" \
            PNG:"${DEFAULT_ICON}" 2>/dev/null
    elif command -v rsvg-convert &>/dev/null; then
        local svg_path
        svg_path="$(cd "$(dirname "$0")" && pwd)/default-gamepad.svg"
        if [[ -f "$svg_path" ]]; then
            rsvg-convert -w "$ICON_SIZE" -h "$ICON_SIZE" "$svg_path" \
                -o "${DEFAULT_ICON}" 2>/dev/null
        fi
    fi

    if [[ ! -f "$DEFAULT_ICON" ]]; then
        convert -size "${ICON_SIZE}x${ICON_SIZE}" xc:"#1A1A2E" \
            -fill "$GLOW_COLOR" \
            -gravity center \
            -pointsize 120 \
            -annotate +0+0 "🎮" \
            PNG:"${DEFAULT_ICON}" 2>/dev/null || \
        convert -size "${ICON_SIZE}x${ICON_SIZE}" xc:"#1A1A2E" \
            -fill "$GLOW_COLOR" \
            -draw "roundrectangle 100,180 412,340 30,30" \
            -draw "circle 160,260 160,260" \
            -draw "circle 352,260 352,260" \
            PNG:"${DEFAULT_ICON}" 2>/dev/null
    fi

    log "Default gamepad icon ready at ${DEFAULT_ICON}"
}

# ---------------------------------------------------------------------------
# Parse .desktop file for game metadata
# ---------------------------------------------------------------------------
parse_desktop_file() {
    local desktop_file="$1"
    local name="" exec_path="" icon_path=""

    name=$(grep -m1 "^Name=" "$desktop_file" 2>/dev/null | cut -d= -f2-)
    exec_path=$(grep -m1 "^Exec=" "$desktop_file" 2>/dev/null | cut -d= -f2-)
    icon_path=$(grep -m1 "^Icon=" "$desktop_file" 2>/dev/null | cut -d= -f2-)

    name="${name:-Unknown Game}"
    exec_path="${exec_path:-}"
    icon_path="${icon_path:-}"

    echo "${name}"
    echo "${exec_path}"
    echo "${icon_path}"
}

# ---------------------------------------------------------------------------
# Detect platform from .desktop file path or exec path
# ---------------------------------------------------------------------------
detect_platform() {
    local desktop_file="$1"
    local exec_cmd="$2"

    case "$desktop_file" in
        */wine/*|*/lutris/*) echo "wine" ;;
        */steam/*)           echo "steam" ;;
        */proton/*)          echo "proton" ;;
        *) ;;
    esac

    if [[ -n "$exec_cmd" ]]; then
        case "$exec_cmd" in
            *wine*|*Wine*)       echo "wine" ;;
            *proton*|*Proton*)   echo "proton" ;;
            *lutris*|*Lutris*)   echo "lutris" ;;
            *.sh)                echo "native" ;;
            *)                   echo "native" ;;
        esac
    fi

    echo "native"
}

# ---------------------------------------------------------------------------
# Process a single .desktop file
# ---------------------------------------------------------------------------
process_desktop() {
    local desktop_file="$1"
    local apply_glow_flag="$2"

    local meta
    meta=$(parse_desktop_file "$desktop_file")
    local name exec_path icon_path
    name=$(echo "$meta" | sed -n '1p')
    exec_path=$(echo "$meta" | sed -n '2p')
    icon_path=$(echo "$meta" | sed -n '3p')

    local game_slug
    game_slug=$(echo "$name" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/_/g; s/__*/_/g; s/^_//; s/_$//')
    local output_icon="${ICON_DIR}/${game_slug}.png"
    local platform
    platform=$(detect_platform "$desktop_file" "$exec_path")

    mkdir -p "${ICON_DIR}"

    local icon_extracted=0

    if [[ -n "$icon_path" && -f "$icon_path" ]]; then
        convert_to_png "$icon_path" "${TEMP_DIR}/${game_slug}_raw.png"
        icon_extracted=1
    elif [[ -n "$icon_path" && "$icon_path" == *.apk ]]; then
        if extract_apk_icon "$icon_path" "${TEMP_DIR}/${game_slug}_raw.png"; then
            icon_extracted=1
        fi
    fi

    local search_dirs=()
    for dir in "/opt/aion/games" "/usr/share/icons" "${HOME}/.local/share/icons"; do
        [[ -d "$dir" ]] && search_dirs+=("$dir")
    done

    if (( icon_extracted == 0 )); then
        local found=""
        found=$(find "${search_dirs[@]}" -maxdepth 3 \
            \( -name "${game_slug}*.png" -o -name "${game_slug}*.svg" \
               -o -name "$(echo "$name" | tr '[:upper:]' '[:lower:]')*.png" \) \
            -type f 2>/dev/null | head -1)
        if [[ -n "$found" ]]; then
            convert_to_png "$found" "${TEMP_DIR}/${game_slug}_raw.png"
            icon_extracted=1
        fi
    fi

    if (( icon_extracted == 0 )); then
        if [[ -f "$DEFAULT_ICON" ]]; then
            cp "$DEFAULT_ICON" "${TEMP_DIR}/${game_slug}_raw.png"
        else
            generate_default_icon
            cp "$DEFAULT_ICON" "${TEMP_DIR}/${game_slug}_raw.png"
        fi
        log "Using default icon for: ${name}"
    fi

    if [[ "$apply_glow_flag" == "true" ]]; then
        apply_glow "${TEMP_DIR}/${game_slug}_raw.png" "$output_icon"
    else
        cp "${TEMP_DIR}/${game_slug}_raw.png" "$output_icon"
    fi

    log "Processed: ${name} -> ${output_icon}"

    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    echo "{\"name\":$(printf '%s' "$name" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"icon_path\":\"${output_icon}\",\"exec_path\":\"${exec_path}\",\"platform\":\"${platform}\",\"last_played\":null,\"added\":\"${now}\"}"
}

# ---------------------------------------------------------------------------
# Scan all game directories and build index
# ---------------------------------------------------------------------------
scan_games() {
    local apply_glow_flag="${1:-false}"
    local entries=()
    local processed=0

    for game_dir in "${GAME_DIRS[@]}"; do
        if [[ ! -d "$game_dir" ]]; then
            log "Directory not found, skipping: ${game_dir}"
            continue
        fi

        log "Scanning: ${game_dir}"

        while IFS= read -r -d '' desktop_file; do
            local entry
            entry=$(process_desktop "$desktop_file" "$apply_glow_flag")
            if [[ -n "$entry" ]]; then
                entries+=("$entry")
                (( processed++ )) || true
            fi
        done < <(find "$game_dir" -maxdepth 3 -name "*.desktop" -type f -print0 2>/dev/null)
    done

    log "Processed ${processed} games"

    mkdir -p "$(dirname "$INDEX_FILE")"

    local json_array="["
    local first=true
    for entry in "${entries[@]}"; do
        if [[ "$first" == "true" ]]; then
            first=false
        else
            json_array+=","
        fi
        json_array+="$entry"
    done
    json_array+="]"

    local pretty
    pretty=$(echo "$json_array" | python3 -m json.tool 2>/dev/null || echo "$json_array")
    echo "$pretty" > "$INDEX_FILE"

    log "Index written to ${INDEX_FILE} (${processed} entries)"
}

# ---------------------------------------------------------------------------
# Refresh existing index (re-scan without glow)
# ---------------------------------------------------------------------------
refresh_index() {
    log "Refreshing game index…"
    scan_games false
    log "Refresh complete"
}

# ---------------------------------------------------------------------------
# Apply glow to all existing icons
# ---------------------------------------------------------------------------
apply_glow_all() {
    log "Applying glow effect to all icons…"
    local count=0

    if [[ ! -d "$ICON_DIR" ]]; then
        log "Icon directory does not exist: ${ICON_DIR}"
        return
    fi

    while IFS= read -r -d '' icon_file; do
        local filename
        filename=$(basename "$icon_file")
        [[ "$filename" == _default_* ]] && continue

        local tmp_glow="${TEMP_DIR}/glow_$(basename "$icon_file")"
        if apply_glow "$icon_file" "$tmp_glow"; then
            mv -f "$tmp_glow" "$icon_file"
            (( count++ )) || true
        fi
    done < <(find "$ICON_DIR" -name "*.png" -type f -print0 2>/dev/null)

    log "Glow applied to ${count} icons"
}

# ---------------------------------------------------------------------------
# Print usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Aion Icon Manager v${VERSION}

Usage: $(basename "$0") [OPTIONS]

Options:
  --refresh       Re-scan .desktop files and rebuild game-grid.json index
  --apply-glow    Apply Electric Cyan glow border to all game icons
  --scan          Scan games and apply glow in one pass (default)
  --default-icon  Generate default gamepad placeholder icon
  --help          Show this help message
  --version       Show version

Examples:
  $(basename "$0")                     # Scan with glow
  $(basename "$0") --refresh           # Rebuild index only
  $(basename "$0") --apply-glow        # Add glow to existing icons
  $(basename "$0") --refresh --apply-glow  # Full rebuild + glow

Directories scanned:
  /opt/aion/games/*.desktop

Icons output:
  ${ICON_DIR}/

Index file:
  ${INDEX_FILE}
EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    check_deps

    local do_refresh=false
    local do_glow=false
    local do_scan=false
    local do_default=false

    if (( $# == 0 )); then
        do_scan=true
        do_glow=true
    fi

    while (( $# > 0 )); do
        case "$1" in
            --refresh)      do_refresh=true ;;
            --apply-glow)   do_glow=true ;;
            --scan)         do_scan=true; do_glow=true ;;
            --default-icon) do_default=true ;;
            --help|-h)      usage; exit 0 ;;
            --version|-v)   echo "Aion Icon Manager v${VERSION}"; exit 0 ;;
            *)              die "Unknown option: $1 (use --help)" ;;
        esac
        shift
    done

    mkdir -p "${ICON_DIR}"

    if [[ "$do_default" == "true" ]]; then
        generate_default_icon
    fi

    if [[ "$do_scan" == "true" ]]; then
        scan_games "$do_glow"
    elif [[ "$do_refresh" == "true" ]]; then
        refresh_index
    fi

    if [[ "$do_glow" == "true" && "$do_scan" == "false" ]]; then
        apply_glow_all
    fi

    log "Done."
}

main "$@"
