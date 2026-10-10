#!/usr/bin/env python3
"""Offline builder and inventory checks; no sudo or real ISO build."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


class BuildCoresTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)
    self.base = self.root / 'base'
    self.base.mkdir()
    (self.base / 'airootfs').mkdir()
    (self.base / 'profiledef.sh').write_text(
      'declare -A file_permissions=()\n'
      "airootfs_image_type='squashfs'\n"
      "airootfs_image_tool_options=('-comp' 'xz')\n")
    (self.base / 'packages.x86_64').write_text('linux\nmkinitcpio\n')
    (self.base / 'pacman.conf').write_text('[options]\n')
    self.bin = self.root / 'bin'
    self.bin.mkdir()
    self.command('nproc', '#!/bin/sh\nprintf "%s\\n" "$TEST_CPUS"\n')
    self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                'TEST_CPUS': '16'}
    self.runs = 0

  def tearDown(self):
    self.temp.cleanup()

  def command(self, name, text):
    path = self.bin / name
    path.write_text(text)
    path.chmod(0o755)

  def run_builder(self, *options):
    self.runs += 1
    output = self.root / f'build-{self.runs}'
    result = subprocess.run(
      ['bash', str(KIT / 'build-iso.sh'), '--base-profile', str(self.base),
       '--output', str(output), *options], env=self.env,
      capture_output=True, text=True, check=False)
    return result, output

  def check_profile(self, expected, *options):
    result, output = self.run_builder(*options)
    self.assertEqual(result.returncode, 0, result.stderr)
    run = next(output.glob('run-*'))
    profile = run / 'profile/profiledef.sh'
    values = subprocess.check_output(
      ['bash', '-c', ('source "$1"; '
       'printf "%s\\n" "${airootfs_image_tool_options[@]}"'),
       'test-profile', str(profile)], text=True).splitlines()
    self.assertEqual(values, ['-comp', 'xz', '-processors', str(expected)])
    manifest = json.loads((run / 'manifest.json').read_text())
    self.assertEqual(manifest['cores'], expected)
    self.assertEqual(manifest['sha256']['profiledef.sh'],
                     hashlib.sha256(profile.read_bytes()).hexdigest())
    self.assertIn(f'Build cores: {expected}', result.stdout)
    return run

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  def test_default_uses_half_available_cpus(self):
    for available, expected in [(1, 1), (2, 1), (3, 1), (8, 4), (16, 8)]:
      with self.subTest(available=available):
        self.env['TEST_CPUS'] = str(available)
        self.check_profile(expected)

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  def test_explicit_core_counts(self):
    for options, expected in [(['--cores', '3'], 3), (['-j6'], 6),
                              (['--cores=5'], 5), (['-j', '2'], 2),
                              (['-j3', '--cores', '7'], 7)]:
      with self.subTest(options=options):
        self.check_profile(expected, *options)

  def test_invalid_core_counts_create_no_build(self):
    for options in [['--cores'], ['-j'], ['--cores', '0'], ['-j0'],
                    ['-j-1'], ['-jfoo'], ['--cores', ''], ['--cores='],
                    ['--cores', '1.5'], ['--cores', '--build']]:
      with self.subTest(options=options):
        result, output = self.run_builder(*options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Error:', result.stderr)
        self.assertFalse(output.exists())

  def mock_build_commands(self):
    # Replace privileged commands; exercise the real taskset and env invocation.
    self.command('sudo', '''#!/bin/sh
case "$1" in
  taskset) exec "$@" ;;
  arch-chroot)
    if [ "$3" = /usr/local/lib/iso-kit/verify-zfs ]; then
      printf 'fixture ZFS verification\\n'
      exit "${TEST_ZFS_CHECK_EXIT:-0}"
    fi
    if [ -n "$TEST_PACMAN" ]; then
      root=$2
      shift 3
      exec "$TEST_PACMAN" --config "$root/etc/pacman.conf" \\
        --dbpath "$root/var/lib/pacman" "$@"
    fi
    printf 'linux fixture\\n'
    ;;
  chown) exit 0 ;;
  *) exit 1 ;;
esac
''')
    for name in ['arch-chroot', 'xorriso']:
      self.command(name, '#!/bin/sh\nexit 0\n')
    self.command('mkarchiso', '''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
work = Path(args[args.index('-w') + 1])
output = Path(args[args.index('-o') + 1])
root = work / 'x86_64/airootfs'
(root / 'etc').mkdir(parents=True)
shutil.copyfile(Path(args[-1]) / 'airootfs/etc/pacman.conf',
                root / 'etc/pacman.conf')
# Archiso retains the local database but deletes all sync databases.
local = root / 'var/lib/pacman/local/linux-1.0-1'
local.mkdir(parents=True)
shutil.copyfile('/var/lib/pacman/local/ALPM_DB_VERSION',
                local.parent / 'ALPM_DB_VERSION')
(local / 'desc').write_text('%NAME%\\nlinux\\n\\n%VERSION%\\n1.0-1\\n')
(output / 'fixture.iso').write_text('offline fixture')
(work.parent / 'cpu-check.json').write_text(json.dumps({
  'cpus': sorted(os.sched_getaffinity(0)),
  'makeflags': os.environ['MAKEFLAGS']}))
''')

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  @unittest.skipUnless(Path('/etc/arch-release').exists() and
                       shutil.which('taskset'), 'Arch/taskset build preflight')
  def test_build_limits_cpu_affinity_and_make_jobs(self):
    self.mock_build_commands()
    self.env['TEST_PACMAN'] = ''
    run = self.check_profile(2, '--build', '-j2')
    observed = json.loads((run / 'cpu-check.json').read_text())
    self.assertEqual(observed['cpus'], sorted(os.sched_getaffinity(0))[:2])
    self.assertEqual(observed['makeflags'], '-j2')
    self.assertTrue((run / 'out/fixture.iso').is_file())

  def mock_zfs_package_helper(self):
    # Dispatch the real wrapper normally, replacing only its package helper.
    # Archives are real; staging uses the host's pacman and repo-add offline.
    self.command('bash', '''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
if Path(sys.argv[1]).name != 'build-zfs-packages.sh':
  os.execv('/usr/bin/bash', ['/usr/bin/bash', *sys.argv[1:]])
if os.environ.get('TEST_ZFS_PACKAGES_EXIT'):
  sys.exit(int(os.environ['TEST_ZFS_PACKAGES_EXIT']))
args = sys.argv[2:]
output = Path(args[args.index('--output') + 1])
output.mkdir()
(output.parent / 'package-args.json').write_text(json.dumps(args))
metadata = output.parent / '.PKGINFO'
for name in ['zfs-utils', 'zfs-dkms']:
  text = (f'pkgname = {name}\\npkgbase = {name}\\npkgver = 2.4.4-1\\n'
          'pkgdesc = Test fixture\\narch = any\\nsize = 1\\n'
          'builddate = 1\\npackager = Test\\n')
  if name == 'zfs-dkms':
    text += 'depend = zfs-utils=2.4.4\\n'
  metadata.write_text(text)
  subprocess.run(['bsdtar', '--zstd', '-cf',
                  str(output / f'{name}-2.4.4-1-any.pkg.tar.zst'),
                  '-C', str(metadata.parent), '.PKGINFO'], check=True)
metadata.unlink()
''')

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  @unittest.skipUnless(Path('/etc/arch-release').exists() and
                       all(shutil.which(c) for c in
                           ['taskset', 'pacman', 'repo-add', 'bsdtar',
                            'vercmp']), 'Arch local package tooling')
  def test_integrated_zfs_build_and_failed_validation(self):
    self.mock_build_commands()
    self.mock_zfs_package_helper()
    self.env['TEST_PACMAN'] = ''
    options = ['--build-zfs-packages', '--trust-local-packages', '--build',
               '--zfs-sources', str(self.root / 'recipes'),
               '--snapshot', '2026/01/01', '-j2']
    result, output = self.run_builder(*options)
    self.assertEqual(result.returncode, 0, result.stderr)
    run = next(output.glob('run-*'))
    args = json.loads((run / 'package-args.json').read_text())
    self.assertEqual(args[args.index('--snapshot') + 1], '2026/01/01')
    self.assertEqual(args[args.index('--cores') + 1], '2')
    self.assertEqual(args[args.index('--sources') + 1],
                     str(self.root / 'recipes'))
    self.assertIn('--build', args)
    self.assertTrue(json.loads((run / 'manifest.json').read_text())['zfs'])
    self.assertTrue((run / 'out/fixture.iso').exists())
    self.assertIn('fixture ZFS verification',
                  (run / 'zfs-check.log').read_text())
    self.env['TEST_ZFS_CHECK_EXIT'] = '1'
    result, output = self.run_builder(*options)
    self.assertNotEqual(result.returncode, 0)
    run = next(output.glob('run-*'))
    self.assertTrue((run / 'candidate/fixture.iso').exists())
    self.assertFalse((run / 'out/fixture.iso').exists())
    self.assertFalse((run / 'out/SHA256SUMS').exists())

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  @unittest.skipUnless(Path('/etc/arch-release').exists() and
                       all(shutil.which(c) for c in
                           ['taskset', 'pacman', 'repo-add', 'bsdtar',
                            'vercmp']), 'Arch local package tooling')
  def test_failed_zfs_package_build_stops_before_staging(self):
    self.mock_build_commands()
    self.mock_zfs_package_helper()
    self.env['TEST_ZFS_PACKAGES_EXIT'] = '9'
    result, output = self.run_builder('--build-zfs-packages',
                                      '--trust-local-packages', '--build')
    self.assertNotEqual(result.returncode, 0)
    run = next(output.glob('run-*'))
    self.assertTrue((run / 'zfs-packages.log').exists())
    self.assertFalse((run / 'profile').exists())
    self.assertFalse((run / 'work').exists())

  def test_invalid_zfs_options_create_no_build(self):
    for options in [['--zfs'], ['--build-zfs-packages'],
                    ['--build-zfs-packages', '--build'],
                    ['--build-zfs-packages', '--build',
                     '--trust-local-packages', '--local-packages', 'unused']]:
      with self.subTest(options=options):
        result, output = self.run_builder(*options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Error:', result.stderr)
        self.assertFalse(output.exists())

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  @unittest.skipUnless(Path('/etc/arch-release').exists() and
                       shutil.which('taskset') and shutil.which('pacman'),
                       'Arch/taskset/pacman build preflight')
  def test_inventory_without_sync_databases(self):
    self.mock_build_commands()
    self.env['TEST_PACMAN'] = shutil.which('pacman')
    result, output = self.run_builder('--build', '-j2')
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertNotIn('database file', result.stderr)
    self.assertNotIn("use '-Sy'", result.stderr)
    run = next(output.glob('run-*'))
    self.assertEqual((run / 'installed-packages.txt').read_text(),
                     'linux 1.0-1\n')
    self.assertTrue((run / 'out/fixture.iso').is_file())


if __name__ == '__main__':
  unittest.main()
