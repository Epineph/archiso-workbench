#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Archiso Workbench: stage or build a rescue/personal installation image.
# Run --help for usage. No host package configuration is replaced.
# -----------------------------------------------------------------------------
set -Eeuo pipefail
umask 022
KIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

function usage() {
  cat << 'HELP'
Usage: ./build-iso.sh [options]

Prepare a fresh live Arch installation/rescue profile. Add --build to create
an ISO. Every invocation starts a new run; earlier staged profiles are not reused.

Options:
  --preset rescue|personal   Package selection (default: personal)
  --zfs                      Include ZFS tools and modules in the live image
  --build-zfs-packages       Build reviewed local recipes; implies --zfs
  --zfs-sources DIR          Parent of zfs-utils/ and zfs-dkms/ recipes
                             (default: packages/ beside this script)
                             Used by --build-zfs-packages
  --local-packages DIR       Install every reviewed *.pkg.tar.zst archive in DIR
  --trust-local-packages     Allow unsigned packages in this local repo only
  --snapshot YYYY/MM/DD      Pin all official repos to a chosen Arch Archive date
  --base-profile DIR         Base profile to copy
                             (default: /usr/share/archiso/configs/releng)
  --output DIR               Parent for fresh runs (default: builds/ beside
                             this script); path must not contain whitespace
  --cores N, -jN             Positive core count (default: half nproc, minimum 1)
                             Also accepts --cores=N and -j N
                             Sets compression/make jobs and ISO CPU affinity
  --build                    Run mkarchiso; otherwise only prepare the profile
  -h, --help                 Show this help

ZFS options:
  Supply built zfs-utils and zfs-dkms archives with --zfs --local-packages DIR,
  or use --build-zfs-packages to compile reviewed recipes in a clean chroot.
  Both paths require --trust-local-packages. --build-zfs-packages also requires
  --build and Arch devtools, and cannot be combined with --local-packages.
  Official package signatures remain required.
  Recipes need matching ZFS releases and PKGBUILD/.SRCINFO files. Review their
  supporting files and supply verified source signing keys before building.
  Package building installs ZFS only into its chroot and the live ISO.

Snapshots and validation:
  Choose a known compatible snapshot; an upload date does not prove support.
  --snapshot also pins official repos used by --build-zfs-packages. Supplied
  archives must be built for the chosen environment. Final ZFS checks validate
  the ISO kernel, matching headers and compiled module. Test loading ZFS in a VM.

Examples:
  ./build-iso.sh --preset personal                         # Stage only
  ./build-iso.sh --preset personal --build -j4             # Build an ISO
  ./build-iso.sh --preset rescue --zfs --local-packages ./local-packages \
    --trust-local-packages --build
  ./build-iso.sh --preset personal --build-zfs-packages \
    --trust-local-packages --build

Run as a normal user. Builds require an updated x86_64 Arch host or Arch VM.
Staging is unprivileged; builds use sudo for chroots, mkarchiso and inspection.
Fresh runs, package archives and logs are retained, including failed candidates.
The final ISO is published after build checks pass; VM boot testing follows.
HELP
}

function die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}
function need() { command -v "$1" > /dev/null || die "Missing command: $1"; }

function set_cores() {
  [[ $1 =~ ^[1-9][0-9]*$ ]] ||
    die '--cores/-j requires a positive integer'
  cores=$1
}

preset=personal
zfs=0
build_zfs_packages=0
build=0
trust=0
local_packages=''
zfs_sources="$KIT_DIR/packages"
snapshot=''
cores=''
base=/usr/share/archiso/configs/releng
output="$KIT_DIR/builds"
while (($#)); do
  case "$1" in
    --preset | --local-packages | --zfs-sources | --snapshot | \
      --base-profile | --output)
      (($# >= 2)) || die "Missing value for $1"
      case "$1" in
        --preset) preset=$2 ;;
        --local-packages) local_packages=$2 ;;
        --zfs-sources) zfs_sources=$2 ;;
        --snapshot) snapshot=$2 ;;
        --base-profile) base=$2 ;;
        --output) output=$2 ;;
      esac
      shift 2
      ;;
    --cores | -j)
      (($# >= 2)) || die "Missing value for $1"
      set_cores "$2"
      shift 2
      ;;
    --cores=*)
      set_cores "${1#*=}"
      shift
      ;;
    -j?*)
      set_cores "${1#-j}"
      shift
      ;;
    --zfs)
      zfs=1
      shift
      ;;
    --build-zfs-packages)
      zfs=1
      build_zfs_packages=1
      shift
      ;;
    --build)
      build=1
      shift
      ;;
    --trust-local-packages)
      trust=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done
if [[ -z $cores ]]; then
  need nproc
  cores=$(($(nproc) / 2))
  ((cores >= 1)) || cores=1
fi
[[ $preset == rescue || $preset == personal ]] || die 'Invalid preset'
((EUID != 0)) || die 'Run as a normal user; do not sudo this wrapper'
need python3
need sha256sum
[[ -d $base ]] || die 'Install archiso or supply --base-profile'
if ((build_zfs_packages)); then
  ((build)) || die '--build-zfs-packages requires --build'
  [[ -z $local_packages ]] ||
    die 'Choose --build-zfs-packages or --local-packages, not both'
  ((trust)) || die '--build-zfs-packages requires --trust-local-packages'
fi
if ((zfs && ! build_zfs_packages)) && [[ -z $local_packages ]]; then
  die '--zfs needs --local-packages or --build-zfs-packages (see --help)'
fi
if [[ -n $local_packages ]] || ((build_zfs_packages)); then
  ((trust)) || die 'Local archives require --trust-local-packages'
  need pacman
  need repo-add
  need bsdtar
  need vercmp
fi
if ((build)); then
  need mkarchiso
  need arch-chroot
  need xorriso
  need sudo
  need taskset
  [[ $(uname -m) == x86_64 ]] || die 'An x86_64 build host is required'
  [[ -f /etc/arch-release ]] || die 'Build on Arch Linux'
fi
mkdir -p -- "$output"
output="$(realpath -- "$output")"
[[ $output != *[[:space:]]* ]] || die 'Build path must not contain whitespace'
run="$(mktemp -d "$output/run-XXXXXXXX")"
# Retain incomplete runs and logs for diagnosis; never recursively clean them.
trap 'printf "Failed at line %s. Run retained: %s\n" "$LINENO" "$run" >&2' ERR
if ((build_zfs_packages)); then
  local_packages="$run/zfs-packages"
  package_args=(--sources "$zfs_sources" --output "$local_packages"
    --work "$run/zfs-build" --cores "$cores" --build)
  [[ -z $snapshot ]] || package_args+=(--snapshot "$snapshot")
  bash "$KIT_DIR/bin/build-zfs-packages.sh" "${package_args[@]}" \
    2>&1 | tee "$run/zfs-packages.log"
fi
args=(--kit "$KIT_DIR" --base "$base" --run "$run" --preset "$preset"
  --cores "$cores")
[[ -z $snapshot ]] || args+=(--snapshot "$snapshot")
[[ -z $local_packages ]] || args+=(--local-packages "$local_packages")
((! zfs)) || args+=(--zfs)
python3 "$KIT_DIR/lib/stage.py" "${args[@]}"
printf '\nPrepared profile: %s/profile\nBuild cores: %s\n' "$run" "$cores"
((build)) || exit 0
mkdir -- "$run/candidate" "$run/out"
cpu_list="$(
  python3 - "$cores" << 'PY'
import os
import sys
print(','.join(map(str, sorted(os.sched_getaffinity(0))[:int(sys.argv[1])])))
PY
)"
# The private pacman configuration belongs to this run, never to /etc.
# Affinity also limits package hooks; MAKEFLAGS controls ordinary make jobs.
sudo taskset --cpu-list "$cpu_list" env MAKEFLAGS="-j$cores" \
  mkarchiso -v -m iso -C "$run/profile/pacman.conf" \
  -w "$run/work" -o "$run/candidate" "$run/profile" \
  2>&1 | tee "$run/build.log"
root="$run/work/x86_64/airootfs"
[[ -d $root ]] || die 'Archiso root layout changed; inspect retained work'
# Archiso removes sync databases before compression. Query installed packages
# without configured repositories; missing sync caches do not require a refresh.
# The user owns this log; only the chroot command needs elevated privileges.
# shellcheck disable=SC2024
sudo arch-chroot "$root" pacman --config /dev/null -Q \
  > "$run/installed-packages.txt"
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
