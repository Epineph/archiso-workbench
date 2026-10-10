#!/usr/bin/env python3
"""Read-only ZFS checks against isolated module trees and command fixtures."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
VERIFIER = KIT / 'overlay/zfs/usr/local/lib/iso-kit/verify-zfs'
KERNEL = '6.18.1-arch1-1'
VERSION = '6.18.1.arch1-1'


class ZfsVerificationTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.work = Path(self.temp.name)
    self.root = self.work / 'image'
    self.root.mkdir()
    self.modules = self.module_tree(KERNEL)
    self.bin = self.work / 'bin'
    self.bin.mkdir()
    self.log = self.work / 'commands.jsonl'
    # Log the actual invocation and supply bounded failures at tool boundaries.
    # A forbidden depmod/uname call remains visible and causes a test failure.
    mock = f'''#!{sys.executable}
import json
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
with Path(os.environ['TEST_ZFS_LOG']).open('a') as stream:
  stream.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'pacman':
  if os.environ.get('TEST_ZFS_PACMAN_FAIL'):
    sys.exit(1)
  print('zfs-dkms 2.4.0-1\\nzfs-utils 2.4.0-1')
  print('linux ' + os.environ['TEST_ZFS_LINUX'])
  print('linux-headers ' + os.environ['TEST_ZFS_HEADERS'])
elif name == 'modinfo':
  if os.environ.get('TEST_ZFS_MODINFO_FAIL'):
    sys.exit(1)
  magic = os.environ['TEST_ZFS_VERMAGIC']
  if magic == 'from-argument':
    magic = sys.argv[sys.argv.index('-k') + 1] + ' SMP preempt mod_unload'
  print(magic)
elif name == 'modprobe':
  if os.environ.get('TEST_ZFS_MODPROBE_FAIL'):
    print('missing dependency spl', file=sys.stderr)
    sys.exit(1)
  print('insmod fixture/spl.ko.zst\\ninsmod fixture/zfs.ko.zst')
else:
  sys.exit('Forbidden verification command: ' + name)
'''
    for command in ['pacman', 'modinfo', 'modprobe', 'depmod', 'uname']:
      executable = self.bin / command
      executable.write_text(mock)
      executable.chmod(0o755)
    self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                'TEST_ZFS_LOG': str(self.log), 'TEST_ZFS_LINUX': VERSION,
                'TEST_ZFS_HEADERS': VERSION,
                'TEST_ZFS_VERMAGIC': f'{KERNEL} SMP preempt mod_unload'}

  def tearDown(self):
    self.temp.cleanup()

  def module_tree(self, kernel):
    directory = self.root / 'usr/lib/modules' / kernel
    (directory / 'build').mkdir(parents=True)
    (directory / 'pkgbase').write_text('linux\n')
    (directory / 'modules.dep').write_text('extra/zfs.ko.zst: extra/spl.ko.zst\n')
    (directory / 'modules.dep.bin').write_bytes(b'offline dependency index')
    (directory / 'extra').mkdir()
    for module in ['zfs', 'spl']:
      (directory / 'extra' / f'{module}.ko.zst').write_bytes(b'fixture module')
    return directory

  def snapshot(self):
    """Include modes and all entries so newly generated indexes are detected."""
    return {str(path.relative_to(self.root)): (
              path.stat().st_mode,
              hashlib.sha256(path.read_bytes()).hexdigest()
              if path.is_file() else None)
            for path in self.root.rglob('*')}

  def verify(self):
    before = self.snapshot()
    result = subprocess.run(
      ['bash', str(VERIFIER), '--root', str(self.root)],
      capture_output=True, text=True, env=self.env, check=False)
    self.assertEqual(self.snapshot(), before, 'Verification changed the image')
    commands = ([json.loads(line) for line in self.log.read_text().splitlines()]
                if self.log.exists() else [])
    self.assertFalse(any(command[0] in ['depmod', 'uname']
                         for command in commands), commands)
    return result, commands

  def rejects(self, message):
    result, commands = self.verify()
    self.assertNotEqual(result.returncode, 0, result.stdout)
    self.assertIn(message, result.stderr)
    return commands

  def test_success_queries_the_fixture_kernel_and_preserves_indexes(self):
    result, commands = self.verify()
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn(f'Verified module and dependencies for {KERNEL}', result.stdout)
    self.assertEqual(commands, [
      ['pacman', '--root', str(self.root), '--config', '/dev/null', '-Q',
       'zfs-dkms', 'zfs-utils', 'linux', 'linux-headers'],
      ['modinfo', '-b', str(self.root), '-k', KERNEL, '-F', 'vermagic', 'zfs'],
      ['modprobe', '--dirname', str(self.root), '--set-version', KERNEL,
       '--show-depends', 'zfs']])

  def test_missing_installed_packages_are_rejected(self):
    self.env['TEST_ZFS_PACMAN_FAIL'] = '1'
    commands = self.rejects('Missing required packages')
    self.assertEqual([command[0] for command in commands], ['pacman'])

  def test_linux_and_headers_package_versions_must_match(self):
    self.env['TEST_ZFS_HEADERS'] = '6.18.2.arch1-1'
    commands = self.rejects('linux and linux-headers versions differ')
    self.assertEqual([command[0] for command in commands], ['pacman'])

  def test_missing_headers_are_rejected(self):
    (self.modules / 'build').rmdir()
    self.rejects(f'Missing headers: {KERNEL}')

  def test_missing_or_empty_dependency_indexes_are_rejected(self):
    for name in ['modules.dep', 'modules.dep.bin']:
      for empty in [False, True]:
        with self.subTest(index=name, empty=empty):
          index = self.modules / name
          original = index.read_bytes()
          if empty:
            index.write_bytes(b'')
          else:
            index.unlink()
          self.rejects('Missing module dependency indexes')
          index.write_bytes(original)

  def test_missing_zfs_module_is_rejected(self):
    self.env['TEST_ZFS_MODINFO_FAIL'] = '1'
    self.rejects(f'No ZFS module for {KERNEL}')

  def test_wrong_module_vermagic_is_rejected(self):
    self.env['TEST_ZFS_VERMAGIC'] = '6.18.2-arch1-1 SMP preempt mod_unload'
    commands = self.rejects('Wrong module vermagic')
    self.assertEqual([command[0] for command in commands], ['pacman', 'modinfo'])

  def test_unresolved_module_dependencies_are_rejected(self):
    self.env['TEST_ZFS_MODPROBE_FAIL'] = '1'
    self.rejects(f'Unresolved ZFS module dependencies: {KERNEL}')

  def test_exactly_one_linux_module_tree_is_required(self):
    shutil.rmtree(self.modules)
    self.rejects('Expected one linux kernel; found 0')
    self.module_tree(KERNEL)
    self.module_tree('another-kernel')
    # Both fixture queries succeed so the tree count itself is exercised.
    self.env['TEST_ZFS_VERMAGIC'] = 'from-argument'
    self.rejects('Expected one linux kernel; found 2')


if __name__ == '__main__':
  unittest.main()
