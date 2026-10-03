"""Focused tests for source export and installer release-value validation."""
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'packaging' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_helper('render-installer')
source = load_helper('source-archive')


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            'RELEASE_TAG': 'v1.3.0-arch.1',
            'MANAGER_PACKAGE': 'e-imzo-manager-1.3.0-1-x86_64.pkg.tar.zst',
            'MANAGER_SHA256': 'a' * 64,
        }
        self.template = '\n'.join('@' + name + '@' for name in self.values)

    def test_exact_values_and_repeated_placeholder(self):
        result = renderer.render(self.template + '\n@RELEASE_TAG@', self.values)
        self.assertNotIn('@', result)
        self.assertEqual(result.count('v1.3.0-arch.1'), 2)
        self.assertIn(self.values['MANAGER_SHA256'], result)

    def test_rejects_missing_unknown_and_extra_values(self):
        for template in (self.template.replace('@RELEASE_TAG@', ''), self.template + '@UNKNOWN@'):
            with self.assertRaises(ValueError):
                renderer.render(template, self.values)
        with self.assertRaises(ValueError):
            renderer.render(self.template, dict(self.values, UNKNOWN='anything'))

    def test_rejects_injection_and_moving_releases(self):
        for name, bad in [('RELEASE_TAG', 'latest'), ('RELEASE_TAG', 'v1.3.0-arch.1\nfalse'), ('MANAGER_PACKAGE', '../manager.pkg.tar.zst'), ('MANAGER_SHA256', 'a' * 63), ('MANAGER_SHA256', "'; curl evil")]:
            with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                renderer.render(self.template, dict(self.values, **{name: bad}))

    def test_rejects_tag_package_mismatch(self):
        with self.assertRaises(ValueError):
            renderer.render(self.template, dict(self.values, RELEASE_TAG='v1.3.0-arch.2'))


class SourcePathTests(unittest.TestCase):
    def test_source_and_sdk_notices_allowed(self):
        for name in ('Cargo.lock', 'LICENSE-AGPL', 'vendor/e-imzo/LICENSE-MIT', 'vendor/e-imzo/PATCHES.md', 'tests/tls_server_name.rs', 'packaging/install.sh.in', '.github/workflows/ci.yml'):
            self.assertTrue(source.checked_path(name), name)

    def test_rejects_private_backend_and_generated_payloads(self):
        for name in ('../src/main.rs', '/src/main.rs', 'src/.git/config', 'vendor/backend/service.jar', 'packaging/E-IMZO-v6.4.7.tar.gz', 'packaging/e-imzo-backend.pkg.tar.zst', 'src/.env', 'src/key.p12', 'src/key.pem', 'src/build/file.rs', 'src/__pycache__/test.pyc'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                source.checked_path(name)

    def test_unrelated_root_files_not_exported(self):
        for name in ('personal.txt', '.envrc', 'credentials', 'build.log'):
            self.assertFalse(source.checked_path(name))


class SourceSnapshotTests(unittest.TestCase):
    def fake_git(self, root, *args):
        if args == ('show', '-s', '--format=%ct', 'HEAD'):
            return b'1700000000\n'
        if args == ('status', '--porcelain', '--untracked-files=normal'):
            return b' M README.md\n?? packaging/helper.py\n'
        if args == ('ls-files', '--cached', '--others', '--exclude-standard', '-z'):
            return b'README.md\0packaging/helper.py\0'
        self.fail(f'Unexpected git call: {args}')

    def test_default_rejects_dirty_helpers_before_export(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(source, 'git', self.fake_git):
            destination = pathlib.Path(directory) / 'source.tar.zst'
            with self.assertRaisesRegex(ValueError, 'clean HEAD'):
                source.export(pathlib.Path(directory), destination)
            self.assertFalse(destination.exists())

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is a build prerequisite')
    def test_explicit_working_tree_exports_reviewed_local_source(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(source, 'git', self.fake_git):
            root = pathlib.Path(directory) / 'source'
            (root / 'packaging').mkdir(parents=True)
            (root / 'README.md').write_text('local modified source\n')
            (root / 'packaging/helper.py').write_text('# reviewed untracked helper\n')
            destination = pathlib.Path(directory) / 'source.tar.zst'
            source.export(root, destination, working_tree=True)
            content = subprocess.check_output(['zstd', '-qdc', str(destination)])
            with tarfile.open(fileobj=io.BytesIO(content)) as archive:
                self.assertEqual(set(archive.getnames()), {'e-imzo-manager-1.3.0/README.md', 'e-imzo-manager-1.3.0/packaging/helper.py'})
                self.assertEqual(archive.extractfile('e-imzo-manager-1.3.0/README.md').read(), b'local modified source\n')


class PackageAbiTests(unittest.TestCase):
    def package_callback(self, openssl_floor):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            project = root / 'src/e-imzo-manager-1.3.0'
            sdk = project / 'vendor/e-imzo'
            sdk.mkdir(parents=True)
            for name in ('LICENSE-AGPL', 'LICENSE-CCBY'):
                (project / name).write_text('fixture notice\n')
            for name in ('LICENSE-MIT', 'LICENSE-APACHE', 'PATCHES.md'):
                (sdk / name).write_text('fixture notice\n')
            env = dict(os.environ, MANAGER_SOURCE_ARCHIVE='e-imzo-manager-1.3.0-source.tar.zst', MANAGER_SOURCE_SHA256='0' * 64, srcdir=str(root / 'src'), pkgdir=str(root / 'pkg'), TEST_OPENSSL_FLOOR=openssl_floor)
            script = '''
set -euo pipefail
source "$1"
meson() { mkdir -p "$pkgdir/usr/bin"; printf fixture > "$pkgdir/usr/bin/e-imzo-manager"; }
readelf() { printf 'GLIBC_2.39 OPENSSL_%s\\n' "$TEST_OPENSSL_FLOOR"; }
package
printf '%s\\n' "${depends[@]}"
'''
            return subprocess.run(['bash', '-c', script, 'test-package-abi', str(ROOT / 'packaging/arch/PKGBUILD')], env=env, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which('vercmp'), 'pacman is a build prerequisite')
    def test_glibc_floor_and_gtk_epoch_are_native_dependencies(self):
        result = self.package_callback('3.0.0')
        self.assertEqual(result.returncode, 0, result.stderr)
        deps = set(result.stdout.splitlines())
        self.assertIn('glibc>=2.39', deps)
        self.assertNotIn('glibc', deps)
        self.assertIn('gtk4>=1:4.22', deps)
        self.assertIn('openssl', deps)
        self.assertEqual(subprocess.check_output(['vercmp', '1:4.18.6-1', '1:4.22'], text=True).strip(), '-1')

    @unittest.skipUnless(shutil.which('vercmp'), 'pacman is a build prerequisite')
    def test_newer_openssl_symbols_require_conscious_metadata_update(self):
        result = self.package_callback('3.4.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('update the explicit runtime floor', result.stderr)


if __name__ == '__main__':
    unittest.main()
