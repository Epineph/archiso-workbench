#!/usr/bin/env python3
"""Offline regression checks; no package installs, mounts or disk writes."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]


def load(name, path):
  loader = importlib.machinery.SourceFileLoader(name, str(path))
  spec = importlib.util.spec_from_loader(name, loader)
  module = importlib.util.module_from_spec(spec)
  loader.exec_module(module)
  return module


stage = load('stage', KIT / 'lib/stage.py')
config = load('config', KIT / 'overlay/common/usr/local/bin/iso-config')
usb = load('usb', KIT / 'bin/write-usb.py')


class WorkbenchTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)

  def tearDown(self):
    self.temp.cleanup()

  def test_secret_and_symlink_rejection(self):
    (self.root / 'auth.json').write_text('{}')
    with self.assertRaises(ValueError):
      stage.screen(self.root)
    (self.root / 'auth.json').unlink()
    (self.root / 'link').symlink_to('/etc/passwd')
    with self.assertRaises(ValueError):
      stage.screen(self.root)

  def test_destination_symlink_rejected(self):
    source = self.root / 'src'
    target = self.root / 'dst'
    source.mkdir()
    target.mkdir()
    (source / 'test').write_text('hello')
    (target / 'test').symlink_to('/etc/passwd')
    with self.assertRaises(ValueError):
      stage.merge(source, target)

  def test_stage_preserves_boot_and_executable_mode(self):
    base = self.root / 'base'
    base.mkdir()
    (base / 'airootfs').mkdir()
    (base / 'profiledef.sh').write_text(
      'declare -A file_permissions=()\n'
      "bootmodes=('bios.syslinux' 'uefi.systemd-boot')\n")
    (base / 'packages.x86_64').write_text('linux\nmkinitcpio\n')
    (base / 'pacman.conf').write_text('[options]\n')
    (base / 'boot-sentinel').write_text('unchanged')
    run = self.root / 'run'
    run.mkdir()
    subprocess.run(['python3', str(KIT / 'lib/stage.py'), '--kit', str(KIT),
                    '--base', str(base), '--run', str(run),
                    '--preset', 'personal', '--snapshot', '2026/01/01'],
                   check=True, capture_output=True)
    profile = run / 'profile'
    self.assertEqual((profile / 'boot-sentinel').read_text(), 'unchanged')
    self.assertEqual((base / 'pacman.conf').read_text(), '[options]\n')
    conf = (profile / 'pacman.conf').read_text()
    self.assertIn('Required DatabaseOptional', conf)
    self.assertIn('NoUpgrade = etc/pacman.conf', conf)
    self.assertIn('2026/01/01/$repo/os/$arch', conf)
    live = (profile / 'airootfs/etc/pacman.conf').read_text()
    self.assertNotIn('file://', live)
    text = (profile / 'profiledef.sh').read_text()
    self.assertIn(
      'file_permissions["/usr/local/bin/iso-config"]="0:0:755"', text)
    self.assertIn("bootmodes=('bios.syslinux' 'uefi.systemd-boot')", text)
    subprocess.run(['bash', '-n', str(profile / 'profiledef.sh')], check=True)
    self.assertTrue((run / 'manifest.json').exists())

  def test_apply_preview_backup_and_uid(self):
    target = self.root / 'target'
    (target / 'etc').mkdir(parents=True)
    (target / 'etc/arch-release').touch()
    (target / 'etc/passwd').write_text(
      'heini:x:1234:2345::/home/heini:/bin/zsh\n')
    home = target / 'home/heini'
    home.mkdir(parents=True)
    (home / '.zshrc').write_text('old\n')
    payload = self.root / 'payload'
    (payload / 'home').mkdir(parents=True)
    (payload / 'home/.zshrc').write_text('new\n')
    args = SimpleNamespace(target=target, user='heini', scope='home',
                           payload=payload, overwrite=True, apply=False)
    with patch.object(config.os.path, 'ismount', return_value=True):
      config.apply(args)
      self.assertEqual((home / '.zshrc').read_text(), 'old\n')
      self.assertFalse((target / 'var').exists())
      args.apply = True
      with patch.object(config.os, 'geteuid', return_value=0), \
           patch.object(config.os, 'chown') as chown:
        config.apply(args)
        self.assertTrue(any(c.args[1:] == (1234, 2345)
                            for c in chown.call_args_list))
    self.assertEqual((home / '.zshrc').read_text(), 'new\n')
    backups = list((target / 'var/backups/iso-kit').glob(
      '*/files/home/heini/.zshrc'))
    self.assertEqual(len(backups), 1)
    self.assertEqual(backups[0].read_text(), 'old\n')

  def test_usb_rejects_internal_mounted_and_partition(self):
    disk = {'name': '/dev/sdz', 'size': 2000, 'type': 'disk',
            'tran': 'usb', 'ro': False, 'mountpoints': [None],
            'model': 'fixture', 'serial': 'TEST'}
    path = Path('/dev/disk/by-id/usb-fixture')
    for field, value in [('tran', 'nvme'), ('type', 'part'), ('ro', True),
                         ('mountpoints', ['/']), ('serial', None)]:
      changed = {**disk, field: value}
      with patch.object(usb.subprocess, 'check_output',
                        return_value=json.dumps({'blockdevices': [changed]})):
        with self.assertRaises(ValueError):
          usb.inspect(path, 1000)
    changed = {**disk, 'children': [
      {'name': '/dev/sdz1', 'mountpoints': ['/mnt']}]}
    with patch.object(usb.subprocess, 'check_output',
                      return_value=json.dumps({'blockdevices': [changed]})):
      with self.assertRaises(ValueError):
        usb.inspect(path, 1000)
    with self.assertRaises(ValueError):
      usb.inspect(Path('/dev/nvme0n1'), 1000)

  def test_target_parent_traversal_rejected(self):
    args = SimpleNamespace(target=Path('/mnt/..'))
    with self.assertRaises(ValueError):
      config.apply(args)

  def test_build_help(self):
    result = subprocess.run(['bash', str(KIT / 'build-iso.sh'), '--help'],
                            capture_output=True, text=True)
    self.assertEqual(result.returncode, 0)
    self.assertIn('--trust-local-packages', result.stdout)


if __name__ == '__main__':
  unittest.main()
