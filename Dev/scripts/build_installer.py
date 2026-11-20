# @module build_installer.py
# @purpose Reproducible DualSetup8 (Online Setup + Offline Setup + Portable.zip) builder, signer, hardlinker, and verifier.
# @inputs CLI flags (--version, --skip-signing, --test-automation, --skip-wheels, --skip-audit, --output-dir) (@depends-on pkg_stage, pkg_release_bundle)
# @outputs 8-file Releases/v<Version>/ + hardlinked Releases/Latest/ + .7z archives + Archive/*-win-x64.7z (@exports build_installer, main)
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from pkg_release_bundle import (  # noqa: E402, F401
    _compress_folder_to_7z,
    _resolve_seven_zip,
    archive_superseded_release_exes,
    build_portable_zip,
    compile_release_docs,
    compress_release_archives,
    enforce_release_retention,
    ensure_central_releases_junction,
    generate_sha256sums,
    mirror_to_latest_hardlinked,
    record_installer_history,
    sign_installer_executable,
    stamp_installer_manifest,
)
from pkg_stage import (  # noqa: E402, F401
    ARCHIVE_DIR,
    AUDIT_SCRIPT,
    BUILDS_DIR,
    CSPROJ_DIR,
    CSPROJ_PATH,
    DEV_ONLY_SCRIPTS,
    DEV_ROOT,
    FORBIDDEN_EXTENSIONS,
    INSTALLER_MANIFEST_SRC,
    PROJECT_CONTAINER,
    RELEASES_DIR,
    SENSITIVE_PATTERNS,
    audit_staging_directory,
    build_default_schedule_template,
    clean_dir,
    compile_user_guide_markdown,
    create_deterministic_payload_zip,
    derive_nuget_semver,
    derive_numeric_file_version,
    log,
    populate_wheelhouse,
    read_locked_version,
    sanitize_doc_text,
    sha256_file,
    stage_clean_release_bundle,
)


def _publish_single_setup_exe(
    *,
    version: str,
    nuget_ver: str,
    file_ver: str,
    delivery_kind: str,
    manifest_rel_path: str,
    publish_out: Path,
    dest_exe: Path,
    test_automation: bool,
    payload_zip_rel: str = "",
    payload_meta_rel: str = "",
) -> Path:
    public_obj_root = Path(r"C:\Users\Public\GreenroomInstallerBuild")
    clean_dir(publish_out)
    clean_dir(CSPROJ_DIR / "bin")
    clean_dir(CSPROJ_DIR / "obj")
    clean_dir(public_obj_root)

    use_public_obj = False
    try:
        public_obj_root.mkdir(parents=True, exist_ok=True)
        use_public_obj = True
    except OSError:
        use_public_obj = False

    dotnet_cmd = [
        "dotnet",
        "publish",
        str(CSPROJ_PATH),
        "-c",
        "Release",
        "-r",
        "win-x64",
        "-t:Rebuild,Publish",
        "--nologo",
        "-v",
        "quiet",
        f"-p:GreenroomVersion={version}",
        f"-p:GreenroomNuGetVersion={nuget_ver}",
        f"-p:GreenroomFileVersion={file_ver}",
        f"-p:InstallerDeliveryKind={delivery_kind}",
        f"-p:InstallerManifestJson={manifest_rel_path}",
        "-p:PublishSingleFile=true",
        "-p:SelfContained=true",
        "-p:IncludeNativeLibrariesForSelfExtract=true",
        "-p:EnableCompressionInSingleFile=true",
        "-p:DebugType=none",
        "-p:DebugSymbols=false",
        "-o",
        str(publish_out),
    ]
    if test_automation:
        dotnet_cmd.append("-p:GreenroomTestAutomation=true")
    if payload_zip_rel:
        dotnet_cmd.append(f"-p:InstallerPayloadZip={payload_zip_rel}")
    if payload_meta_rel:
        dotnet_cmd.append(f"-p:InstallerPayloadMeta={payload_meta_rel}")
    if use_public_obj:
        dotnet_cmd.append(f"-p:BaseIntermediateOutputPath={public_obj_root}\\obj\\")

    log(f"Compiling self-contained .NET 8 installer ({delivery_kind}, testAutomation={test_automation})...")
    subprocess.run(dotnet_cmd, cwd=str(CSPROJ_DIR), check=True, stdin=subprocess.DEVNULL)

    for stray in publish_out.rglob("*"):
        if stray.is_file() and (stray.suffix.lower() == ".pdb" or stray.name.lower().startswith("foundation.") and stray.suffix.lower() == ".xml"):
            stray.unlink()

    built_exe = publish_out / "Greenroom-Setup.exe"
    if not built_exe.exists():
        raise FileNotFoundError(f"Expected compiled installer not found at {built_exe}")
    dest_exe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built_exe, dest_exe)
    return dest_exe


def _verify_setup_exe(exe_path: Path) -> None:
    proc = subprocess.run(
        [str(exe_path), "--verify"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Installer --verify check failed for '{exe_path.name}': {proc.stdout}\n{proc.stderr}")
    log(f"Verified {exe_path.name} --verify (exit=0).")


def _run_audit_script(release_dir: Path, version: str, stage_dir: Path) -> None:
    log(f"Running 4-pillar DualSetup8 audit_installer.ps1 on {release_dir}...")
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(AUDIT_SCRIPT),
            "-InstallerPath",
            str(release_dir),
            "-RequireDualInstaller",
            "-VerifyChecksums",
            "-ExpectedVersion",
            version,
            "-StagingDir",
            str(stage_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"audit_installer.ps1 exited with code {proc.returncode} on '{release_dir}'")


def build_installer(
    cert_thumbprint: str = "",
    skip_wheels: bool = False,
    run_audit: bool = True,
    version_override: str = "",
    skip_signing: bool = False,
    test_automation: bool = False,
    output_dir: str = "",
) -> Path:
    version = version_override.strip().lstrip("v") if version_override.strip() else read_locked_version()
    nuget_ver = derive_nuget_semver(version)
    file_ver = derive_numeric_file_version(version)

    stage_override = (BUILDS_DIR / f"v{version}-automation") if test_automation else None
    is_release_root = not test_automation and not output_dir.strip()
    if output_dir.strip():
        target_release_dir = Path(output_dir).resolve()
    elif test_automation:
        target_release_dir = DEV_ROOT / ".build_cache" / "test-automation" / f"v{version}"
    else:
        target_release_dir = RELEASES_DIR / f"v{version}"

    online_exe_name = f"Greenroom-Setup-v{version}-win-x64.exe"
    offline_exe_name = f"Greenroom-Offline-Setup-v{version}-win-x64.exe"
    portable_zip_name = f"Greenroom-v{version}-win-x64-Portable.zip"

    log(f"Starting DualSetup8 build for v{version} (NuGet={nuget_ver}, FileVersion={file_ver}, skipSigning={skip_signing}, testAutomation={test_automation})...")
    stage_dir, wheel_count = stage_clean_release_bundle(
        version,
        skip_wheels=skip_wheels,
        include_test_automation=test_automation,
        stage_dir_override=stage_override,
    )

    if is_release_root and target_release_dir.exists():
        archive_superseded_release_exes(target_release_dir)
    clean_dir(target_release_dir)
    target_release_dir.mkdir(parents=True, exist_ok=True)

    compile_release_docs(
        version,
        target_release_dir,
        online_exe_name=online_exe_name,
        offline_exe_name=offline_exe_name,
        portable_zip_name=portable_zip_name,
    )

    portable_zip_path = target_release_dir / portable_zip_name
    portable_size, portable_sha = build_portable_zip(stage_dir, target_release_dir, portable_zip_path)

    payload_work = CSPROJ_DIR / "obj_payload"
    publish_out = CSPROJ_DIR / "publish_out"
    clean_dir(payload_work)
    payload_work.mkdir(parents=True, exist_ok=True)

    try:
        online_manifest = payload_work / "online" / "installer.manifest.json"
        stamp_installer_manifest(online_manifest, version, "onlineSetup", portable_zip_name, portable_size, portable_sha)
        online_exe = _publish_single_setup_exe(
            version=version,
            nuget_ver=nuget_ver,
            file_ver=file_ver,
            delivery_kind="onlineSetup",
            manifest_rel_path="obj_payload/online/installer.manifest.json",
            publish_out=publish_out,
            dest_exe=target_release_dir / online_exe_name,
            test_automation=test_automation,
        )

        sign_infos: dict[str, dict[str, str]] = {}
        if skip_signing:
            sign_infos["online"] = {"status": "Skipped"}
        else:
            sign_infos["online"] = sign_installer_executable(online_exe, thumbprint=cert_thumbprint)
        _verify_setup_exe(online_exe)

        payload_zip = payload_work / "payload.zip"
        payload_meta = payload_work / "payload-meta.json"
        payload_sha = create_deterministic_payload_zip(stage_dir, payload_zip, payload_meta, version, wheel_count)

        offline_manifest = payload_work / "offline" / "installer.manifest.json"
        stamp_installer_manifest(offline_manifest, version, "offlineSetup", portable_zip_name, portable_size, portable_sha)
        offline_exe = _publish_single_setup_exe(
            version=version,
            nuget_ver=nuget_ver,
            file_ver=file_ver,
            delivery_kind="offlineSetup",
            manifest_rel_path="obj_payload/offline/installer.manifest.json",
            publish_out=publish_out,
            dest_exe=target_release_dir / offline_exe_name,
            test_automation=test_automation,
            payload_zip_rel="obj_payload/payload.zip",
            payload_meta_rel="obj_payload/payload-meta.json",
        )

        if skip_signing:
            sign_infos["offline"] = {"status": "Skipped"}
        else:
            sign_infos["offline"] = sign_installer_executable(offline_exe, thumbprint=cert_thumbprint)
        _verify_setup_exe(offline_exe)
    finally:
        clean_dir(payload_work)
        clean_dir(publish_out)
        clean_dir(CSPROJ_DIR / "bin")
        clean_dir(CSPROJ_DIR / "obj")
        clean_dir(Path(r"C:\Users\Public\GreenroomInstallerBuild"))

    generate_sha256sums(target_release_dir)

    if is_release_root:
        latest_dir = RELEASES_DIR / "Latest"
        mirror_to_latest_hardlinked(target_release_dir, latest_dir)
        compress_release_archives(RELEASES_DIR, target_release_dir, latest_dir, version)
        record_installer_history(version, online_exe, offline_exe, portable_zip_path, payload_sha, sign_infos)
        ensure_central_releases_junction(RELEASES_DIR)
        enforce_release_retention(RELEASES_DIR, version, collapse_current_suffix=False)

        if run_audit and not skip_signing and AUDIT_SCRIPT.exists():
            _run_audit_script(target_release_dir, version, stage_dir)
            _run_audit_script(latest_dir, version, stage_dir)
        elif skip_signing:
            log("Skipping Authenticode audit_installer.ps1 because --skip-signing is active.")

        enforce_release_retention(RELEASES_DIR, version, collapse_current_suffix=True)

    log(f"SUCCESS: DualSetup8 release bundle ready at {target_release_dir}")
    return online_exe


def _normalize_cli_argv(raw_argv: list[str]) -> list[str]:
    aliases = {
        "-skipsigning": "--skip-signing",
        "-testautomation": "--test-automation",
        "-skipwheels": "--skip-wheels",
        "-skipaudit": "--skip-audit",
        "-version": "--version",
        "-outputdir": "--output-dir",
    }
    return [aliases.get(arg.lower(), arg) for arg in raw_argv]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build, archive, sign, and audit the Greenroom DualSetup8 release bundle.")
    parser.add_argument("--version", default="", help="Optional version override (e.g. 0.3.0)")
    parser.add_argument("--cert-thumbprint", default=os.environ.get("GREENROOM_SIGN_THUMBPRINT", ""), help="Optional code-signing cert thumbprint")
    parser.add_argument("--skip-wheels", action="store_true", help="Skip downloading/bundling offline wheelhouse")
    parser.add_argument("--skip-audit", action="store_true", help="Skip final audit_installer.ps1 invocation")
    parser.add_argument("--skip-signing", action="store_true", help="Build unsigned installers (for CI SignPath submission)")
    parser.add_argument("--test-automation", action="store_true", help="Build Stage 2 Test Automation installers (-p:GreenroomTestAutomation=true)")
    parser.add_argument("--output-dir", default="", help="Optional output folder override for DualSetup8 bundle")
    parser.add_argument("--no-pause", "--unattended", action="store_true", help="Run unattended")
    parser.add_argument("--pause", "--interactive", action="store_true", help="Compatibility flag for .bat wrapper")
    args = parser.parse_args(_normalize_cli_argv(sys.argv[1:] if argv is None else argv))

    build_installer(
        cert_thumbprint=args.cert_thumbprint,
        skip_wheels=args.skip_wheels,
        run_audit=not args.skip_audit,
        version_override=args.version,
        skip_signing=args.skip_signing,
        test_automation=args.test_automation,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
