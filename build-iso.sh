#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Archiso Workbench: stage or build a rescue/personal installation image.
# Run --help for usage. No host package configuration is replaced.
# -----------------------------------------------------------------------------
set -Eeuo pipefail
umask 022
KIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

function usage() {
  cat <<'HELP'
Usage: ./build-iso.sh [options]
  --preset rescue|personal    Package selection (default: personal)
  --zfs                      Include zfs-dkms and zfs-utils from local packages
  --local-packages DIR        Directory of reviewed *.pkg.tar.zst archives
  --trust-local-packages     Allow unsigned packages in this local repo only
  --snapshot YYYY/MM/DD       Pin ALL official repos to an explicit archive date
  --base-profile DIR         Default: /usr/share/archiso/configs/releng
  --output DIR               Parent for fresh runs (default: ./builds)
  --build                    Run mkarchiso; otherwise only prepare the profile
  -h, --help                 Show this help

Staging is unprivileged. --build uses sudo only for mkarchiso and inspection.
Use an up-to-date x86_64 Arch host or dedicated Arch VM. Local package build
scripts are executable code: review/build them separately as a normal user.
The final ISO is published only after image/package/ZFS checks pass.
HELP
}

function die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
function need() { command -v "$1" >/dev/null || die "Missing command: $1"; }

preset=personal
zfs=0
build=0
trust=0
local_packages=''
snapshot=''
base=/usr/share/archiso/configs/releng
output="$KIT_DIR/builds"
while (($#)); do
  case "$1" in
    --preset|--local-packages|--snapshot|--base-profile|--output)
      (($# >= 2)) || die "Missing value for $1"
      case "$1" in
        --preset) preset=$2 ;;
        --local-packages) local_packages=$2 ;;
        --snapshot) snapshot=$2 ;;
        --base-profile) base=$2 ;;
        --output) output=$2 ;;
      esac
      shift 2 ;;
    --zfs) zfs=1; shift ;;
    --build) build=1; shift ;;
    --trust-local-packages) trust=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ $preset == rescue || $preset == personal ]] || die 'Invalid preset'
((EUID != 0)) || die 'Run as a normal user; do not sudo this wrapper'
need python3
need sha256sum
[[ -d $base ]] || die 'Install archiso or supply --base-profile'
if ((zfs)) && [[ -z $local_packages ]]; then
  die '--zfs needs --local-packages containing zfs-dkms and zfs-utils'
fi
if [[ -n $local_packages ]]; then
  ((trust)) || die 'Local archives require --trust-local-packages'
  need pacman
  need repo-add
fi
if ((build)); then
  need mkarchiso
  need arch-chroot
  need xorriso
  need sudo
  [[ $(uname -m) == x86_64 ]] || die 'An x86_64 build host is required'
  [[ -f /etc/arch-release ]] || die 'Build on Arch Linux'
fi
mkdir -p -- "$output"
output="$(realpath -- "$output")"
[[ $output != *[[:space:]]* ]] || die 'Build path must not contain whitespace'
run="$(mktemp -d "$output/run-XXXXXXXX")"
# Retain incomplete runs and logs for diagnosis; never recursively clean them.
trap 'printf "Failed at line %s. Run retained: %s\n" "$LINENO" "$run" >&2' ERR
args=(--kit "$KIT_DIR" --base "$base" --run "$run" --preset "$preset")
[[ -z $snapshot ]] || args+=(--snapshot "$snapshot")
[[ -z $local_packages ]] || args+=(--local-packages "$local_packages")
((!zfs)) || args+=(--zfs)
python3 "$KIT_DIR/lib/stage.py" "${args[@]}"
printf '\nPrepared profile: %s/profile\n' "$run"
((build)) || exit 0
mkdir -- "$run/candidate" "$run/out"
# The private pacman configuration belongs to this run, never to /etc.
sudo mkarchiso -v -m iso -C "$run/profile/pacman.conf" \
  -w "$run/work" -o "$run/candidate" "$run/profile" \
  2>&1 | tee "$run/build.log"
root="$run/work/x86_64/airootfs"
[[ -d $root ]] || die 'Archiso root layout changed; inspect retained work'
sudo arch-chroot "$root" pacman -Q > "$run/installed-packages.txt"
if ((zfs)); then
  sudo arch-chroot "$root" /usr/local/lib/iso-kit/verify-zfs \
    2>&1 | tee "$run/zfs-check.log"
fi
shopt -s nullglob
images=("$run/candidate/"*.iso)
((${#images[@]} == 1)) || die 'Expected exactly one candidate ISO'
xorriso -indev "${images[0]}" -report_el_torito plain \
  > "$run/boot-report.txt" 2>&1
# A readable ISO alone is not a firmware boot test; use the VM checklist.
sudo chown -- "$(id -u):$(id -g)" "${images[0]}"
mv -- "${images[0]}" "$run/out/"
(cd -- "$run/out" && sha256sum -- *.iso > SHA256SUMS)
printf '\nISO and checksum: %s/out\nTest it in a VM before USB use.\n' "$run"
