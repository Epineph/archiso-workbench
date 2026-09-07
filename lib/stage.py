#!/usr/bin/env python3
"""Prepare a fresh archiso profile; never edit the host or an existing run."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


# -- Overlay validation --------------------------------------------------------
def files_under(root: Path):
  """Reject links and special files in user additions, including hidden files."""
  for p in sorted(root.rglob('*')):
    if p.is_symlink():
      raise ValueError(f'Symlink not allowed in additions: {p}')
    if not (p.is_file() or p.is_dir()):
      raise ValueError(f'Special file not allowed: {p}')
    if any(c in str(p.relative_to(root)) for c in '\n\r\t'):
      raise ValueError(f'Control character in filename: {p!r}')
    if p.is_file():
      yield p


def screen(root: Path):
  """Catch common accidental secrets; this check is not exhaustive."""
  blocked = {'.ssh', '.gnupg', '.aws', '.azure', '.kube', '.git', '.env',
             'auth.json', '.netrc', '.npmrc', '.pypirc', 'shadow', 'gshadow',
             'machine-id', 'zpool.cache', 'credentials', 'keyfiles'}
  for p in files_under(root):
    rel = p.relative_to(root)
    if any(x in blocked or x.endswith(('.key', '.pem')) for x in rel.parts):
      raise ValueError(f'Possible credential/machine identity: {p}')
    if p.stat().st_size > 16 * 1024 * 1024:
      raise ValueError(f'Overlay file exceeds 16 MiB: {p}')
    data = p.read_bytes()
    if re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', data):
      raise ValueError(f'Private key found: {p}')


def merge(src: Path, dst: Path):
  """Copy regular additions without following inherited destination symlinks."""
  for p in files_under(src):
    target = dst / p.relative_to(src)
    for parent in [target, *target.parents]:
      if parent == dst.parent:
        break
      if parent.is_symlink():
        raise ValueError(f'Overlay would traverse a symlink: {parent}')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, target)


def package_list(path: Path):
  result = []
  for line in path.read_text().splitlines():
    name = line.split('#', 1)[0].strip()
    if not name:
      continue
    if not re.fullmatch(r'[a-zA-Z0-9@_+][a-zA-Z0-9@._+:-]*', name):
      raise ValueError(f'Invalid package name in {path}: {name}')
    result.append(name)
  return result


def put(path: Path, text: str):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)


# -- Profile assembly ----------------------------------------------------------
def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for name in ('kit', 'base', 'run'):
    parser.add_argument('--' + name, required=True, type=Path)
  parser.add_argument('--preset', choices=['rescue', 'personal'], required=True)
  parser.add_argument('--snapshot')
  parser.add_argument('--local-packages', type=Path)
  parser.add_argument('--zfs', action='store_true')
  a = parser.parse_args()
  if a.snapshot:
    if not re.fullmatch(r'\d{4}/\d{2}/\d{2}', a.snapshot):
      parser.error('Snapshot must be YYYY/MM/DD')
    dt.datetime.strptime(a.snapshot, '%Y/%m/%d')
  for name in ('profiledef.sh', 'packages.x86_64', 'pacman.conf'):
    if not (a.base / name).is_file():
      parser.error(f'Base profile lacks {name}')
  additions = [a.kit / 'overlay/common']
  if a.zfs:
    additions.append(a.kit / 'overlay/zfs')
  if (a.kit / 'overlay/custom').exists():
    additions.append(a.kit / 'overlay/custom')
  for tree in [*additions, a.kit / 'payload']:
    screen(tree)
  profile = a.run / 'profile'
  shutil.copytree(a.base, profile, symlinks=True)
  root = profile / 'airootfs'
  for tree in additions:
    merge(tree, root)
  merge(a.kit / 'payload', root / 'opt/reinstall-kit/payload')
  shutil.copy2(a.kit / 'README.md', root / 'opt/reinstall-kit/README.md')
  locale = root / 'etc/locale.gen'
  existing = locale.read_text() if locale.exists() else ''
  put(locale, existing + '\nen_DK.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n')
  names = package_list(profile / 'packages.x86_64')
  for name in ['common', a.preset]:
    names += package_list(a.kit / f'packages/{name}.txt')
  names += package_list(a.kit / 'packages/extra.txt')
  if a.zfs:
    names += ['linux-headers', 'base-devel', 'dkms', 'zfs-dkms', 'zfs-utils']

  # Use only official repos plus explicitly supplied local artifacts.
  # Never borrow third-party repos, IgnorePkg, or credentials from the host.
  server = ('Server = https://archive.archlinux.org/repos/' + a.snapshot +
            '/$repo/os/$arch') if a.snapshot else (
              'Include = /etc/pacman.d/mirrorlist')
  conf = ('[options]\nArchitecture = auto\nCheckSpace\n'
          'SigLevel = Required DatabaseOptional\n'
          'LocalFileSigLevel = Optional\nParallelDownloads = 5\n'
          'NoUpgrade = etc/pacman.conf etc/locale.conf etc/vconsole.conf\n'
          'NoUpgrade = etc/locale.gen etc/motd\n\n')
  official = '\n'.join(f'[{r}]\n{server}\n' for r in ['core', 'extra'])
  archives = []
  if a.local_packages:
    repo = a.run / 'repo'
    repo.mkdir()
    seen = set()
    for p in sorted(a.local_packages.resolve().glob('*.pkg.tar.zst')):
      if p.is_symlink() or not p.is_file():
        raise ValueError(f'Package must be a regular file: {p}')
      info = subprocess.check_output(['pacman', '-Qp', str(p)], text=True)
      name, version = info.strip().split()
      if name in seen:
        raise ValueError(f'Multiple versions of local package: {name}')
      seen.add(name)
      names.append(name)
      dest = repo / p.name
      shutil.copy2(p, dest)
      if Path(str(p) + '.sig').exists():
        shutil.copy2(str(p) + '.sig', str(dest) + '.sig')
      archives.append(str(dest))
    if not archives:
      raise ValueError('No *.pkg.tar.zst packages found')
    if a.zfs and not {'zfs-dkms', 'zfs-utils'} <= seen:
      raise ValueError('Supply both zfs-dkms and zfs-utils archives')
    subprocess.run(['repo-add', str(repo / 'iso-local.db.tar.gz'),
                    *archives], check=True)
    # Optional permits unsigned locally-built artifacts, but does not trust
    # unknown signers. Official packages still require trusted signatures.
    conf += ('[iso-local]\nSigLevel = PackageOptional DatabaseOptional\n'
             f'Server = file://{repo.resolve()}\n\n')
  put(profile / 'pacman.conf', conf + official)
  # The live image must never refer to a build-host file:// path.
  live_conf = ('[options]\nArchitecture = auto\nCheckSpace\n'
               'SigLevel = Required DatabaseOptional\n'
               'LocalFileSigLevel = Optional\nParallelDownloads = 5\n\n')
  put(root / 'etc/pacman.conf', live_conf + official)
  names = sorted(set(names))
  put(profile / 'packages.x86_64', '\n'.join(names) + '\n')
  if 'linux' not in names:
    raise ValueError('This version requires the releng linux kernel')

  # Archiso deliberately normalizes modes; executable additions need explicit
  # file_permissions entries, including scripts carried only as payload.
  lines = ["\n# Archiso Workbench overrides", "iso_name='heini-arch'",
           f"iso_version='{dt.datetime.now(dt.timezone.utc):%Y.%m.%d}-"
           f"{a.preset}{'-zfs' if a.zfs else ''}'", "buildmodes=('iso')"]
  for p in sorted(root.rglob('*')):
    if p.is_file() and not p.is_symlink() and p.stat().st_mode & stat.S_IXUSR:
      rel = '/' + str(p.relative_to(root))
      if not re.fullmatch(r'/[a-zA-Z0-9_./+@-]+', rel):
        raise ValueError(f'Executable path uses unsupported characters: {rel}')
      lines.append(f'file_permissions["{rel}"]="0:0:755"')
  with (profile / 'profiledef.sh').open('a') as f:
    f.write('\n'.join(lines) + '\n')
  if a.zfs:
    for service in ['zfs-import-cache.service', 'zfs-import-scan.service']:
      target = root / 'etc/systemd/system' / service
      target.parent.mkdir(parents=True, exist_ok=True)
      if target.exists() or target.is_symlink():
        target.unlink()
      target.symlink_to('/dev/null')
  manifest = {'preset': a.preset, 'zfs': a.zfs, 'snapshot': a.snapshot,
              'base_profile': str(a.base.resolve()), 'packages': names,
              'sha256': {}}
  for p in sorted(profile.rglob('*')):
    if p.is_file() and not p.is_symlink():
      manifest['sha256'][str(p.relative_to(profile))] = (
        hashlib.sha256(p.read_bytes()).hexdigest())
  for p in map(Path, archives):
    manifest['sha256']['repo/' + p.name] = (
      hashlib.sha256(p.read_bytes()).hexdigest())
  put(a.run / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
  print(f'{len(names)} requested packages; manifest: {a.run / "manifest.json"}')


if __name__ == '__main__':
  try:
    main()
  except (ValueError, OSError, subprocess.CalledProcessError) as exc:
    raise SystemExit(f'Error: {exc}')
