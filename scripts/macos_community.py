"""Self-signed macOS packaging and real Keychain checks on disposable hosted runners.

No private material or account payloads are included in the evidence/artifact directory.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/desktop/build/compose/binaries/main/app/AutPlay.app"
OUT = ROOT / "build/macos-community"
TEMP = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "autplay-community"
STATE = TEMP / "custody.json"
MACHO = {bytes.fromhex(x) for x in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca")}


def run(*args: str | Path, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    if str(args[0]) == "security":
        print("Keychain setup: " + str(args[1]), flush=True)
    try:
        result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(args[0]).name} timed out") from None
    if check and result.returncode:
        # Never echo a command: keychain commands contain ephemeral credentials.
        message = result.stderr[-5000:]
        for value in list(os.environ.values()) + (list(state().values()) if STATE.exists() else []):
            if isinstance(value, str) and len(value) > 10:
                message = message.replace(value, "<redacted>")
        raise RuntimeError(f"{Path(args[0]).name} failed ({result.returncode}): {message}")
    return result


def state() -> dict:
    return json.loads(STATE.read_text())


def pin() -> str:
    value = (ROOT / "config/macos/community-signer.sha256").read_text().strip()
    if not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("Invalid public certificate pin")
    return value


def prepare() -> None:
    if sys.platform != "darwin" or STATE.exists():
        raise RuntimeError("Requires a fresh disposable macOS runner")
    TEMP.mkdir(mode=0o700, parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    cert = ROOT / "config/macos/community-signer.pem"
    run("openssl", "x509", "-in", cert, "-outform", "DER", "-out", TEMP / "signer.der")
    cert_bytes = (TEMP / "signer.der").read_bytes()
    if hashlib.sha256(cert_bytes).hexdigest() != pin():
        raise RuntimeError("Public certificate/pin mismatch")
    p12 = TEMP / "signer.p12"
    p12.write_bytes(base64.b64decode(os.environ["AUTPLAY_COMMUNITY_P12"], validate=True))
    p12.chmod(0o600)
    old_default = run("security", "default-keychain", "-d", "user").stdout.strip().strip('"')
    old_search = [line.strip().strip('"') for line in run("security", "list-keychains", "-d", "user").stdout.splitlines()]
    signing = TEMP / "signing.keychain-db"
    accounts = Path.home() / "Library/Keychains" / ("autplay-ci-" + secrets.token_hex(8) + ".keychain-db")
    owner_keychain = accounts.with_name(accounts.stem + "-owner.keychain-db")
    values = {"signing": str(signing), "accounts": str(accounts), "owner_keychain": str(owner_keychain), "old_default": old_default, "old_search": old_search,
              "password": secrets.token_urlsafe(48), "account_password": secrets.token_urlsafe(48),
              "sha1": hashlib.sha1(cert_bytes).hexdigest().upper()}
    STATE.write_text(json.dumps(values)); STATE.chmod(0o600)
    for path, password in ((signing, values["password"]), (accounts, values["account_password"]), (owner_keychain, values["account_password"])):
        run("security", "create-keychain", "-p", password, path)
        run("security", "set-keychain-settings", "-lut", "7200", path)
        run("security", "unlock-keychain", "-p", password, path)
    run("security", "import", p12, "-k", signing, "-P", os.environ["AUTPLAY_COMMUNITY_P12_PASSWORD"], "-T", "/usr/bin/codesign")
    # This permission belongs ONLY to the CI certificate keychain, never account items.
    run("security", "set-key-partition-list", "-S", "apple-tool:,apple:", "-s", "-k", values["password"], signing)
    run("security", "list-keychains", "-d", "user", "-s", signing, accounts, owner_keychain, *old_search)
    print(run("security", "find-identity", "-p", "codesigning", signing).stdout, flush=True)
    # Fail before the expensive build if this PKCS#12 cannot sign. A pinned designated
    # requirement does not require introducing our root into the recipient's trust store.
    check_binary = TEMP / "signer-check"
    # Do not copy SIP's restricted file flags from the system executable.
    shutil.copyfile("/usr/bin/true", check_binary)
    check_binary.chmod(0o700)
    run("codesign", "--force", "--sign", values["sha1"], "--keychain", signing,
        "--options", "runtime", "--timestamp=none", check_binary)
    run("codesign", "--verify", "--strict", check_binary)
    run("security", "default-keychain", "-d", "user", "-s", accounts)
    p12.unlink()
    print("Community signing identity installed; account test keychain isolated")


def sign(app: Path) -> None:
    values = state()
    entitlements = TEMP / "launcher.plist"
    entitlements.write_bytes(plistlib.dumps({"com.apple.security.cs.allow-jit": True,
                                            "com.apple.security.cs.disable-library-validation": True}))
    identifiers = {"libAutPlaySecureState.dylib": "app.autplay.desktop.secure-state.v1",
                   "libAutPlaySourceAccess.dylib": "app.autplay.desktop.source-access.v1",
                   "AutPlayRemoteMediaHelper": "app.autplay.desktop.remote-media.v1"}
    def seal(path: Path, identifier: str, launcher: bool = False) -> None:
        requirement = f'=designated => identifier "{identifier}" and certificate leaf = H"{values["sha1"]}"'
        args = ["codesign", "--force", "--sign", values["sha1"], "--keychain", values["signing"],
                "--options", "runtime", "--timestamp=none", "--identifier", identifier, "--requirements", requirement]
        if launcher:
            args += ["--entitlements", str(entitlements)]
        run(*args, path)
    for path in sorted(app.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink() or not path.is_file() or path == app / "Contents/MacOS/AutPlay":
            continue
        with path.open("rb") as source:
            magic = source.read(4)
        if magic in MACHO:
            identifier = identifiers.get(path.name, "app.autplay.desktop.native." + hashlib.sha256(str(path.relative_to(app)).encode()).hexdigest()[:24])
            seal(path, identifier)
    runtime = app / "Contents/runtime"
    if (runtime / "Contents/Info.plist").exists():
        seal(runtime, "app.autplay.desktop.runtime")
    seal(app, "app.autplay.desktop", True)
    run("codesign", "--verify", "--deep", "--strict", "--all-architectures", app)


def probe() -> None:
    probe_app = TEMP / "probe/AutPlay.app"
    shutil.copytree(APP, probe_app)
    classes = TEMP / "classes"
    classes.mkdir()
    jdk = Path(os.environ["JAVA_HOME"])
    run(jdk / "bin/javac", "-cp", str(probe_app / "Contents/app/*"), "-d", classes, ROOT / "tests/native/CommunityKeychainProbe.java")
    run(jdk / "bin/jar", "--create", "--file", probe_app / "Contents/app/community-probe.jar", "-C", classes, ".")
    cfg = probe_app / "Contents/app/AutPlay.cfg"
    text = cfg.read_text()
    text, count = re.subn(r"^app.mainclass=.*$", "app.mainclass=app.autplay.desktop.security.CommunityKeychainProbe", text, flags=re.M)
    if count != 1:
        raise RuntimeError("Unexpected launcher configuration")
    text, count = re.subn(r"^app.classpath=", "app.classpath=$APPDIR/community-probe.jar:", text, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError("Missing launcher classpath")
    cfg.write_text(text)
    run("xcrun", "clang++", "-std=c++17", "-fobjc-arc", "-dynamiclib", "-target", "arm64-apple-macosx13.0",
        "-I" + str(jdk / "include"), "-I" + str(jdk / "include/darwin"), "-framework", "Foundation", "-framework", "Security",
        ROOT / "tests/native/CommunityProbeChecks.mm", "-o", probe_app / "Contents/app/resources/native/libCommunityProbeChecks.dylib")
    sign(probe_app)
    executable = probe_app / "Contents/MacOS/AutPlay"
    passed = []
    for phase in ("create", "restart", "lock"):
        if phase == "restart":
            os.environ["JAVA_TOOL_OPTIONS"] = "-javaagent:/autplay-must-not-load.jar"
            os.environ["_JAVA_OPTIONS"] = "-javaagent:/autplay-must-not-load.jar"
        try:
            result = run(executable, phase, timeout=90)
        finally:
            os.environ.pop("JAVA_TOOL_OPTIONS", None)
            os.environ.pop("_JAVA_OPTIONS", None)
        if f"AUTPLAY_COMMUNITY_PROBE_PASSED {phase}" not in result.stdout.splitlines():
            raise RuntimeError("Missing Keychain acceptance marker")
        passed.append(phase)
        print("Keychain acceptance passed: " + phase)
    values = state()
    run("security", "unlock-keychain", "-p", values["account_password"], values["accounts"])
    foreign = TEMP / "foreign-reader"
    run("xcrun", "clang++", "-fobjc-arc", "-framework", "Foundation", "-framework", "Security",
        ROOT / "tests/native/CommunityForeignReader.mm", "-o", foreign)
    run("codesign", "--force", "--sign", "-", "--identifier", "example.foreign.reader", foreign)
    run(foreign, pin(), timeout=30)
    passed.append("foreign-program-keychain-read-denied")
    run("security", "default-keychain", "-d", "user", "-s", values["owner_keychain"])
    for phase in ("owner-create", "owner-restart"):
        result = run(executable, phase, timeout=90)
        if f"AUTPLAY_COMMUNITY_PROBE_PASSED {phase}" not in result.stdout.splitlines():
            raise RuntimeError("Missing production owner acceptance marker")
        passed.append(phase)
    run("security", "default-keychain", "-d", "user", "-s", values["accounts"])
    # A changed sealed resource must be rejected even if the executable still starts.
    record = probe_app / "Contents/app/resources/native/AutPlaySignedIdentity"
    record.write_text(record.read_text() + "tampered\n")
    result = run(executable, "restart", timeout=90, check=False)
    if result.returncode == 0 or "AUTPLAY_COMMUNITY_PROBE_PASSED" in result.stdout:
        raise RuntimeError("Tampered package accepted")
    report = {"status": "passed", "source_commit": os.environ["GITHUB_SHA"], "signer_sha256": pin(),
              "checks": passed + ["device-proof-verification", "private-key-export-rejected", "sealed-resource-tamper-rejected", "java-agent-environment-rejected"],
              "scope": "real file-based Keychain and production JNI; synthetic account data; no production server"}
    (OUT / "keychain-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")


def package() -> None:
    # Replace the jpackage executable. Keeping an account-authorized alternate launcher would
    # let an attacker skip the native seal/environment gate by invoking that executable.
    (APP / "Contents/MacOS/AutPlay").unlink()
    jdk = Path(os.environ["JAVA_HOME"])
    run("xcrun", "clang++", "-std=c++17", "-O2", "-fobjc-arc", "-target", "arm64-apple-macosx13.0",
        '-DAUTPLAY_COMMUNITY_SIGNER_SHA256="' + pin() + '"',
        "-I" + str(jdk / "include"), "-I" + str(jdk / "include/darwin"),
        "-framework", "Foundation", "-framework", "Security",
        ROOT / "apps/desktop/src/native/AutPlayCommunityLauncher.mm", "-o", APP / "Contents/MacOS/AutPlay")
    sign(APP)
    probe()
    # Production package keeps its original launcher and never contains the probe JNI/classes.
    from macos_ci import startup
    if startup(ROOT):
        raise RuntimeError("Signed GUI/bundled JVM startup failed")
    dmg = OUT / "AutPlay-community-arm64.dmg"
    stage = TEMP / "dmg"
    stage.mkdir()
    shutil.copytree(APP, stage / "AutPlay.app")
    (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
    run("hdiutil", "create", "-volname", "AutPlay Community", "-srcfolder", stage, "-format", "UDZO", dmg, timeout=180)
    with dmg.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    (OUT / "SHA256SUMS").write_text(checksum + "  " + dmg.name + "\n")
    shutil.copy2(ROOT / "config/macos/community-signer.pem", OUT)
    shutil.copy2(ROOT / "config/macos/community-signer.sha256", OUT)
    print("Signed package verified: " + checksum)


def cleanup() -> None:
    if not STATE.exists():
        return
    values = state()
    commands = [("security", "default-keychain", "-d", "user", "-s", values["old_default"]),
                ("security", "list-keychains", "-d", "user", "-s", *values["old_search"])]
    commands += [("security", "delete-keychain", values[key]) for key in ("accounts", "signing", "owner_keychain")]
    problems = []
    for command in commands:
        try:
            run(*command, timeout=20)
        except RuntimeError:
            problems.append(command[1])
    shutil.rmtree(TEMP)
    if problems:
        raise RuntimeError("Disposable keychain cleanup failed: " + ", ".join(problems))


if __name__ == "__main__":
    {"prepare": prepare, "package": package, "cleanup": cleanup}[sys.argv[1]]()
