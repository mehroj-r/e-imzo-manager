#!/usr/bin/env bash
# Validate only the native manager package; never load or run its ELF binary.
set -euo pipefail
[[ $# == 1 ]] || { printf 'Usage: %s manager.pkg.tar.zst\n' "$0" >&2; exit 2; }
package=$(realpath -- "$1")
[[ ${package##*/} == e-imzo-manager-1.3.0-1-x86_64.pkg.tar.zst ]] || { printf 'Unexpected package name\n' >&2; exit 1; }
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
python - "$package" "$staging" <<'PY'
import pathlib
import re
import subprocess
import sys
import tarfile

package, staging = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
required = {
    '.PKGINFO', '.BUILDINFO', '.MTREE', 'usr/bin/e-imzo-manager',
    'usr/share/e-imzo-manager/resources.gresource',
    'usr/share/applications/uz.xinux.EIMZOManager.desktop',
    'usr/share/metainfo/uz.xinux.EIMZOManager.metainfo.xml',
    'usr/share/glib-2.0/schemas/uz.xinux.EIMZOManager.gschema.xml',
    'usr/share/icons/hicolor/scalable/apps/uz.xinux.EIMZOManager.svg',
    'usr/share/icons/hicolor/symbolic/apps/uz.xinux.EIMZOManager-symbolic.svg',
    'usr/share/licenses/e-imzo-manager/LICENSE-AGPL',
    'usr/share/licenses/e-imzo-manager/LICENSE-CCBY',
    'usr/share/licenses/e-imzo-manager/e-imzo/LICENSE-MIT',
    'usr/share/licenses/e-imzo-manager/e-imzo/LICENSE-APACHE',
    'usr/share/licenses/e-imzo-manager/e-imzo/PATCHES.md',
}
seen = set()
files = set()
with subprocess.Popen(['zstd', '-qdc', str(package)], stdout=subprocess.PIPE) as process:
    with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
        for member in archive:
            name = member.name.rstrip('/')
            path = pathlib.PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or not name or name in seen:
                raise SystemExit(f'Unsafe or duplicate package path: {name}')
            seen.add(name)
            if member.uid != 0 or member.gid != 0:
                raise SystemExit(f'Non-root package ownership: {name}')
            if member.isdir():
                if name not in {'usr', 'usr/bin', 'usr/share'} and not name.startswith('usr/share/'):
                    raise SystemExit(f'Unexpected package directory: {name}')
                continue
            if not member.isfile() or member.mode & 0o6000:
                raise SystemExit(f'Links, special files and privileged modes forbidden: {name}')
            if name not in required and not re.fullmatch(r'usr/share/locale/[A-Za-z0-9_@.-]+/LC_MESSAGES/e-imzo-manager\.mo', name):
                raise SystemExit(f'Unexpected package payload: {name}')
            if member.size > 512 * 1024 * 1024:
                raise SystemExit(f'Oversized package file: {name}')
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
            files.add(name)
    if process.wait():
        raise SystemExit('Package decompression failed')
if required - files:
    raise SystemExit(f'Missing package payload: {sorted(required - files)}')
if not any(name.endswith('/e-imzo-manager.mo') for name in files):
    raise SystemExit('Translations are missing')
metadata = {}
for line in (staging / '.PKGINFO').read_text().splitlines():
    if ' = ' in line:
        key, value = line.split(' = ', 1)
        metadata.setdefault(key, []).append(value)
for key, value in {'pkgname': 'e-imzo-manager', 'pkgver': '1.3.0-1', 'arch': 'x86_64'}.items():
    if metadata.get(key) != [value]:
        raise SystemExit(f'Incorrect package metadata: {key}')
expected = {'gtk4>=1:4.22', 'glib2>=2.88', 'libadwaita>=1.9', 'openssl', 'polkit', 'systemd', 'e-imzo>=6.4.7'}
deps = set(metadata.get('depend', []))
glibc = [value for value in deps if re.fullmatch(r'glibc>=[0-9]+(?:\.[0-9]+)+', value)]
if len(glibc) != 1 or deps != expected | set(glibc):
    raise SystemExit(f'Incorrect runtime dependencies: {sorted(deps)}')
if set(metadata.get('license', [])) != {'AGPL-3.0-only', 'CC-BY-4.0', 'MIT', 'Apache-2.0'}:
    raise SystemExit('Incorrect license metadata')
desktop = (staging / 'usr/share/applications/uz.xinux.EIMZOManager.desktop').read_text()
if not re.search(r'^Exec=/usr/bin/e-imzo-manager(?:\s|$)', desktop, re.M):
    raise SystemExit('Desktop command must use /usr/bin/e-imzo-manager')
binary = staging / 'usr/bin/e-imzo-manager'
header = subprocess.check_output(['readelf', '-h', str(binary)], text=True)
if 'Advanced Micro Devices X86-64' not in header:
    raise SystemExit('ELF is not x86_64')
versions = subprocess.check_output(['readelf', '--version-info', str(binary)], text=True)
abi = set(re.findall(r'GLIBC_([0-9]+(?:\.[0-9]+)+)', versions))
if not abi:
    raise SystemExit('Cannot determine ELF glibc requirement')
minimum = max(abi, key=lambda version: tuple(map(int, version.split('.'))))
if glibc != [f'glibc>={minimum}']:
    raise SystemExit('Declared glibc floor does not match ELF')
openssl_versions = set(re.findall(r'OPENSSL_([0-9]+(?:\.[0-9]+)+)', versions))
if not openssl_versions:
    raise SystemExit('Cannot determine ELF OpenSSL requirement')
minimum_openssl = max(openssl_versions, key=lambda version: tuple(map(int, version.split('.'))))
if tuple(map(int, minimum_openssl.split('.'))) > (3, 0, 0):
    raise SystemExit('ELF requires newer OpenSSL symbols: update the explicit package runtime floor')
dynamic = subprocess.check_output(['readelf', '-d', str(binary)], text=True)
needed = set(re.findall(r'\(NEEDED\).*\[([^]]+)\]', dynamic))
if not {'libssl.so.3', 'libcrypto.so.3'} <= needed or any(not re.fullmatch(r'[A-Za-z0-9+._-]+', library) for library in needed):
    raise SystemExit('ELF requires unexpected OpenSSL SONAMEs or non-system library paths')
strings = subprocess.check_output(['strings', '-a', str(binary)])
for forbidden in (b'/home/', b'/root/', b'/tmp/', b'/build/', b'target-cpu=native'):
    if forbidden in strings:
        raise SystemExit(f'Builder-local binary path/flag: {forbidden.decode()}')
for expected_path in (b'/usr/share/e-imzo-manager/resources.gresource', b'/usr/share/locale'):
    if expected_path not in strings:
        raise SystemExit(f'Missing system resource path: {expected_path.decode()}')
print(f'Validated {package.name}: x86_64, GLIBC >= {minimum}, system resources and full notices')
PY
