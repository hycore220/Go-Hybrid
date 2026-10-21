"""Exercise release artifact selection offline, without Go/Rust toolchains."""

import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("sh")
if os.name == "nt":
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    SHELL = str(candidate) if candidate.exists() else None


def shell_path(path):
    path = Path(path).resolve()
    if os.name == "nt":
        return "/" + path.drive[0].lower() + path.as_posix()[2:]
    return str(path)


@unittest.skipUnless(SHELL, "requires a POSIX shell (Git Bash works on Windows)")
class ReleaseTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="package-target-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "tools").mkdir()
        (self.root / "compiler").mkdir()
        (self.root / "runtime/go").mkdir(parents=True)
        (self.root / "runtime/rust/hyrt").mkdir(parents=True)
        (self.root / "runtime/rust/hyrt/lib.rs").write_text("// fixture source\n", encoding="utf-8")
        shutil.copy2(ROOT / "tools/package_release.sh", self.root / "tools/package_release.sh")
        (self.root / "tools/RELEASE-INSTALL.md").write_text("fixture\n", encoding="utf-8")
        self.shims = self.root / "fixture-tools"
        self.shims.mkdir()
        self.write_script("go", "#!/bin/sh\nexit 0\n")
        self.write_script("cargo", r'''#!/bin/sh
set -eu
out="${CARGO_TARGET_DIR:-target}"
triple=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --target-dir) out="$2"; shift;;
        --target) triple="$2"; shift;;
    esac
    shift
done
out="$out${triple:+/$triple}/release"
mkdir -p "$out"
case "$PWD" in
    */hyrt) printf 'fresh library\n' > "$out/libhyrt.a";;
    */compiler) cp "$HYB_TEST_FRESH_CLI" "$out/hyb"; chmod +x "$out/hyb";;
    *) exit 99;;
esac
''')
        self.cli = r'''#!/bin/sh
# fresh CLI
set -eu
[ "$1" = build ] || exit 98
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then out="$2"; break; fi
    shift
done
printf '#!/bin/sh\nprintf "hello, Hybrid!\\n"\n' > "$out"
chmod +x "$out"
'''
        (self.root / "fresh-cli").write_text(self.cli, encoding="utf-8", newline="\n")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("HYB_", "CARGO_"))}
        self.env.update({
            "HYB_PKG_OS": "linux", "HYB_PKG_ARCH": "amd64",
            "HYB_TEST_FRESH_CLI": shell_path(self.root / "fresh-cli"),
            "HYB_TEST_SCRIPT": shell_path(self.root / "tools/package_release.sh"),
            "HYB_TEST_TOOLS": shell_path(self.shims),
        })

    def write_script(self, name, content):
        path = self.shims / name
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def run_package(self, *, redirected, stale=False, cross=False):
        triple = "x86_64-unknown-linux-gnu" if cross else ""
        if redirected:
            self.env["CARGO_TARGET_DIR"] = shell_path(self.root / "shared target")
        if cross:
            self.env.update(HYB_RUST_TARGET=triple, HYB_CC="cc")
        if stale:
            for directory, name, content in (
                ("compiler", "hyb", self.cli.replace("# fresh CLI", "# STALE CLI")),
                ("runtime/rust/hyrt", "libhyrt.a", "STALE library\n"),
            ):
                target = self.root / directory / "target" / triple / "release" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
                target.chmod(0o755)
        result = subprocess.run(
            [SHELL, "-c", 'PATH="$HYB_TEST_TOOLS:$PATH"; export PATH; '
             'exec sh "$HYB_TEST_SCRIPT" v0.0.0-test'],
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archive_path = self.root / "dist/hybrid-v0.0.0-test-linux-amd64.tar.gz"
        with tarfile.open(archive_path, "r:gz") as archive:
            cli = archive.extractfile("hybrid-v0.0.0-test/hyb").read().decode("utf-8")
            lib = archive.extractfile(
                "hybrid-v0.0.0-test/runtime/rust/hyrt/target/release/libhyrt.a"
            ).read().decode("utf-8")
        self.assertEqual(cli, self.cli, "release must contain the CLI built in this invocation")
        self.assertEqual(lib, "fresh library\n", "release must contain the freshly built staticlib")
        self.assertIn("smoke PASS", result.stdout)
        self.assertFalse((self.root / "shared target").exists(), "release builds stay crate-local")

    def test_native_control_without_shared_target(self):
        self.run_package(redirected=False)

    def test_shared_target_does_not_break_a_clean_release(self):
        self.run_package(redirected=True)

    def test_shared_target_cannot_package_stale_local_artifacts(self):
        self.run_package(redirected=True, stale=True)

    def test_cross_target_cannot_package_stale_local_artifacts(self):
        self.run_package(redirected=True, stale=True, cross=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
