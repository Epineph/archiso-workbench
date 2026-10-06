# Project context

Updated 6 October 2026 for the build-script correction.

- The user authorized fixing the builder and then asked to ignore problematic
  unofficial repositories. BioArchLinux/Chaotic-AUR setup was removed. The build
  continues to use official repositories and explicitly supplied local archives.
- Preserve the user's expanded `packages/personal.txt`; its complete selection
  resolved using cached official repository metadata on this Arch host.
- Archiso 91 removes `var/lib/pacman/sync` before image compression. Queries after
  `mkarchiso` must use `pacman --config /dev/null -Q` to read installed versions
  without missing-sync-database warnings. This applies to both the inventory and
  the ZFS verification helper.
- The removed repository block expanded unset `$arch`/`$repo`, redirected to the
  host's `/etc/pacman.conf`, and ran after ISO creation. Avoid post-build package
  or configuration mutations, which cannot affect the already compressed image.
- All 12 tests, Bash syntax, ShellCheck, shfmt, Ruff, and diff checks passed.
  Commands: `python3 -m unittest discover -s tests -v`,
  `shellcheck build-iso.sh overlay/zfs/usr/local/lib/iso-kit/verify-zfs`,
  `shfmt -d -i 2 -ci -sr build-iso.sh overlay/zfs/usr/local/lib/iso-kit/verify-zfs`,
  and `ruff check tests/test_build_cores.py`.
- Real releng staging succeeded. Package resolution used `pacman -Sp`, without
  refreshes or installations. A fresh privileged build and VM/ZFS checks remain
  unperformed; see `docs/VALIDATION.md`.
- Existing `builds/` runs and images were retained. The next user build command
  is `./build-iso.sh --preset personal --build`.
