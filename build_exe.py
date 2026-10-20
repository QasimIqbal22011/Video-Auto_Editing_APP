"""
Automated Build Script for Video Helper Studio
Compiles the application into a standalone Windows Executable (.exe)
"""
import os
import shutil
import subprocess
import sys

RUST_TOOLCHAIN = "1.97.0"


def _visual_studio_build_env(base_env):
    """Load MSVC's linker and SDK paths when called from ordinary PowerShell."""
    if base_env.get("VCToolsInstallDir"):
        return base_env

    vswhere = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Microsoft Visual Studio", "Installer", "vswhere.exe"
    )
    if not os.path.isfile(vswhere):
        return base_env
    found = subprocess.run(
        [vswhere, "-latest", "-products", "*", "-requires",
         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
        capture_output=True,
        text=True,
        check=False
    )
    install_path = found.stdout.strip()
    if found.returncode != 0 or not install_path:
        return base_env

    dev_cmd = os.path.join(install_path, "Common7", "Tools", "VsDevCmd.bat")
    if not os.path.isfile(dev_cmd):
        return base_env
    command = f'"{dev_cmd}" -arch=x64 -host_arch=x64 >nul && set'
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", command],
        capture_output=True,
        text=True,
        check=False
    )
    if result.returncode != 0:
        result.stdout = ""
    env = dict(base_env)
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            env[key] = value
    if not env.get("VCToolsInstallDir"):
        # Some managed environments install the toolset successfully but
        # VsDevCmd's subprocess quoting is blocked. Populate the equivalent
        # compiler/include/library paths directly for Rust's MSVC target.
        tools_root = os.path.join(install_path, "VC", "Tools", "MSVC")
        versions = sorted(
            (name for name in os.listdir(tools_root) if os.path.isdir(os.path.join(tools_root, name))),
            reverse=True,
        ) if os.path.isdir(tools_root) else []
        if not versions:
            return base_env
        tools_dir = os.path.join(tools_root, versions[0])
        kits_root = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Windows Kits", "10")
        kit_versions = sorted(
            (name for name in os.listdir(os.path.join(kits_root, "Lib")) if os.path.isdir(os.path.join(kits_root, "Lib", name))),
            reverse=True,
        ) if os.path.isdir(os.path.join(kits_root, "Lib")) else []
        kit_version = kit_versions[0] if kit_versions else ""
        kit_bin = os.path.join(kits_root, "bin", kit_version, "x64")
        rust_bin = os.path.join(
            os.path.expanduser("~"), ".rustup", "toolchains",
            f"{RUST_TOOLCHAIN}-x86_64-pc-windows-msvc", "bin"
        )
        paths = [
            rust_bin,
            os.path.join(tools_dir, "bin", "Hostx64", "x64"),
            kit_bin,
            os.path.join(install_path, "Common7", "IDE", "CommonExtensions", "Microsoft", "CMake", "CMake", "bin"),
        ]
        env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
        env["VCToolsInstallDir"] = tools_dir + os.sep
        env["VCINSTALLDIR"] = os.path.join(install_path, "VC") + os.sep
        env["WindowsSdkDir"] = kits_root + os.sep
        if kit_version:
            env["WindowsSDKVersion"] = kit_version + os.sep
            env["UniversalCRTSdkDir"] = kits_root + os.sep
            env["UCRTVersion"] = kit_version + os.sep
        include_dirs = [
            os.path.join(tools_dir, "include"),
            os.path.join(kits_root, "Include", kit_version, "ucrt"),
            os.path.join(kits_root, "Include", kit_version, "shared"),
            os.path.join(kits_root, "Include", kit_version, "um"),
            os.path.join(kits_root, "Include", kit_version, "winrt"),
            os.path.join(kits_root, "Include", kit_version, "cppwinrt"),
        ]
        lib_dirs = [
            os.path.join(tools_dir, "lib", "x64"),
            os.path.join(kits_root, "Lib", kit_version, "ucrt", "x64"),
            os.path.join(kits_root, "Lib", kit_version, "um", "x64"),
        ]
        env["INCLUDE"] = os.pathsep.join(path for path in include_dirs if os.path.isdir(path))
        env["LIB"] = os.pathsep.join(path for path in lib_dirs if os.path.isdir(path))
    return env


def build_rust_renderer(base_dir, test=False):
    manifest = os.path.join(base_dir, "rust_renderer", "Cargo.toml")
    if not os.path.isfile(manifest):
        raise FileNotFoundError(f"Rust renderer manifest not found: {manifest}")

    proto = shutil.which("proto")
    cargo = shutil.which("cargo")
    if not proto:
        proto_candidate = os.path.join(os.path.expanduser("~"), ".proto", "bin", "proto.exe")
        if os.path.isfile(proto_candidate):
            proto = proto_candidate
    if not cargo:
        cargo_candidates = [
            os.path.join(os.path.expanduser("~"), ".rustup", "toolchains",
                         f"{RUST_TOOLCHAIN}-x86_64-pc-windows-msvc", "bin", "cargo.exe"),
            os.path.join(os.path.expanduser("~"), ".cargo", "bin", "cargo.exe"),
        ]
        cargo = next((candidate for candidate in cargo_candidates if os.path.isfile(candidate)), None)
    if cargo:
        command = [cargo, "test" if test else "build"]
        if not test:
            command.append("--release")
        command.extend(["--manifest-path", manifest])
    elif proto:
        command = [proto, "run", "rust", RUST_TOOLCHAIN, "--", "test" if test else "build"]
        if not test:
            command.append("--release")
        command.extend(["--manifest-path", manifest])
    else:
        raise RuntimeError(
            "Building the GPU renderer requires Rust 1.97+ (cargo or proto). "
            "Install Rust and the Visual C++ Build Tools, then rerun this script."
        )

    env = _visual_studio_build_env(os.environ)
    print("Testing the Rust/WGPU frame renderer..." if test else "Building the Rust/WGPU frame renderer...")
    result = subprocess.run(command, cwd=base_dir, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"Rust renderer build failed with exit code {result.returncode}")


def build():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    spec_file = os.path.join(base_dir, "VideoHelper.spec")

    print("=" * 60)
    print("Building Video Helper Studio Standalone Executable...")
    print(f"Working Directory: {base_dir}")
    print(f"Spec File: {spec_file}")
    print("=" * 60)

    try:
        build_rust_renderer(base_dir)
    except Exception as exc:
        print(f"Rust renderer build failed: {exc}")
        return 1

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        spec_file
    ]

    result = subprocess.run(cmd, cwd=base_dir)
    if result.returncode == 0:
        exe_path = os.path.join(base_dir, "dist", "VideoHelper", "VideoHelper.exe")
        print("\n" + "=" * 60)
        print("BUILD SUCCESSFUL!")
        print(f"Standalone executable located at:\n{exe_path}")
        print("=" * 60)
    else:
        print("\nBuild failed with exit code:", result.returncode)
    return result.returncode

if __name__ == '__main__':
    raise SystemExit(build())
