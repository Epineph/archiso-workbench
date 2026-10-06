#!/usr/bin/env python3
"""Preview or write one ISO to an unmounted USB disk using /dev/disk/by-id.

Default is preview. --write requests an interactive, destructive operation.
No disk is unmounted automatically. A completed write is read back and hashed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def descendants(node):
  yield node
  for child in node.get('children', []):
    yield from descendants(child)


def inspect(device, size):
  if (device.parent != Path('/dev/disk/by-id')
      or not device.name.startswith('usb-')):
    raise ValueError('Use /dev/disk/by-id/usb-... for a whole USB disk')
  data = json.loads(subprocess.check_output(
    ['lsblk', '--json', '--bytes', '--paths', '--output',
     'NAME,SIZE,TYPE,TRAN,RO,MOUNTPOINTS,MODEL,SERIAL', str(device)], text=True))
  disks = data['blockdevices']
  if len(disks) != 1:
    raise ValueError('Expected exactly one disk')
  disk = disks[0]
  if disk['type'] != 'disk' or disk['tran'] != 'usb' or disk['ro']:
    raise ValueError('Destination must be a writable whole USB disk')
  if int(disk['size']) < size:
    raise ValueError('The ISO is larger than the disk')
  if not disk.get('serial'):
    raise ValueError('No device serial; refusing ambiguous identification')
  nodes = list(descendants(disk))
  if any(any(n.get('mountpoints') or []) for n in nodes):
    raise ValueError('Disk or a descendant is mounted/in use; unmount manually')
  swaps = {str(Path(line.split()[0]).resolve())
           for line in Path('/proc/swaps').read_text().splitlines()[1:]}
  if any(str(Path(n['name']).resolve()) in swaps for n in nodes):
    raise ValueError('Disk has active swap')
  return disk


def digest(path, size):
  h = hashlib.sha256()
  with path.open('rb', buffering=0) as f:
    remaining = size
    while remaining:
      block = f.read(min(4 * 1024 * 1024, remaining))
      if not block:
        raise ValueError('Short read during verification')
      h.update(block)
      remaining -= len(block)
  return h.hexdigest()


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('iso', type=Path)
  p.add_argument('device', type=Path)
  p.add_argument('--write', action='store_true')
  a = p.parse_args()
  iso = a.iso.resolve(strict=True)
  if not iso.is_file() or iso.suffix != '.iso' or iso.stat().st_size == 0:
    raise ValueError('Supply a nonempty regular .iso file')
  size = iso.stat().st_size
  disk = inspect(a.device, size)
  print(json.dumps(disk, indent=2))
  print(f'ISO: {iso}\nALL CONTENTS of {a.device} will be overwritten.')
  if not a.write:
    print('Preview only; add --write to request the write.')
    return
  if os.geteuid() != 0:
    raise ValueError('Run with sudo for --write')
  if not os.isatty(0):
    raise ValueError('Interactive terminal required')
  token = 'ERASE ' + disk['serial']
  if input(f'Type exactly {token!r}: ') != token:
    raise ValueError('Confirmation did not match; no write')
  # Revalidate immediately before dd; do not unplug/reconnect during writing.
  current = inspect(a.device, size)
  if current != disk:
    raise ValueError('Device information changed; no write')
  expected = digest(iso, size)
  subprocess.run(['dd', f'if={iso}', f'of={a.device}', 'bs=4M',
                  'conv=fsync', 'status=progress'], check=True)
  if digest(a.device, size) != expected:
    raise ValueError('Read-back SHA256 mismatch; do not boot this device')
  print('Write finished; ISO-length read-back SHA256 matches.')


if __name__ == '__main__':
  try:
    main()
  except (OSError, ValueError, subprocess.CalledProcessError) as exc:
    raise SystemExit(f'Error: {exc}')
