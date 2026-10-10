#!/usr/bin/env bash
# Build reviewed local ZFS recipes in an isolated Arch build chroot.
set -Eeuo pipefail

# -----------------------------------------------------------------------------
function usage() {
  cat << 'HELP'
Usage: build-zfs-packages.sh --output DIR --work DIR [options]

Plan a clean-chroot build of local zfs-utils and zfs-dkms recipes. Only --build
executes recipes, downloads sources, creates directories, or invokes sudo.
Review both PKGBUILDs and their supporting files before supplying --build.

Options:
  --sources DIR       Recipe parent (default: packages/ in this repository).
  --output DIR        Required empty or nonexistent package output directory.
  --work DIR          Required empty or nonexistent build/log/chroot directory.
  --cores N           Parallel make jobs (default: half available CPUs, min 1).
  --snapshot DATE     Pin official repos to a chosen date, YYYY/MM/DD.
  --build             Execute the printed plan as a normal Arch x86_64 user.
  -h, --help          Show this help.

Examples:
  ./bin/build-zfs-packages.sh --output builds/zfs-pkgs --work builds/zfs-work
  ./bin/build-zfs-packages.sh --output builds/zfs-pkgs --work builds/zfs-work \
    --cores 4 --build

Use separate empty or nonexistent output/work directories, outside the sources.
Sources must contain zfs-utils/{PKGBUILD,.SRCINFO} and
zfs-dkms/{PKGBUILD,.SRCINFO} for matching ZFS releases. Recipes are copied.

Building requires a normal user on x86_64 Arch Linux with devtools, makepkg,
pacman and sudo. Supply verified source signing keys before building; the helper
does not fetch recipes or import keys. Build dependencies and the utilities
needed by zfs-dkms are installed in the chroot.

Choose a known compatible snapshot; an upload date does not prove kernel support.
Use the same snapshot for the ISO build. The ISO builder checks the compiled ZFS
module against its kernel; live loading still requires a VM test.

Only the verified zfs-utils and zfs-dkms .pkg.tar.zst archives go into --output.
Build sources, logs and chroots remain under --work, including after failures.
HELP
}

function fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

function failed_step() {
  local status=$?
  printf 'Error: ZFS package build failed (exit %s).\n' "$status" >&2
  printf 'Retained work/logs: %s\nRetained packages: %s\n' \
    "$work" "$output" >&2
  exit "$status"
}

function require_value() {
  [[ $# -ge 2 && -n $2 && $2 != --* ]] || fail "$1 requires a value"
}

function print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

# Read .SRCINFO as data; never source a PKGBUILD while planning.
function metadata_versions() {
  python3 - "$@" << 'PYINFO'
import re
import sys
from pathlib import Path

versions = []
for name, filename in zip(('zfs-utils', 'zfs-dkms'), sys.argv[1:]):
  values = {}
  try:
    for raw in Path(filename).read_text().splitlines():
      line = raw.strip()
      if not line or line.startswith('#'):
        continue
      if '=' not in line:
        raise ValueError('malformed .SRCINFO line')
      key, value = (part.strip() for part in line.split('=', 1))
      values.setdefault(key, []).append(value)
    if values.get('pkgname') != [name]:
      raise ValueError(f'expected only pkgname = {name}')
    for key in ('pkgver', 'pkgrel'):
      if len(values.get(key, [])) != 1:
        raise ValueError(f'missing or duplicate {key}')
    epoch = values.get('epoch', ['0'])
    if len(epoch) != 1 or not epoch[0].isdigit():
      raise ValueError('invalid epoch')
    pkgver, pkgrel = values['pkgver'][0], values['pkgrel'][0]
    if not re.fullmatch(r'[A-Za-z0-9_+.]+', pkgver):
      raise ValueError('invalid pkgver')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', pkgrel):
      raise ValueError('invalid pkgrel')
    version = f'{pkgver}-{pkgrel}'
    if int(epoch[0]):
      version = f'{epoch[0]}:{version}'
    versions.append((epoch[0], pkgver, version))
  except (OSError, UnicodeError, ValueError) as error:
    print(f'Error: {filename}: {error}', file=sys.stderr)
    sys.exit(1)
if versions[0][:2] != versions[1][:2]:
  print('Error: zfs-utils and zfs-dkms recipe versions do not match',
        file=sys.stderr)
  sys.exit(1)
print(versions[0][2])
print(versions[1][2])
PYINFO
}

function check_paths() {
  python3 - "$sources" "$output" "$work" << 'PYPATHS'
import sys
from pathlib import Path

sources, output, work = (Path(arg) for arg in sys.argv[1:])
for name, path in (('output', output), ('work', work)):
  if path.exists() and (not path.is_dir() or any(path.iterdir())):
    sys.exit(f'Error: --{name} must be empty or nonexistent: {path}')
  if path == sources or path in sources.parents or sources in path.parents:
    sys.exit(f'Error: --{name} must be separate from --sources: {path}')
if output == work or output in work.parents or work in output.parents:
  sys.exit('Error: --output and --work must be separate directories')
PYPATHS
}

# Query the archive itself, rather than relying on its filename or a glob.
function select_archive() {
  local name=$1 expected=$2 directory=$3 archive info package version extra
  local selected=''
  local -a archives
  shopt -s nullglob
  archives=("$directory"/*.pkg.tar.*)
  shopt -u nullglob
  for archive in "${archives[@]}"; do
    [[ $archive != *.sig ]] || continue
    if ! info=$(pacman --config /dev/null -Qp -- "$archive"); then
      fail "cannot read built package archive: $archive"
    fi
    read -r package version extra <<< "$info"
    [[ -z $extra && -n $package && -n $version ]] ||
      fail "invalid package metadata: $archive"
    [[ $package == "$name" ]] || continue
    [[ $version == "$expected" ]] ||
      fail "$name archive version $version differs from recipe $expected"
    [[ -z $selected ]] || fail "multiple built archives for $name"
    selected=$archive
  done
  [[ -n $selected ]] || fail "no built $name archive found in $directory"
  printf '%s\n' "$selected"
}

# -----------------------------------------------------------------------------
kit=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sources="$kit/packages"
output=''
work=''
cores=''
snapshot=''
build=0
while (($#)); do
  case $1 in
    --sources | --output | --work | --cores | --snapshot)
      require_value "$@"
      case $1 in
        --sources) sources=$2 ;;
        --output) output=$2 ;;
        --work) work=$2 ;;
        --cores) cores=$2 ;;
        --snapshot) snapshot=$2 ;;
      esac
      shift 2
      ;;
    --sources=*)
      sources=${1#*=}
      shift
      ;;
    --output=*)
      output=${1#*=}
      shift
      ;;
    --work=*)
      work=${1#*=}
      shift
      ;;
    --cores=*)
      [[ -n ${1#*=} ]] || fail '--cores requires a value'
      cores=${1#*=}
      shift
      ;;
    --snapshot=*)
      [[ -n ${1#*=} ]] || fail '--snapshot requires a value'
      snapshot=${1#*=}
      shift
      ;;
    --build)
      build=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) fail "unknown option: $1 (use --help)" ;;
  esac
done
[[ -n $sources ]] || fail '--sources requires a directory'
[[ -n $output ]] || fail '--output is required (use --help)'
[[ -n $work ]] || fail '--work is required (use --help)'
command -v python3 > /dev/null || fail 'missing required tool: python3'
if [[ -z $cores ]]; then
  available=$(nproc)
  cores=$((available / 2))
  ((cores > 0)) || cores=1
fi
[[ $cores =~ ^[1-9][0-9]*$ ]] || fail '--cores must be a positive integer'
if [[ -n $snapshot ]]; then
  python3 - "$snapshot" << 'PYDATE'
import datetime
import re
import sys
value = sys.argv[1]
if not re.fullmatch(r'[0-9]{4}/[0-9]{2}/[0-9]{2}', value):
  sys.exit('Error: --snapshot must use YYYY/MM/DD')
try:
  datetime.datetime.strptime(value, '%Y/%m/%d')
except ValueError:
  sys.exit('Error: --snapshot is not a valid date')
PYDATE
fi
sources=$(realpath -m -- "$sources")
output=$(realpath -m -- "$output")
work=$(realpath -m -- "$work")
check_paths
for name in zfs-utils zfs-dkms; do
  [[ -r $sources/$name/PKGBUILD ]] || fail "missing $sources/$name/PKGBUILD"
  [[ -r $sources/$name/.SRCINFO ]] || fail "missing $sources/$name/.SRCINFO"
done
metadata=$(metadata_versions "$sources/zfs-utils/.SRCINFO" \
  "$sources/zfs-dkms/.SRCINFO")
mapfile -t versions <<< "$metadata"

chroot="$work/chroot"
pacman_conf="$work/pacman.conf"
makepkg_conf="$work/makepkg.conf"
packages=(base-devel dkms libaio libtirpc openssl zlib)
mirror='https://geo.mirror.pkgbuild.com'
[[ -z $snapshot ]] || mirror="https://archive.archlinux.org/repos/$snapshot"
mkarchroot_args=(sudo mkarchroot -C "$pacman_conf" -M "$makepkg_conf"
  -c "$work/cache" "$chroot/root" "${packages[@]}")
build_user=$(id -un)

printf 'ZFS package build plan\nSources: %s\nOutput: %s\nWork: %s\n' \
  "$sources" "$output" "$work"
printf 'Versions: zfs-utils %s; zfs-dkms %s\nBuild cores: %s\n' \
  "${versions[0]}" "${versions[1]}" "$cores"
# shellcheck disable=SC2016 # Pacman expands its own variables.
printf 'Official repository server: %s/$repo/os/$arch\n' "$mirror"
printf 'Copy reviewed recipes; create private pacman/makepkg configurations.\n'
print_command "${mkarchroot_args[@]}"
for name in zfs-utils zfs-dkms; do
  printf 'In %s/recipes/%s:\n' "$work" "$name"
  print_command makepkg --config "$makepkg_conf" --printsrcinfo
  args=(sudo "--preserve-env=GNUPGHOME,SSH_AUTH_SOCK" env
    "MAKEPKG_CONF=$makepkg_conf" "MAKEFLAGS=-j$cores" "NPROC=$cores"
    "PKGEXT=.pkg.tar.zst"
    "PKGDEST=$work/artifacts/$name" "LOGDEST=$work/logs/$name"
    "SRCDEST=$work/sources" "SRCPKGDEST=$work/source-packages"
    makechrootpkg -c -r "$chroot" -l "iso-kit-$name" -U "$build_user")
  if [[ $name == zfs-dkms ]]; then
    args+=(-I '<verified zfs-utils archive>')
  fi
  print_command "${args[@]}"
done
if ((! build)); then
  printf 'Plan only. Review the recipes, then repeat with --build.\n'
  exit 0
fi

# -----------------------------------------------------------------------------
[[ -f /etc/arch-release && $(uname -m) == x86_64 ]] ||
  fail '--build requires an Arch Linux x86_64 host'
((EUID != 0)) || fail 'run as a normal user; devtools invokes sudo when needed'
for tool in mkarchroot makechrootpkg makepkg pacman python3 sudo; do
  command -v "$tool" > /dev/null || fail "missing required build tool: $tool"
done
config_source=/usr/share/devtools/makepkg.conf.d/x86_64.conf
[[ -r $config_source ]] || config_source=/etc/makepkg.conf
[[ -r $config_source ]] || fail 'no installed makepkg configuration found'
trap failed_step ERR
mkdir -p -- "$output" "$chroot" "$work/recipes" "$work/cache" \
  "$work/sources" \
  "$work/source-packages" "$work/artifacts" "$work/logs"
cp -- "$config_source" "$makepkg_conf"
# Private configuration prevents user/global destinations and job overrides.
{
  printf '\n# ZFS helper overrides\n'
  printf 'MAKEFLAGS="-j%s"\nNPROC=%s\n' "$cores" "$cores"
  printf "PKGEXT='.pkg.tar.zst'\n"
  printf 'COMPRESSZST=(zstd -c -T%s --ultra -20 -)\n' "$cores"
} >> "$makepkg_conf"
{
  printf '[options]\nArchitecture = x86_64\n'
  printf 'SigLevel = Required DatabaseOptional\nLocalFileSigLevel = Optional\n'
  for repository in core extra; do
    # shellcheck disable=SC2016 # Pacman expands its own variables.
    printf '\n[%s]\nServer = %s/$repo/os/$arch\n' "$repository" "$mirror"
  done
} > "$pacman_conf"
python3 - "$sources" "$work/recipes" << 'PYCOPY'
import shutil
import sys
from pathlib import Path
sources, target = (Path(arg) for arg in sys.argv[1:])
for name in ('zfs-utils', 'zfs-dkms'):
  shutil.copytree(sources / name, target / name,
                  ignore=shutil.ignore_patterns('.git', 'src', 'pkg',
                                                '*.pkg.tar.*', '*.log'))
PYCOPY
# Generate metadata only after explicit authorization to execute recipes.
for name in zfs-utils zfs-dkms; do
  mkdir -p -- "$work/artifacts/$name" "$work/logs/$name"
  (
    cd -- "$work/recipes/$name"
    makepkg --config "$makepkg_conf" --printsrcinfo \
      > "$work/logs/$name/generated.SRCINFO"
  )
done
metadata=$(metadata_versions "$work/logs/zfs-utils/generated.SRCINFO" \
  "$work/logs/zfs-dkms/generated.SRCINFO")
mapfile -t generated_versions <<< "$metadata"
[[ ${generated_versions[*]} == "${versions[*]}" ]] ||
  fail '.SRCINFO is stale; regenerate and review both recipes before building'

"${mkarchroot_args[@]}" 2>&1 | tee "$work/logs/mkarchroot.log"
utils_archive=''
for index in 0 1; do
  names=(zfs-utils zfs-dkms)
  name=${names[$index]}
  args=(sudo "--preserve-env=GNUPGHOME,SSH_AUTH_SOCK" env
    "MAKEPKG_CONF=$makepkg_conf" "MAKEFLAGS=-j$cores" "NPROC=$cores"
    "PKGEXT=.pkg.tar.zst"
    "PKGDEST=$work/artifacts/$name" "LOGDEST=$work/logs/$name"
    "SRCDEST=$work/sources" "SRCPKGDEST=$work/source-packages"
    makechrootpkg -c -r "$chroot" -l "iso-kit-$name" -U "$build_user")
  [[ $name != zfs-dkms ]] || args+=(-I "$utils_archive")
  (
    cd -- "$work/recipes/$name"
    "${args[@]}"
  ) 2>&1 | tee "$work/logs/$name/driver.log"
  archive=$(select_archive "$name" "${versions[$index]}" \
    "$work/artifacts/$name")
  cp -- "$archive" "$output/"
  [[ $name != zfs-utils ]] || utils_archive=$archive
  printf 'Verified package: %s %s\n' "$name" "${versions[$index]}"
done
printf 'ZFS package archives: %s\nBuild logs and chroots: %s\n' "$output" "$work"
