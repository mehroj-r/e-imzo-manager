"""Focused installer tests; only temp directories and command fixtures mutate.

Run: python3 -m unittest discover -s tests -p 'test_installer.py' -v
No network, sudo, pacman transactions or real user/system services are used.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "packaging" / "install.sh.in"
TOOLS = ("bash", "bsdtar", "zstd", "gzip")
METADATA = """pkgname = e-imzo-manager
pkgver = 1.3.0-1
arch = x86_64
depend = gtk4>=1:4.22
depend = glib2>=2.88
depend = libadwaita>=1.9
depend = glibc>=2.43
depend = e-imzo>=6.4.7
"""


def write_tar(path, entries):
    """Entries: (name, bytes) or (name, typeflag, link_target)."""
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            info = tarfile.TarInfo(entry[0])
            info.uid, info.gid = 123, 456
            info.mode = 0o755 if entry[0].endswith("/") else 0o644
            if len(entry) == 3:
                info.type, info.linkname = entry[1:]
                archive.addfile(info)
            elif entry[0].endswith("/"):
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(entry[1])
                archive.addfile(info, io.BytesIO(entry[1]))


def vendor_entries():
    return [
        ("E-IMZO/", b""), ("E-IMZO/lib/", b""),
        ("E-IMZO/E-IMZO.jar", b"vendor jar\x00\xff"),
        ("E-IMZO/E-IMZO.sh", b"#!/bin/bash\nexit 99\n"),
        ("E-IMZO/E-IMZO.pem", b"vendor certificate"),
        ("E-IMZO/README.txt", b"vendor notice"),
        ("E-IMZO/lib/example-1.0.jar", b"dependency jar"),
    ]


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "bash/libarchive/zstd/gzip are required")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e-imzo-installer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.log"
        self.env = os.environ.copy()
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}",
                        FIXTURE_LOG=str(self.log), LC_ALL="C", DISPLAY="", WAYLAND_DISPLAY="")
        self.env.pop("BASH_ENV", None)
        self.fixture("id", 'printf "%s\\n" "${FIXTURE_UID:-1000}"')
        self.fixture("uname", 'printf "%s\\n" "${FIXTURE_ARCH:-x86_64}"')
        self.fixture("pacman-conf", 'printf "%s\\n" "${FIXTURE_SIGLEVEL:-PackageOptional PackageTrustedOnly}"')
        self.fixture("vercmp", r"""python3 - "$1" "$2" <<'PY'
import re,sys
def v(s):
    epoch, s = s.split(':', 1) if ':' in s else ('0', s)
    return int(epoch), tuple(int(x) for x in re.findall(r'\d+', s))
a,b=map(v, sys.argv[1:]); print((a>b)-(a<b))
PY""")
        self.fixture("sudo", 'printf "sudo %s\\n" "$*" >> "$FIXTURE_LOG"; exec "$@"')
        self.fixture("pacman", """printf 'pacman %s\n' "$*" >> "$FIXTURE_LOG"
case "$1" in
    -T)
        dep=${@: -1}; name=${dep%%[<>=]*}
        if [[ $name == jre17-openjdk && ${FIXTURE_JDK17_PROVIDER:-0} == 1 ]]; then exit 0; fi
        [[ $name != "${FIXTURE_MISSING:-not-a-package}" ]] || exit 127
        case "$dep" in
            *'>='*) required=${dep#*>=}; op='>=' ;;
            *'<='*) required=${dep#*<=}; op='<=' ;;
            *'>'*) required=${dep#*>}; op='>' ;;
            *'<'*) required=${dep#*<}; op='<' ;;
            *'='*) required=${dep#*=}; op='=' ;;
            *) exit 0 ;;
        esac
        comparison=$(vercmp "${FIXTURE_VERSION:-99:99.0}" "$required")
        case "$op" in
            '>=') [[ $comparison -ge 0 ]] || exit 127 ;;
            '<=') [[ $comparison -le 0 ]] || exit 127 ;;
            '>') [[ $comparison -gt 0 ]] || exit 127 ;;
            '<') [[ $comparison -lt 0 ]] || exit 127 ;;
            '=') [[ $comparison -eq 0 ]] || exit 127 ;;
        esac ;;
    -Q)
        case "$2" in
            jre17-openjdk) [[ ${FIXTURE_JDK17_PROVIDER:-0} == 0 ]] || exit 1 ;;
            e-imzo|e-imzo-bin|e-imzo-backend) [[ ${FIXTURE_EXISTING:-0} == 1 ]] || exit 1 ;;
        esac
        [[ "$2" != "${FIXTURE_MISSING:-not-a-package}" ]] || exit 1
        printf '%s %s\n' "$2" "${FIXTURE_VERSION:-99:99.0}" ;;
    -Si) printf 'Name : %s\nVersion : %s\n' "$2" "${FIXTURE_REPO_VERSION:-99:99.0}" ;;
    -U) [[ ${FIXTURE_FAIL_TRANSACTION:-0} == 0 ]] ;;
    -S) [[ ${FIXTURE_FAIL_DEPENDENCIES:-0} == 0 ]] ;;
    *) exit 99 ;;
esac""")
        self.fixture("systemctl", """printf 'systemctl %s\n' "$*" >> "$FIXTURE_LOG"
case "$*" in
    *show-environment*) exit 0 ;;
    *is-enabled*) printf '%s\n' "${FIXTURE_ENABLED:-disabled}"; [[ ${FIXTURE_ENABLED:-disabled} == enabled ]] ;;
    *LoadState*) if [[ ${FIXTURE_EXISTING:-0} == 1 ]]; then printf 'loaded\n'; else printf 'not-found\n'; fi ;;
    *ActiveState*) printf '%s\n' "${FIXTURE_READINESS_STATE:-active}" ;;
    *is-active*) [[ ${FIXTURE_ACTIVE:-0} == 1 ]] ;;
    *daemon-reload*|*restart*|*enable*) [[ ${FIXTURE_FAIL_SERVICE:-0} == 0 ]] ;;
    *status*) exit 3 ;;
esac""")
        self.vendor = self.root / "vendor.tar.gz"
        write_tar(self.vendor, vendor_entries())
        self.manager = self.root / "e-imzo-manager-1.3.0-1-x86_64.pkg.tar.zst"
        write_tar(self.manager, [(".PKGINFO", METADATA.encode()), ("usr/", b""),
                                ("usr/bin/", b""), ("usr/bin/e-imzo-manager", b"unexecuted fixture")])

    def fixture(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n" + body + "\n")
        path.chmod(0o755)

    def run_bash(self, body, *, stdin="", source=INSTALLER, extra_env=None):
        env = self.env.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(["bash", "-c", 'source "$1"; shift\n' + body, "test", str(source)],
                              input=stdin, text=True, capture_output=True, env=env, cwd=self.root, timeout=20)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def render_fixture(self):
        # Local test-only rendering, never a user-facing override of trust pins.
        source = INSTALLER.read_text().replace("@RELEASE_TAG@", "v1.3.0-arch.1")
        source = source.replace("@MANAGER_PACKAGE@", self.manager.name)
        source = source.replace("@MANAGER_SHA256@", hashlib.sha256(self.manager.read_bytes()).hexdigest())
        source = source.replace("c077152064cd44c8ade4ba877177061a2edfa039a2c0755751cc65e199611a76",
                                hashlib.sha256(self.vendor.read_bytes()).hexdigest())
        rendered = self.root / "rendered.sh"
        rendered.write_text(source)
        return rendered

    def run_main(self, flags="--no-launch", stdin="n\nn\n", extra_env=None):
        self.env.update(FIXTURE_VENDOR=str(self.vendor), FIXTURE_MANAGER=str(self.manager))
        # Replace only platform/desktop boundaries and transport with local
        # functions. All hashes, archive checks, packaging and ordering are real.
        body = """check_platform() { [[ $(id -u) != 0 && $(uname -m) == x86_64 ]]; }
check_user_bus() { systemctl --user show-environment >/dev/null; }
download() {
    printf 'download %s\n' "$1" >> "$FIXTURE_LOG"
    if [[ "$1" == "$BACKEND_URL" ]]; then cp "$FIXTURE_VENDOR" "$2"; else cp "$FIXTURE_MANAGER" "$2"; fi
}
backend_port_ready() { printf 'port-probe 127.0.0.1:64443\\n' >> "$FIXTURE_LOG"; return 0; }
main """ + flags
        return self.run_bash(body, source=self.render_fixture(), stdin=stdin, extra_env=extra_env)

    def test_source_has_no_side_effects(self):
        self.assert_ok(self.run_bash(":"))
        self.assertEqual(self.calls(), "")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_hash_rejection(self):
        result = self.run_bash(f'verify_hash "{self.vendor}" "' + "0" * 64 + '"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertEqual(self.calls(), "")

    def test_hash_success(self):
        digest = hashlib.sha256(self.vendor.read_bytes()).hexdigest()
        self.assert_ok(self.run_bash(f'verify_hash "{self.vendor}" "{digest}"'))

    def test_environment_cannot_override_trusted_backend(self):
        result = self.run_bash('printf "%s\\n%s\\n" "$BACKEND_URL" "$BACKEND_SHA256"',
                               extra_env={"BACKEND_URL": "https://attacker.invalid/a", "BACKEND_SHA256": "0" * 64})
        self.assert_ok(result)
        self.assertIn("https://dls.soliq.uz/v6.4.7/E-IMZO-v6.4.7.tar.gz", result.stdout)
        self.assertIn("c077152064cd44c8ade4ba877177061a2edfa039a2c0755751cc65e199611a76", result.stdout)

    def test_root_refused(self):
        result = self.run_bash("check_platform", extra_env={"FIXTURE_UID": "0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not root", result.stderr)

    def test_architecture_refused(self):
        result = self.run_bash("check_platform", extra_env={"FIXTURE_ARCH": "aarch64"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("x86_64", result.stderr)

    def test_required_local_signatures_refused(self):
        for policy in ("PackageRequired PackageTrustedOnly", "Required TrustedOnly"):
            with self.subTest(policy=policy):
                result = self.run_bash("check_signature_policy", extra_env={"FIXTURE_SIGLEVEL": policy})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("will not weaken", result.stderr)
        self.assertNotIn("sudo", self.calls())

    def test_optional_and_database_required_signature_policy(self):
        self.assert_ok(self.run_bash("check_signature_policy", extra_env={"FIXTURE_SIGLEVEL": "PackageOptional DatabaseRequired PackageTrustedOnly"}))

    def test_missing_sudo_refused(self):
        result = self.run_bash('command() { [[ "$2" != sudo ]] && builtin command "$@"; }; check_tools')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing prerequisite: sudo", result.stderr)

    def test_valid_archives(self):
        for kind, archive in (("backend", self.vendor), ("manager", self.manager)):
            with self.subTest(kind=kind):
                self.assert_ok(self.run_bash(f'validate_archive "{archive}" {kind} "{self.root / "listing"}"'))

    def test_archive_attacks(self):
        attacks = {
            "traversal": ("E-IMZO/lib/../../escape.jar", b"x"),
            "absolute": ("/tmp/escape", b"x"),
            "dot_component": ("E-IMZO/./escape", b"x"),
            "double_slash": ("E-IMZO//escape", b"x"),
            "unexpected": ("E-IMZO/keys/user.key", b"x"),
            "nested_library": ("E-IMZO/lib/nested/x.jar", b"x"),
            "newline": ("E-IMZO/lib/a\nb.jar", b"x"),
            "tab": ("E-IMZO/lib/a\tb.jar", b"x"),
            "escape_character": ("E-IMZO/lib/a\x1bb.jar", b"x"),
            "backslash": ("E-IMZO/lib/a\\b.jar", b"x"),
            "symlink": ("E-IMZO/lib/link.jar", tarfile.SYMTYPE, "/tmp/escape"),
            "hardlink": ("E-IMZO/lib/link.jar", tarfile.LNKTYPE, "E-IMZO/E-IMZO.jar"),
            "fifo": ("E-IMZO/lib/pipe.jar", tarfile.FIFOTYPE, ""),
            "block_device": ("E-IMZO/lib/device.jar", tarfile.BLKTYPE, ""),
            "character_device": ("E-IMZO/lib/device.jar", tarfile.CHRTYPE, ""),
            "duplicate": ("E-IMZO/E-IMZO.jar", b"x"),
        }
        for attack, entry in attacks.items():
            with self.subTest(attack=attack):
                archive = self.root / f"{attack}.tar.gz"
                write_tar(archive, vendor_entries() + [entry])
                result = self.run_bash(f'validate_archive "{archive}" backend "{self.root / "listing"}"')
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "escape").exists())

    def test_manager_install_hook_forbidden(self):
        write_tar(self.manager, [(".PKGINFO", METADATA.encode()), ("usr/bin/e-imzo-manager", b"x"), (".INSTALL", b"exit 99")])
        result = self.run_bash(f'validate_archive "{self.manager}" manager "{self.root / "listing"}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected manager path", result.stderr)

    def test_missing_vendor_payload_refused(self):
        write_tar(self.vendor, vendor_entries()[:-2])
        result = self.run_bash(f'validate_archive "{self.vendor}" backend "{self.root / "listing"}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing vendor payload", result.stderr)

    def metadata_directory(self, text=METADATA):
        directory = self.root / "metadata"
        directory.mkdir(exist_ok=True)
        (directory / ".PKGINFO").write_text(text)
        return directory

    def test_metadata_requires_identity_and_elf_floor(self):
        for altered in (METADATA.replace("glibc>=2.43", "glibc"),
                        METADATA.replace("gtk4>=1:4.22", "gtk4>=1:4.20"),
                        METADATA.replace("gtk4>=1:4.22", "gtk4>=4.22"),
                        METADATA.replace("e-imzo-manager", "other-manager"),
                        METADATA.replace("x86_64", "aarch64")):
            with self.subTest(metadata=altered):
                directory = self.metadata_directory(altered)
                self.assertNotEqual(self.run_bash(f'read_manager_metadata "{directory}"').returncode, 0)

    def test_outdated_runtime_floor_refused_before_sudo(self):
        directory = self.metadata_directory()
        result = self.run_bash(f'read_manager_metadata "{directory}" && check_runtime_requirements',
                               extra_env={"FIXTURE_VERSION": "1.0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Update your Arch system deliberately", result.stderr)
        self.assertNotIn("sudo", self.calls())

    def test_gtk_epoch_does_not_accept_outdated_api(self):
        directory = self.metadata_directory()
        result = self.run_bash(f'read_manager_metadata "{directory}" && check_runtime_requirements',
                               extra_env={"FIXTURE_VERSION": "1:4.18.6-1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gtk4 1:4.18.6-1 does not satisfy gtk4>=1:4.22", result.stderr)
        self.assertNotIn("sudo", self.calls())

    def test_elf_glibc_floor_refused(self):
        directory = self.metadata_directory()
        self.fixture("pacman", """if [[ $1 == -Q ]]; then
if [[ $2 == glibc ]]; then printf 'glibc 2.42\n'; else printf '%s 99:99.0\n' "$2"; fi
else exit 99; fi""")
        result = self.run_bash(f'read_manager_metadata "{directory}" && check_runtime_requirements')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("glibc 2.42 does not satisfy glibc>=2.43", result.stderr)

    def test_missing_dependency_repository_floor_checked(self):
        directory = self.metadata_directory()
        result = self.run_bash(f'read_manager_metadata "{directory}" && check_runtime_requirements',
                               extra_env={"FIXTURE_MISSING": "gtk4", "FIXTURE_REPO_VERSION": "4.20"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Repository gtk4 does not satisfy", result.stderr)
        self.assertNotIn("sudo", self.calls())

    def test_backend_native_package_preserves_bytes_and_encodes_root(self):
        vendor_dir = self.root / "extracted"
        stage = self.root / "stage"
        output = self.root / "backend.pkg.tar.zst"
        result = self.run_bash(f'umask 077; extract_archive "{self.vendor}" "{vendor_dir}" && build_backend_package "{vendor_dir}" "{stage}" "{output}"')
        self.assert_ok(result)
        unpacked = self.root / "unpacked"
        unpacked.mkdir()
        subprocess.run(["bsdtar", "-xf", str(output), "-C", str(unpacked), "--no-same-owner", "--no-same-permissions"], check=True)
        for name, data in vendor_entries():
            if not name.endswith("/"):
                self.assertEqual((unpacked / "usr/share/e-imzo" / name.removeprefix("E-IMZO/")).read_bytes(), data)
        pkginfo = (unpacked / ".PKGINFO").read_text()
        self.assertIn("provides = e-imzo=6.4.7", pkginfo)
        self.assertIn("license = custom:vendor", pkginfo)
        self.assertIn("conflict = e-imzo\n", pkginfo)
        self.assertNotIn("conflict = e-imzo-manager", pkginfo)
        self.assertFalse((unpacked / ".INSTALL").exists())
        self.assertIn("/usr/lib/jvm/java-17-openjdk/bin/java", (unpacked / "usr/bin/e-imzo").read_text())
        self.assertIn("ExecStart=/usr/lib/jvm/java-17-openjdk/bin/java", (unpacked / "usr/lib/systemd/user/e-imzo.service").read_text())
        manifest = subprocess.check_output(["gzip", "-dc", str(unpacked / ".MTREE")], text=True)
        self.assertIn("uid=0 gid=0", manifest)
        self.assertIn("sha256digest=", manifest)
        listing = subprocess.check_output(["bsdtar", "--numeric-owner", "-tvf", str(output)], text=True, env={**self.env, "LC_ALL": "C"})
        for line in listing.splitlines():
            self.assertEqual(line.split()[2:4], ["0", "0"])
        self.assertEqual((stage / "usr/bin/e-imzo").stat().st_mode & 0o777, 0o755)
        for relative in (".PKGINFO", ".MTREE", "usr/lib/systemd/user/e-imzo.service", "usr/share/e-imzo/E-IMZO.jar"):
            self.assertEqual((stage / relative).stat().st_mode & 0o777, 0o644)

    def test_dry_run_no_mutations_or_privilege(self):
        result = self.run_main("--dry-run")
        self.assert_ok(result)
        self.assertIn("Dry-run successful", result.stdout)
        calls = self.calls()
        self.assertNotIn("sudo", calls)
        self.assertNotIn("daemon-reload", calls)
        self.assertNotIn("systemctl --user enable", calls)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_signature_refusal_before_download(self):
        result = self.run_main(extra_env={"FIXTURE_SIGLEVEL": "PackageRequired"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("download", self.calls())
        self.assertNotIn("sudo", self.calls())

    def test_hash_refusal_before_sudo_or_service(self):
        rendered = self.render_fixture()
        with self.manager.open("ab") as file:
            file.write(b"tampered")
        self.env.update(FIXTURE_VENDOR=str(self.vendor), FIXTURE_MANAGER=str(self.manager))
        result = self.run_bash("""check_platform() { return 0; }
check_user_bus() { return 0; }
download() { if [[ $1 == "$BACKEND_URL" ]]; then cp "$FIXTURE_VENDOR" "$2"; else cp "$FIXTURE_MANAGER" "$2"; fi; }
main --no-launch""", source=rendered)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertNotIn("sudo", self.calls())
        self.assertNotIn("systemctl", self.calls())

    def local_binary(self):
        binary = self.home / ".local/bin/e-imzo-manager"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"existing user binary")
        return binary

    def test_transaction_failure_no_user_changes_or_service_actions(self):
        binary = self.local_binary()
        result = self.run_main(stdin="y\ny\n", extra_env={"FIXTURE_FAIL_TRANSACTION": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(binary.read_bytes(), b"existing user binary")
        self.assertNotIn("daemon-reload", self.calls())
        self.assertNotIn("systemctl --user enable", self.calls())
        self.assertFalse((self.home / ".local/state").exists())

    def test_dependency_failure_does_not_attempt_local_transaction(self):
        result = self.run_main(extra_env={"FIXTURE_MISSING": "gtk4", "FIXTURE_FAIL_DEPENDENCIES": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo pacman -S --needed -- gtk4>=1:4.22", self.calls())
        self.assertNotIn("sudo pacman -U", self.calls())
        self.assertNotIn("daemon-reload", self.calls())
        self.assertNotIn(" -Sy", self.calls())

    def test_jdk17_provider_does_not_request_conflicting_jre_package(self):
        result = self.run_main(extra_env={"FIXTURE_JDK17_PROVIDER": "1"})
        self.assert_ok(result)
        calls = self.calls()
        self.assertIn("pacman -T -- jre17-openjdk", calls)
        self.assertNotIn("pacman -Q jre17-openjdk", calls)
        self.assertNotIn("pacman -Si jre17-openjdk", calls)
        self.assertNotIn("sudo pacman -S", calls)
        self.assertNotIn("pacman -R", calls)
        self.assertEqual(calls.count("sudo pacman -U"), 1)

    def test_native_dependency_provider_satisfies_virtual_soname(self):
        self.fixture("pacman", 'printf "pacman %s\\n" "$*" >> "$FIXTURE_LOG"; [[ $1 == -T ]]')
        result = self.run_bash("MANAGER_DEPENDENCIES=('libexample.so=1-64'); check_runtime_requirements; printf 'missing: %s\\n' \"${MISSING_DEPENDENCIES[*]}\"")
        self.assert_ok(result)
        self.assertIn("pacman -T -- libexample.so=1-64", self.calls())
        self.assertNotIn("pacman -Q libexample.so", self.calls())
        self.assertIn("missing: \n", result.stdout)

    def test_one_interactive_native_transaction(self):
        self.assert_ok(self.run_main())
        calls = self.calls()
        self.assertEqual(calls.count("sudo pacman -U"), 1)
        self.assertIn(self.manager.name, calls)
        self.assertIn("e-imzo-backend-6.4.7-1-x86_64.pkg.tar.zst", calls)
        for forbidden in ("--noconfirm", "--overwrite", "--nodeps", "--noscriptlet", "-Sy", "-R"):
            self.assertNotIn(forbidden, calls)

    def test_disabled_service_preserved_on_upgrade(self):
        result = self.run_main(extra_env={"FIXTURE_EXISTING": "1", "FIXTURE_ENABLED": "disabled"})
        self.assert_ok(result)
        self.assertIn("Existing service state preserved", result.stdout)
        for mutation in ("enable --now", " restart ", " start ", "port-probe", "ActiveState"):
            self.assertNotIn(mutation, self.calls())

    def test_running_disabled_service_restarted_without_enable(self):
        self.assert_ok(self.run_main(extra_env={"FIXTURE_EXISTING": "1", "FIXTURE_ENABLED": "disabled", "FIXTURE_ACTIVE": "1"}))
        self.assertIn("systemctl --user restart e-imzo.service", self.calls())
        self.assertNotIn("enable --now", self.calls())

    def test_first_install_autostart_requires_consent(self):
        self.assert_ok(self.run_main(stdin="n\n"))
        self.assertNotIn("enable --now", self.calls())
        self.log.unlink()
        self.assert_ok(self.run_main(stdin="y\n"))
        self.assertIn("systemctl --user enable --now e-imzo.service", self.calls())

    def test_masked_service_preserved(self):
        self.assert_ok(self.run_main(extra_env={"FIXTURE_EXISTING": "1", "FIXTURE_ENABLED": "masked", "FIXTURE_ACTIVE": "1"}))
        self.assertNotIn("restart e-imzo.service", self.calls())
        self.assertNotIn("enable --now", self.calls())

    def test_delayed_backend_socket_readiness(self):
        self.fixture("sleep", 'printf "readiness-pause\\n" >> "$FIXTURE_LOG"')
        result = self.run_bash("""backend_port_ready() {
    printf 'port-probe\\n' >> "$FIXTURE_LOG"
    [[ $(grep -c '^port-probe$' "$FIXTURE_LOG") -ge 3 ]]
}
wait_for_backend 3""")
        self.assert_ok(result)
        self.assertEqual(self.calls().count("port-probe"), 3)
        self.assertEqual(self.calls().count("readiness-pause"), 2)
        self.assertIn("local backend socket is ready", result.stdout)

    def test_backend_terminal_failure_stops_before_probe_or_launch(self):
        result = self.run_bash("""backend_port_ready() { printf 'unexpected-probe\\n' >> "$FIXTURE_LOG"; return 0; }
wait_for_backend 3""", extra_env={"FIXTURE_READINESS_STATE": "failed"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed state", result.stderr)
        self.assertIn("Manager was not launched", result.stderr)
        self.assertNotIn("unexpected-probe", self.calls())

    def test_backend_readiness_timeout_is_bounded_and_diagnostic(self):
        result = self.run_bash("""backend_port_ready() { return 1; }
wait_for_backend 0""")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("within 0s", result.stderr)
        self.assertIn("Manager was not launched", result.stderr)
        self.assertIn("journalctl --user -u e-imzo.service", result.stderr)

    def test_tcp_probe_is_only_bounded_localhost_connect(self):
        self.fixture("timeout", 'printf "timeout %s\\n" "$*" >> "$FIXTURE_LOG"; exit 1')
        result = self.run_bash("backend_port_ready")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timeout --kill-after=1 1 bash -c exec 3<>/dev/tcp/127.0.0.1/64443", self.calls())
        self.assertNotIn("curl", self.calls())

    def test_readiness_failure_in_main_prevents_launch_and_restores_migration(self):
        binary = self.local_binary()
        result = self.run_main(stdin="y\ny\ny\n", extra_env={"FIXTURE_READINESS_STATE": "failed"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Manager was not launched", result.stderr)
        self.assertNotIn("Manager launch requested", result.stdout)
        self.assertNotIn("Launch the system manager now?", result.stderr)
        self.assertEqual(binary.read_bytes(), b"existing user binary")
        self.assertNotIn("port-probe", self.calls())

    def test_local_backup_explicit_consent_and_exact_allowlist(self):
        binary = self.local_binary()
        keys = self.home / ".config/e-imzo/private.key"
        keys.parent.mkdir(parents=True)
        keys.write_bytes(b"never touched")
        cache = self.home / ".local/share/glib-2.0/schemas/gschemas.compiled"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"shared cache")
        self.assert_ok(self.run_main(stdin="n\nn\n"))
        self.assertTrue(binary.exists())
        self.assertFalse((self.home / ".local/state").exists())
        self.log.unlink()
        result = self.run_main(stdin="y\nn\n")
        self.assert_ok(result)
        self.assertFalse(binary.exists())
        manifests = list((self.home / ".local/state/e-imzo-manager").glob("*/manifest.txt"))
        self.assertEqual(len(manifests), 1)
        original, saved = manifests[0].read_text().strip().split("\t")
        self.assertEqual(original, str(binary))
        self.assertEqual(Path(saved).read_bytes(), b"existing user binary")
        self.assertEqual(keys.read_bytes(), b"never touched")
        self.assertEqual(cache.read_bytes(), b"shared cache")
        self.assertLess(self.calls().index("sudo pacman -U"), self.calls().index("daemon-reload"))

    def test_local_backup_restored_on_cache_failure(self):
        binary = self.local_binary()
        applications = self.home / ".local/share/applications"
        applications.mkdir(parents=True)
        desktop = applications / "uz.xinux.EIMZOManager.desktop"
        desktop.write_bytes(b"existing desktop entry")
        self.fixture("update-desktop-database", 'printf "cache-update\\n" >> "$FIXTURE_LOG"; exit 1')
        result = self.run_main(stdin="y\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(binary.read_bytes(), b"existing user binary")
        self.assertIn("Migration rollback attempted", result.stderr)
        self.assertNotIn("daemon-reload", self.calls())

    def test_local_backup_restored_on_service_failure(self):
        binary = self.local_binary()
        result = self.run_main(stdin="y\ny\n", extra_env={"FIXTURE_FAIL_SERVICE": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(binary.read_bytes(), b"existing user binary")
        self.assertIn("Migration rollback attempted", result.stderr)

    def test_symlinked_migration_parent_refused(self):
        external = self.root / "external"
        external.mkdir()
        (external / "e-imzo-manager").write_bytes(b"external file")
        (self.home / ".local").mkdir()
        (self.home / ".local/bin").symlink_to(external, target_is_directory=True)
        result = self.run_main(stdin="y\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinked parent", result.stderr)
        self.assertEqual((external / "e-imzo-manager").read_bytes(), b"external file")
        self.assertNotIn("sudo", self.calls())


if __name__ == "__main__":
    unittest.main()
