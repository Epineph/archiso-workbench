# Project context

Updated 10 October 2026 for the ZFS build integration.

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
- Added `--build-zfs-packages`, implying `--zfs` and requiring both `--build`
  and `--trust-local-packages`. The helper copies reviewed local recipes,
  builds utilities first in a clean Arch chroot, and injects their verified
  archive when building DKMS. It never installs those packages on the host.
- Source recipe directories contain no installable archives. The helper reads
  `.SRCINFO` without executing recipes in plan mode; `--build` verifies generated
  metadata and preserves source-signature verification. Missing signing keys
  must be verified and supplied by the user. No recipes or keys are fetched
  automatically. Snapshot builds use the same official archive for compilation
  and ISO packages.
- Local archive staging validates package metadata, x86_64/any architecture,
  matching OpenZFS releases and the DKMS package's utilities dependency pins.
  Include runtime libraries explicitly because the current utilities recipe
  does not declare them. The user's recipe checkouts and `packages/zfs.txt`
  remain unchanged; that text file is not the selector for ZFS support.
- Final ZFS checks are read-only: never run `depmod` after compression. Require
  matching linux/header versions, existing module indexes, correct vermagic,
  and resolvable module dependencies for the ISO kernel. Pacman's DKMS hook
  performs module compilation and index generation before compression.
- Real releng staging succeeded for rescue (142 packages), personal (177), and
  rescue-ZFS (151, using synthetic archives). Official packages resolve with
  cached `pacman -Sp` metadata; no refreshes or installations were performed.
- Review caught a devtools requirement: create the chroot parent before invoking
  `mkarchroot`, while leaving its `root` directory nonexistent. The helper test
  now checks this prerequisite instead of silently creating missing parents.
- A fresh privileged package/ISO build and VM/ZFS checks remain unperformed;
  see `docs/VALIDATION.md`. The restricted session cannot invoke real sudo.
- All 45 tests and relevant static checks passed. Commands include
  `python3 -m unittest discover -s tests -v`, Bash syntax,
  ShellCheck, `shfmt -d -i 2 -ci -sr` for the wrapper/helper/verifier, Ruff for
  staging and changed/new tests, and `git diff --check`.
- Both builder help texts explain recipe/archive choices, required flags,
  script-relative defaults, chosen snapshots, retained results and VM checks.
- Existing `builds/` runs and images were retained. The next user build command
  for ZFS is `./build-iso.sh --preset personal --build-zfs-packages
  --trust-local-packages --build` after recipe/key review and host prerequisites.
