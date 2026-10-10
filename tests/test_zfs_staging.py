#!/usr/bin/env python3
"""Exercise local ZFS archives and repository assembly without installations."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('zfs_stage', KIT / 'lib/stage.py')
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)
ARCHIVE_TOOLS = all(shutil.which(name) for name in ['bsdtar', 'pacman'])


@unittest.skipUnless(ARCHIVE_TOOLS, 'Archive checks need bsdtar and pacman')
class ZfsArchiveTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)
    self.packages = self.root / 'packages'
    self.packages.mkdir()
    self.sequence = 0

  def tearDown(self):
    self.temp.cleanup()

  def archive(self, name, version='2.4.0-1', arch='x86_64', depends=()):
    """Build genuine package metadata, read by the installed pacman tools."""
    self.sequence += 1
    content = self.root / f'content-{self.sequence}'
    content.mkdir()
    metadata = [f'pkgname = {name}', f'pkgbase = {name}',
                f'pkgver = {version}', 'pkgdesc = Offline ZFS fixture',
                'builddate = 1760000000', 'packager = Offline test',
                'size = 8', f'arch = {arch}', 'license = CDDL']
    metadata += [f'depend = {dependency}' for dependency in depends]
    (content / '.PKGINFO').write_text('\n'.join(metadata) + '\n')
    payload = content / 'usr/share/offline-fixtures' / name
    payload.parent.mkdir(parents=True)
    payload.write_text('fixture\n')
    archive = self.packages / f'{name}-{version}-{arch}.pkg.tar.zst'
    subprocess.run(['bsdtar', '--zstd', '-cf', str(archive), '-C',
                    str(content), '.PKGINFO', 'usr'],
                   check=True, capture_output=True)
    return archive

  def pair(self, dkms='2.4.0-1', utils='2.4.0-1', depends=()):
    return [self.archive('zfs-dkms', dkms, depends=depends),
            self.archive('zfs-utils', utils)]

  def test_empty_and_recipe_directories_require_built_archives(self):
    with self.assertRaisesRegex(ValueError, 'No .* archives found'):
      stage.checked_archives(self.packages, True)
    recipe = self.packages / 'zfs-dkms'
    recipe.mkdir()
    (recipe / 'PKGBUILD').write_text('pkgname=zfs-dkms\n')
    with self.assertRaisesRegex(ValueError, 'PKGBUILDs must be built first'):
      stage.checked_archives(self.packages, True)
    with self.assertRaisesRegex(ValueError, 'does not exist'):
      stage.checked_archives(self.root / 'missing', True)

  def test_both_zfs_packages_are_required(self):
    for name in ['zfs-utils', 'zfs-dkms']:
      with self.subTest(package=name):
        package = self.archive(name)
        with self.assertRaisesRegex(ValueError, 'both zfs-dkms and zfs-utils'):
          stage.checked_archives(self.packages, True)
        package.unlink()

  def test_duplicate_package_names_are_rejected(self):
    self.pair()
    self.archive('zfs-utils', '2.4.0-2')
    with self.assertRaisesRegex(ValueError, 'Multiple versions.*zfs-utils'):
      stage.checked_archives(self.packages, True)

  def test_other_architectures_and_symlink_archives_are_rejected(self):
    package = self.archive('zfs-utils', arch='aarch64')
    with self.assertRaisesRegex(ValueError, 'not for x86_64'):
      stage.checked_archives(self.packages, False)
    package.unlink()
    package = self.archive('zfs-utils')
    alias = self.packages / 'alias.pkg.tar.zst'
    alias.symlink_to(package)
    with self.assertRaisesRegex(ValueError, 'regular file'):
      stage.checked_archives(self.packages, False)

  def test_different_zfs_source_releases_are_rejected(self):
    self.pair(utils='2.3.5-1')
    with self.assertRaisesRegex(ValueError, 'same ZFS release'):
      stage.checked_archives(self.packages, True)

  def test_different_package_releases_for_same_source_are_allowed(self):
    packages = self.pair(dkms='2.4.0-2', utils='2.4.0-5')
    found = stage.checked_archives(self.packages, True)
    self.assertEqual({path for path, _ in found}, set(packages))
    self.assertEqual({name for _, name in found}, {'zfs-dkms', 'zfs-utils'})

  @unittest.skipUnless(shutil.which('vercmp'), 'Version pins need vercmp')
  def test_zfs_utils_version_constraints_are_enforced(self):
    for dependency in ['zfs-utils=2.4.0-2', 'zfs-utils>2.4.0-1',
                       'zfs-utils<2.4.0-1', 'zfs-utils>=2.4.0-2',
                       'zfs-utils<=2.4.0-0']:
      with self.subTest(dependency=dependency):
        packages = self.pair(depends=[dependency])
        with self.assertRaisesRegex(ValueError, 'does not satisfy'):
          stage.checked_archives(self.packages, True)
        for package in packages:
          package.unlink()
    self.pair(dkms='2.4.0-2', utils='2.4.0-5',
              depends=['zfs-utils>=2.4.0-2', 'zfs-utils<=2.4.0-5'])
    self.assertEqual(len(stage.checked_archives(self.packages, True)), 2)

  @unittest.skipUnless(shutil.which('repo-add'), 'Staging needs repo-add')
  def test_staging_uses_exact_archives_and_separate_live_configuration(self):
    packages = self.pair()
    hashes = {package.name: hashlib.sha256(package.read_bytes()).hexdigest()
              for package in packages}
    base = self.root / 'base'
    base.mkdir()
    (base / 'airootfs').mkdir()
    (base / 'profiledef.sh').write_text(
      'declare -A file_permissions=()\n'
      "bootmodes=('bios.syslinux' 'uefi.systemd-boot')\n")
    (base / 'packages.x86_64').write_text('linux\nmkinitcpio\n')
    original_conf = ('[options]\nIgnorePkg = linux\n'
                     '[unofficial]\nServer = https://untrusted.invalid\n')
    (base / 'pacman.conf').write_text(original_conf)
    run = self.root / 'run'
    run.mkdir()
    result = subprocess.run(
      [sys.executable, str(KIT / 'lib/stage.py'), '--kit', str(KIT),
       '--base', str(base), '--run', str(run), '--preset', 'rescue',
       '--zfs', '--local-packages', str(self.packages)],
      capture_output=True, text=True, check=False)
    self.assertEqual(result.returncode, 0, result.stderr)
    profile = run / 'profile'
    conf = (profile / 'pacman.conf').read_text()
    live_conf = (profile / 'airootfs/etc/pacman.conf').read_text()
    for text in [conf, live_conf]:
      self.assertIn('[core]', text)
      self.assertIn('[extra]', text)
      self.assertIn('SigLevel = Required DatabaseOptional', text)
      self.assertNotIn('unofficial', text)
      self.assertNotIn('IgnorePkg', text)
    self.assertIn(f'Server = file://{run / "repo"}', conf)
    self.assertIn('PackageOptional DatabaseOptional', conf)
    self.assertNotIn('file://', live_conf)
    self.assertNotIn('[iso-local]', live_conf)
    self.assertEqual((base / 'pacman.conf').read_text(), original_conf)
    names = set((profile / 'packages.x86_64').read_text().splitlines())
    self.assertTrue({'linux', 'linux-headers', 'base-devel', 'dkms',
                     'zfs-dkms', 'zfs-utils', 'libaio', 'libtirpc',
                     'openssl', 'zlib'} <= names)
    manifest = json.loads((run / 'manifest.json').read_text())
    self.assertTrue(manifest['zfs'])
    self.assertEqual(set(manifest['packages']), names)
    repo = run / 'repo'
    database = repo / 'iso-local.db.tar.gz'
    members = subprocess.check_output(['bsdtar', '-tf', str(database)],
                                      text=True).splitlines()
    for package in packages:
      self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(),
                       hashes[package.name])
      self.assertEqual((repo / package.name).read_bytes(), package.read_bytes())
      self.assertEqual(manifest['sha256'][f'repo/{package.name}'],
                       hashes[package.name])
      name = subprocess.check_output(['pacman', '-Qp', str(package)],
                                     text=True).split()[0]
      member = next(item for item in members
                    if item.startswith(f'{name}-') and item.endswith('/desc'))
      description = subprocess.check_output(
        ['bsdtar', '-xOf', str(database), member], text=True)
      self.assertIn(f'%FILENAME%\n{package.name}\n', description)
      self.assertIn(f'%SHA256SUM%\n{hashes[package.name]}\n', description)
    root = profile / 'airootfs'
    for service in ['zfs-import-cache.service', 'zfs-import-scan.service']:
      mask = root / 'etc/systemd/system' / service
      self.assertTrue(mask.is_symlink())
      self.assertEqual(str(mask.readlink()), '/dev/null')
    helper = root / 'usr/local/lib/iso-kit/verify-zfs'
    self.assertTrue(helper.stat().st_mode & 0o100)
    definition = (profile / 'profiledef.sh').read_text()
    self.assertIn('file_permissions["/usr/local/lib/iso-kit/verify-zfs"]='
                  '"0:0:755"', definition)
    subprocess.run(['bash', '-n', str(profile / 'profiledef.sh')], check=True)


if __name__ == '__main__':
  unittest.main()
