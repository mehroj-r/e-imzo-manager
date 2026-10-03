#!/usr/bin/env bash
# Run as an ordinary user inside an isolated, fully provisioned Arch x86_64 builder.
set -euo pipefail
usage() { printf 'Usage: %s [--working-tree] RELEASE_TAG OUTPUT_DIRECTORY\n' "$0" >&2; }
working_tree=()
if [[ ${1:-} == --working-tree ]]; then working_tree=(--working-tree); shift; fi
[[ $# == 2 ]] || { usage; exit 2; }
release_tag=$1
[[ $release_tag == v1.3.0-arch.1 ]] || { printf 'This packaging revision targets v1.3.0-arch.1 only\n' >&2; exit 1; }
[[ $(uname -m) == x86_64 && $EUID != 0 ]] || { printf 'Use a non-root x86_64 Arch build user\n' >&2; exit 1; }
[[ -r /etc/arch-release ]] || { printf 'An isolated Arch builder is required\n' >&2; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# Check before executing any worktree helper or trusting its release metadata.
if [[ ${#working_tree[@]} == 0 ]] && [[ -n $(git -C "$root" status --porcelain --untracked-files=normal) ]]; then
  printf 'Release sources require clean HEAD (including helpers/templates); use --working-tree only for local verification\n' >&2
  exit 1
fi
source_commit=$(git -C "$root" rev-parse HEAD)
output=$(realpath -m -- "$2")
if [[ -d $output ]] && [[ -n $(find "$output" -mindepth 1 -print -quit) ]]; then
  printf 'Output directory must be empty: %s\n' "$output" >&2; exit 1
fi
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git -C "$root" show -s --format=%ct HEAD)
export MANAGER_SOURCE_ARCHIVE=e-imzo-manager-1.3.0-source.tar.zst
python "$root/packaging/source-archive.py" "${working_tree[@]}" "$work/$MANAGER_SOURCE_ARCHIVE"
export MANAGER_SOURCE_SHA256
MANAGER_SOURCE_SHA256=$(sha256sum "$work/$MANAGER_SOURCE_ARCHIVE")
MANAGER_SOURCE_SHA256=${MANAGER_SOURCE_SHA256%% *}
bsdtar -xOf "$work/$MANAGER_SOURCE_ARCHIVE" e-imzo-manager-1.3.0/packaging/arch/PKGBUILD > "$work/PKGBUILD"

# Resolve source compliance before spending time compiling. A local verification
# may seed this from an already checksum-verified, source-only cargo-vendor tree.
export CARGO_HOME="$work/source-cargo-home"
if [[ -n ${MANAGER_CARGO_VENDOR_SEED:-} ]]; then
  python - "$CARGO_HOME" "$MANAGER_CARGO_VENDOR_SEED" <<'PY'
import json, pathlib, sys
home, seed = map(pathlib.Path, sys.argv[1:])
if not seed.is_dir():
    raise SystemExit('Cargo source seed must be a vendored registry source directory')
home.mkdir(parents=True, exist_ok=True)
(home / 'config.toml').write_text('[source.crates-io]\nreplace-with = "vendored-sources"\n[source.vendored-sources]\ndirectory = ' + json.dumps(str(seed.resolve())) + '\n[net]\noffline = true\n')
PY
fi
# Keep the SDK path patch, every locked registry source and all license files.
mkdir "$work/compliance"
bsdtar -xf "$work/$MANAGER_SOURCE_ARCHIVE" -C "$work/compliance"
compliance="$work/compliance/e-imzo-manager-1.3.0"
mkdir -p "$compliance/.cargo"
(cd "$compliance" && cargo vendor --locked --respect-source-config --versioned-dirs cargo-vendor > .cargo/config.toml)
printf '\n[net]\noffline = true\n' >> "$compliance/.cargo/config.toml"
(cd "$compliance" && CARGO_HOME="$work/offline-cargo-home" cargo metadata --locked --offline --format-version 1 > /dev/null)
sources=e-imzo-manager-1.3.0-arch.1-sources.tar.zst
bsdtar --uid 0 --gid 0 --uname root --gname root -cf - -C "$work/compliance" e-imzo-manager-1.3.0 | zstd -q -19 -T0 -o "$work/$sources"

# Reuse only these verified registry sources, not a compiled build or PR artifact.
# Meson still owns its exact build/cargo-home; the package builds entirely offline.
export MANAGER_CARGO_VENDOR_SEED="$compliance/cargo-vendor"
export CARGO_NET_OFFLINE=true
# e-imzo is a runtime provide absent from official repos. CI installs all actual
# compile inputs beforehand. --nodeps is never an end-user install flag.
(cd "$work" && makepkg --nodeps --cleanbuild --noconfirm)
manager=e-imzo-manager-1.3.0-1-x86_64.pkg.tar.zst
[[ -f $work/$manager ]]
source_root="$work/src/e-imzo-manager-1.3.0"
bash "$source_root/packaging/validate-package.sh" "$work/$manager"
export CARGO_HOME="$source_root/build/cargo-home"

mkdir "$work/assets"
cp -- "$work/$manager" "$work/assets/$manager"
cp -- "$work/$sources" "$work/assets/$sources"
manager_sha=$(sha256sum "$work/assets/$manager")
manager_sha=${manager_sha%% *}
python "$source_root/packaging/render-installer.py" --template "$source_root/packaging/install.sh.in" \
  --output "$work/assets/install.sh" --release-tag "$release_tag" \
  --manager-package "$manager" --manager-sha256 "$manager_sha"
bash -n "$work/assets/install.sh"
shellcheck "$work/assets/install.sh"
python - "$source_commit" "$source_root" "$work/assets/build-info.json" "$release_tag" "$MANAGER_SOURCE_SHA256" "${working_tree[*]}" <<'PY'
import json
import os
import pathlib
import re
import subprocess
import sys

source_commit, source, output, tag, source_sha, dirty = sys.argv[1:]
def run(*command):
    return subprocess.check_output(command, text=True).strip()
packages = {}
for line in run('pacman', '-Q').splitlines():
    name, version = line.split()
    if not re.fullmatch(r'[a-zA-Z0-9@._+:-]+', name + version):
        raise SystemExit('Unexpected builder package version')
    packages[name] = version
elf = run('readelf', '--version-info', str(pathlib.Path(source) / 'build/src/e-imzo-manager'))
versions = set(re.findall(r'GLIBC_([0-9]+(?:\.[0-9]+)+)', elf))
openssl_versions = set(re.findall(r'OPENSSL_([0-9]+(?:\.[0-9]+)+)', elf))
dynamic = run('readelf', '-d', str(pathlib.Path(source) / 'build/src/e-imzo-manager'))
needed = sorted(re.findall(r'\(NEEDED\).*\[([^]]+)\]', dynamic))
if not openssl_versions or any(not re.fullmatch(r'[A-Za-z0-9+._-]+', library) for library in needed):
    raise SystemExit('Unexpected ELF OpenSSL or library requirements')
builder_image = os.environ.get('MANAGER_BUILDER_IMAGE')
if builder_image is not None and not re.fullmatch(r'archlinux@sha256:[0-9a-f]{64}', builder_image):
    raise SystemExit('Invalid builder image provenance')
info = {
    'release_tag': tag, 'package': 'e-imzo-manager-1.3.0-1-x86_64.pkg.tar.zst',
    'repository': 'https://github.com/mehroj-r/e-imzo-manager',
    'source_commit': source_commit,
    'upstream_commit': 'dced597aa49c43258e2f7899871374389fe02df0',
    'source_snapshot': 'working-tree-local-verification' if dirty else 'tracked-commit',
    'source_archive_sha256': source_sha, 'architecture': 'x86_64',
    'prefix': '/usr', 'profile': 'default/release',
    'minimum_glibc': max(versions, key=lambda v: tuple(map(int, v.split('.')))),
    'minimum_openssl': max(openssl_versions, key=lambda v: tuple(map(int, v.split('.')))),
    'elf_needed': needed,
    'rustc': run('rustc', '--version'), 'cargo': run('cargo', '--version'),
    'meson': run('meson', '--version'), 'builder_packages': packages,
    'builder_image': builder_image,
    'verification': ['locked-rust-tests', 'production-tls-regression', 'tls-mutation', 'meson-desktop-appstream-schema', 'python-unittests', 'package-manifest-elf-licenses', 'shellcheck'],
}
pathlib.Path(output).write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
PY
(cd "$work/assets" && sha256sum "$manager" install.sh build-info.json "$sources" > SHA256SUMS)
# Exactly five files, never the builder, caches, proprietary backend or raw logs.
mkdir -p -- "$output"
for asset in "$manager" install.sh SHA256SUMS build-info.json "$sources"; do
  cp -- "$work/assets/$asset" "$output/$asset"
done
printf 'Validated release assets written to %s\n' "$output"
if [[ ${#working_tree[@]} != 0 ]]; then printf 'LOCAL VERIFICATION ONLY: source is not a published commit\n'; fi
