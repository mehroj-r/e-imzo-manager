# E-IMZO Manager for Arch and Omarchy

A community fork of [Xinux's E-IMZO Manager](https://github.com/xinux-org/e-imzo-manager), based on upstream 1.3.0. This fork adds a tested TLS hostname correction and native Arch packaging with a GitHub-only installer.

**Supported:** up-to-date x86_64 Arch Linux and Omarchy, with GTK4 >=4.22, GLib >=2.88 and libadwaita >=1.9. This is not an official vendor release or a universal Linux binary. Other architectures and older distributions are not supported by this release.

## Installation

Install [v1.3.0-arch.1](https://github.com/mehroj-r/e-imzo-manager/releases/tag/v1.3.0-arch.1) from your ordinary desktop terminal. This downloads the pinned installer to a private temporary file before executing it:

```bash
(
  set -eu
  installer="$(mktemp)"
  trap 'rm -f -- "$installer"' EXIT
  curl --fail --show-error --location \
    --proto '=https' --proto-redir '=https' \
    'https://github.com/mehroj-r/e-imzo-manager/releases/download/v1.3.0-arch.1/install.sh' \
    --output "$installer"
  bash "$installer"
)
```

Read `install.sh` before running it if desired. Public release downloads, checksums and the installer's `--dry-run` path have been verified. The release includes a prebuilt manager, source bundle, checksums and sanitized build information; it does not redistribute the Java backend.

The installer will:

1. Download the prebuilt manager from a version-pinned GitHub Release and verify its embedded SHA-256.
2. Download the official E-IMZO 6.4.7 signing backend directly from `dls.soliq.uz`, verify its pinned SHA-256, and assemble a native backend package locally.
3. Install required runtime libraries and Java 17 through the official Arch repositories.
4. Install the manager and backend through one reviewed pacman transaction, with confirmation before replacing conflicting old backend packages.
5. Configure the signing service for the current user, preserving disabled-service preferences on upgrades.

No Nix, AUR access, AUR helper, GitHub login, Rust compiler or end-user compilation is required. Run the installer as your ordinary desktop user, not with `sudo bash`; it requests sudo only for package installation. It does not run a full system upgrade or refresh repository databases. If installed libraries are too old, update the system deliberately first.

Unsigned local packages must be permitted by your existing pacman policy. The installer will not weaken signature policy or import package-signing keys.

### Existing installations

The generated `e-imzo-backend` package provides `e-imzo=6.4.7` and conflicts with the older `e-imzo` and `e-imzo-bin` packages. Pacman asks before replacing them in the same transaction; the installer does not remove a working backend first.

A previous user-local manager can shadow the system package. The installer lists matching manager files and asks before moving them to a timestamped backup. It does not delete signing keys, user preferences or shared directories. Do not use blanket `--overwrite`, dependency-bypass flags, or remove your signing-key directory to resolve installation problems.

## Run and service management

The packaged application is `/usr/bin/e-imzo-manager`, available through the desktop application launcher.

```bash
/usr/bin/e-imzo-manager
systemctl --user status e-imzo.service
```

The signing service uses a Java 17-specific executable rather than changing the machine-wide Java default. The backend listens on loopback ports 64443 and 64646; the manager connects to `wss://127.0.0.1:64443/service/cryptapi`.

To opt into service autostart, or disable it explicitly:

```bash
systemctl --user enable --now e-imzo.service
systemctl --user disable --now e-imzo.service
```

Use the **user** service, not `sudo systemctl` or root's user manager.

## What is fixed

Upstream SDK 0.4.0 passed a TCP address such as `127.0.0.1:64443` as the TLS server name. Java rejects the colon/port in that field with an illegal-parameter TLS alert. The vendored client now passes only `url.host_str()` to TLS, while leaving the TCP destination unchanged.

A regression test calls the production client against a strict local TLS/WebSocket server. The fixed version passes; restoring the original argument fails. SDK 0.4.1 retains the original defect, so merely updating that dependency does not resolve it.

The manager also detects an installed systemd user unit independently of autostart, so an intentionally disabled service is not mistaken for a missing backend. The missing-service link points to this fork's installation instructions rather than the NixOS package website.

The Java backend is not patched. The client library's existing certificate-verification behavior is unchanged; no additional TLS exceptions or protocol downgrades are introduced. See [SDK provenance and patch details](vendor/e-imzo/PATCHES.md).

## Signing keys, browsers and hardware

The installer does **not** import or create keys, sign documents, modify browser/system certificate trust, or change USB permissions.

- Browser-based signing may need a separate, deliberate certificate setup. The vendor certificate is at `/usr/share/e-imzo/E-IMZO.pem`; consult the bundled `/usr/share/e-imzo/README.txt`. Do not disable certificate validation globally. The manager itself does not require adding this certificate to system trust.
- The vendor warns that PFX keys **created on Java 17 or newer** may not work on Java 8 or the E-IMZO-ID-card mobile application. This release does not recreate or convert existing keys.
- Smart-card/ID-card readers need separate PC/SC and device configuration; file-based PFX use does not require automatically installing or enabling that hardware stack.

## Updates and uninstall

Use the installer from the desired tagged release for an update. Pacman tracks installed files, versions and dependencies. The backend is fetched from its official source, not republished as a GitHub asset.

Before uninstalling, stop and disable the current user's signing service, then remove the packages:

```bash
systemctl --user disable --now e-imzo.service
sudo pacman -R e-imzo-manager e-imzo-backend
systemctl --user daemon-reload
```

This preserves signing keys and user preferences. Review pacman's transaction before confirming; do not use recursive removal to delete unrelated runtime packages. If the installer backed up a user-local manager, retain that backup until the system installation is verified and use its recorded paths for an intentional restoration.

## Development and packaging

Use Arch's packaged Rust >=1.93, Meson >=1.10.1, Ninja, pkg-config, gettext, GTK4/libadwaita/GLib/OpenSSL, desktop-file-utils and AppStream. The upstream Rustup toolchain file requests Miri on stable; the Arch-packaged `/usr/bin/cargo` avoids that component request.

```bash
meson setup build --prefix=/usr --buildtype=release -Dprofile=default
meson compile -C build
meson test --no-rebuild -C build --print-errorlogs
CARGO_HOME="$PWD/build/cargo-home" cargo test --locked --release \
  --target-dir "$PWD/build/src" --all-targets
python3 -m unittest discover -s tests -p 'test_*.py'
```

Meson writes generated configuration containing the installation prefix. Do not distribute a binary built with a personal home-directory prefix or install just the executable: the external resource bundle and other assets are required.

`packaging/arch/PKGBUILD` is used only to build release packages; it is not an AUR submission. GitHub CI validates the Rust regression, application metadata, installer and package before release publication. Release source bundles contain the matching modified source, dependency sources and build/install instructions. Raw build-directory logs are not release assets because some upstream tools record inherited environment variables.

### Maintainer release build

Inside a provisioned, isolated Arch x86_64 build environment, as a non-root build user, use a clean tracked checkout and an empty output directory:

```bash
bash packaging/build-release.sh v1.3.0-arch.1 /tmp/e-imzo-release-assets
```

In addition to the compiler/library inputs above, the builder needs base-devel, Git, Python, ShellCheck, libarchive, zstd and binutils; CI also validates workflows with actionlint. The helper uses makepkg with separately provisioned build dependencies, verifies the production TLS regression and mutation gate, and emits only the manager package, pinned installer, checksums, source bundle and sanitized build information. It does not download or publish the Java backend.

For uncommitted local verification, explicitly pass `--working-tree` before the tag argument. Such assets are labeled as a working-tree snapshot and are **not release assets**. Published releases are built from the exact clean tagged commit.

The source bundle includes vendored registry sources and offline Cargo configuration. Preserve `vendor/e-imzo`, `.cargo/config.toml` and the dependency-source directory together when rebuilding. The usual Meson commands above apply after extracting the source bundle; the release's build information records the exact builder/toolchain versions. The source distribution retains public upstream dependency test fixtures and notices, not user signing keys or local settings.

## Provenance and licenses

Original manager: Xinux Developers, [upstream repository](https://github.com/xinux-org/e-imzo-manager) and [canonical upstream project](https://git.oss.uzinfocom.uz/xinux/e-imzo-manager). Arch packaging and the documented SDK correction were added in this fork on 2026-10-03.

The manager retains its upstream AGPL license: [LICENSE-AGPL](LICENSE-AGPL). Documentation/metadata retain the relevant [CC-BY-4.0 terms](LICENSE-CCBY). The vendored Rust SDK is MIT OR Apache-2.0; both notices are included. This fork corrects inconsistent application/package license metadata rather than relicensing upstream code.

The Java signing backend is vendor software. Redistribution permission has not been established, so this repository and its releases do not host the backend archive, JARs or generated backend package. The installer downloads unchanged vendor bytes for installation on the user's machine; their availability and authenticity are separate from this fork's manager source license.
