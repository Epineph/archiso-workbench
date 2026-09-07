#!/usr/bin/env bash
# UEFI smoke-test launcher with persistent, private OVMF variables.
set -Eeuo pipefail
function usage() {
  cat <<'HELP'
Usage: test-iso.sh IMAGE.iso OVMF_CODE.fd OVMF_VARS.fd
Supply matching non-Secure-Boot OVMF code/vars from edk2-ovmf.
A fresh variable-store copy is retained in a temporary directory after the test.
Uses KVM when available, otherwise TCG. No host disks are attached.
HELP
}
if [[ ${1:-} == --help ]]; then usage; exit 0; fi
(($# == 3)) || { usage >&2; exit 2; }
for path in "$@"; do
  [[ -f $path ]] || { printf 'Missing file: %s\n' "$path" >&2; exit 1; }
  [[ $path != *,* ]] || { echo 'Commas in paths are unsupported' >&2; exit 1; }
done
command -v qemu-system-x86_64 >/dev/null
iso="$(realpath -- "$1")"
code="$(realpath -- "$2")"
state="$(mktemp -d -- "${TMPDIR:-/tmp}/iso-vm-XXXXXXXX")"
cp -- "$3" "$state/OVMF_VARS.fd"
accel=tcg
[[ ! -r /dev/kvm || ! -w /dev/kvm ]] || accel=kvm
printf 'VM state: %s\n' "$state"
qemu-system-x86_64 -machine "q35,accel=$accel" -m 4096 -smp 2 \
  -drive "if=pflash,format=raw,readonly=on,file=$code" \
  -drive "if=pflash,format=raw,file=$state/OVMF_VARS.fd" \
  -device virtio-scsi-pci,id=scsi0 \
  -drive "if=none,id=cd0,media=cdrom,format=raw,readonly=on,file=$iso" \
  -device scsi-cd,drive=cd0,bus=scsi0.0,bootindex=1 \
  -nic user,model=virtio-net-pci
