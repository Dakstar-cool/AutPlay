import os
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_ROOT = REPOSITORY_ROOT / "deploy" / "installer"


def bash_executable() -> str:
    bash = shutil.which("bash")
    if sys.platform == "win32":
        bash = str(Path(os.environ["PROGRAMFILES"]) / "Git" / "bin" / "bash.exe")
        assert Path(bash).is_file(), "Git for Windows Bash is required by the release gate"
    assert bash is not None
    return bash


def powershell_executable() -> str:
    executable = "powershell.exe" if sys.platform == "win32" else "pwsh"
    resolved = shutil.which(executable)
    assert resolved is not None, f"{executable} is required by the release gate"
    return resolved


def bash_path(path: Path) -> str:
    if sys.platform != "win32":
        return str(path)
    result = subprocess.run(
        [bash_executable(), "-lc", 'cygpath -u "$1"', "autplay-path", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_installer_scripts_have_valid_shell_syntax(tmp_path: Path) -> None:
    bash = bash_executable()
    for script in ("install-server.sh", "server-control.sh"):
        staged = tmp_path / script
        shutil.copyfile(INSTALLER_ROOT / script, staged)
        subprocess.run(
            [bash, "-n", script],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )


def test_linux_installer_rejects_broad_and_bundle_state_directories(tmp_path: Path) -> None:
    script = tmp_path / "install-server.sh"
    shutil.copyfile(INSTALLER_ROOT / script.name, script)
    broad = tmp_path.parent / "shared-state"
    broad.mkdir(mode=0o755)
    broad.chmod(0o755)
    for unsafe in (tmp_path, broad):
        result = subprocess.run(
            [
                bash_executable(),
                script.name,
                "--bind-host",
                "192.168.1.2",
                "--state-dir",
                bash_path(unsafe),
                "--no-start",
            ],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "STATE_" in result.stderr
        assert not (unsafe / "secrets").exists()


def test_windows_installer_rejects_shared_state_directories() -> None:
    if sys.platform != "win32":
        return
    for unsafe in (Path(os.environ["PUBLIC"]), Path(os.environ["LOCALAPPDATA"]) / "Temp"):
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER_ROOT / "install-server.ps1"),
                "-BindHost",
                "192.168.1.2",
                "-StateDirectory",
                str(unsafe),
                "-NoStart",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "STATE_PATH_UNSAFE" in result.stderr


def test_installer_scripts_have_valid_powershell_syntax() -> None:
    for script in ("install-server.ps1", "server-control.ps1"):
        relative = f"deploy/installer/{script}"
        parser_command = (
            f"$errors=$null; $path=(Resolve-Path '{relative}'); "
            "[void][Management.Automation.Language.Parser]::ParseFile("
            "$path,[ref]$null,[ref]$errors); "
            "if($errors){$errors | Out-String | Write-Error; exit 1}"
        )
        result = subprocess.run(
            [
                powershell_executable(),
                "-NoProfile",
                "-Command",
                parser_command,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_installer_is_private_lan_only_and_preserves_state_on_stop() -> None:
    powershell = (INSTALLER_ROOT / "install-server.ps1").read_text(encoding="utf-8")
    shell = (INSTALLER_ROOT / "install-server.sh").read_text(encoding="utf-8")
    control_powershell = (INSTALLER_ROOT / "server-control.ps1").read_text(encoding="utf-8")
    control_shell = (INSTALLER_ROOT / "server-control.sh").read_text(encoding="utf-8")

    assert "RFC1918" in powershell
    assert "never use 0.0.0.0" in powershell
    assert "RFC1918" in shell
    assert "0.0.0.0" in shell
    assert "down --remove-orphans" in control_powershell
    assert "down --remove-orphans" in control_shell
    assert "down --volumes" not in control_powershell
    assert "down --volumes" not in control_shell
    assert "2.24.4" in powershell
    assert "2.24.4" in shell
    assert "fingerprint" in control_powershell
    assert "fingerprint" in control_shell
    assert "STATE_VERSION_MISMATCH" in powershell
    assert "STATE_VERSION_MISMATCH" in shell
    assert "STATE_PATH_UNSAFE" in powershell
    assert "STATE_PATH_UNSAFE" in shell
    assert "Set-PrivateDirectoryAcl" in powershell
    assert "Test-PrivateDirectoryAcl" in powershell
    assert "AUTPLAY_SERVER_STATE_V1" in powershell
    assert "STATE_DIRECTORY_NOT_PRIVATE" in shell


def test_release_overlay_is_last_in_installer_compose_order() -> None:
    for script_name in (
        "install-server.ps1",
        "server-control.ps1",
        "install-server.sh",
        "server-control.sh",
    ):
        script = (INSTALLER_ROOT / script_name).read_text(encoding="utf-8")
        assert script.index("compose.admin-local.yaml") < script.index("compose.release.yaml")
        if script_name.startswith("server-control"):
            assert "AUTPLAY_RELEASE_TAG" in script
            assert "AUTPLAY_SOURCE_COMMIT" in script
            assert "STATE_VERSION_MISMATCH" in script


def test_release_overlay_covers_every_admin_local_server_process() -> None:
    overlay = (REPOSITORY_ROOT / "deploy" / "compose" / "compose.release.yaml").read_text(
        encoding="utf-8"
    )
    for service in ("migrate", "api", "worker-cpu", "stream", "mobile-api", "admin-init"):
        assert f"  {service}:" in overlay
    assert overlay.count('    image: "${AUTPLAY_SERVER_IMAGE:') == 6


def test_release_packager_emits_both_apks_and_the_server_installer() -> None:
    packager = (REPOSITORY_ROOT / "scripts" / "package-release.ps1").read_text(encoding="utf-8")
    build = (REPOSITORY_ROOT / "apps" / "android" / "build.gradle.kts").read_text(encoding="utf-8")

    assert 'create("trustedLan")' in build
    assert 'applicationIdSuffix = ".lan"' in build
    assert (
        REPOSITORY_ROOT
        / "apps"
        / "android"
        / "src"
        / "trustedLan"
        / "res"
        / "values-ru"
        / "strings.xml"
    ).read_text(encoding="utf-8").count("AutPlay LAN") == 1
    assert "assembleTrustedLan" in packager
    assert "trusted-lan.apk" in packager
    assert "--out $trustedLanAsset" in packager
    assert "Trusted-LAN APK development signing failed" in packager
    assert "server-installer-manifest.json" in packager
    assert "installer.zip" in packager
    assert "compose.public-edge.yaml" in packager
    assert "Caddyfile.public-edge" in packager
    assert "RELEASE_NOTES_$releaseVersion.md" in packager
    assert "version_code = $androidVersionCode" in packager
    assert "BuildConfig.VERSION_NAME" in (
        REPOSITORY_ROOT
        / "apps"
        / "android"
        / "src"
        / "main"
        / "kotlin"
        / "app"
        / "autplay"
        / "MainActivity.kt"
    ).read_text(encoding="utf-8")
