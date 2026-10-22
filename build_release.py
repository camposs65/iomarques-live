"""Build the Windows release without opening the app or contacting Supabase."""

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

from installation import ASSET_FILES, PACKAGE_FILES, SOURCE_FILES, build_package, source_version


RELEASE_ASSET = "IoMarquesLive-Windows-x64.zip"
INSTALLER_FILES = (*SOURCE_FILES, "config_publica.json", "Instalar.bat", "instalar.ps1",
                   "instalar_app_windows.py", "requirements.txt", "requirements-build.txt", "README.md")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_release(package, output, version, source=None):
    """Use exact allowlists, even if the source or build folder has private data."""
    package, output = Path(package), Path(output)
    manifest = json.loads((package / "release.json").read_text(encoding="utf-8"))
    expected_files = set(PACKAGE_FILES) - {"release.json"}
    if (manifest.get("schema") != 1 or manifest.get("version") != version
            or set(manifest.get("files", {})) != expected_files):
        raise RuntimeError("O manifesto não corresponde à versão que será publicada.")
    for name in expected_files:
        path, record = package / name, manifest["files"][name]
        if (path.stat().st_size != record.get("size")
                or sha256_file(path) != record.get("sha256")):
            raise RuntimeError(f"A integridade do arquivo não foi confirmada: {name}.")
    output.mkdir(parents=True, exist_ok=True)
    assets = [output / RELEASE_ASSET]
    with zipfile.ZipFile(assets[0], "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in PACKAGE_FILES:
            archive.write(package / name, name)
    if source is not None:
        source = Path(source)
        assets.append(output / f"IoMarquesLive-v{version}-Instalacao.zip")
        prefix = f"IoMarquesLive-v{version}"
        with zipfile.ZipFile(assets[-1], "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in INSTALLER_FILES:
                archive.write(source / name, f"{prefix}/{name}")
            for name in ASSET_FILES:
                archive.write(source / "assets" / name, f"{prefix}/assets/{name}")
    checksums = output / "SHA256SUMS"
    checksums.write_text("".join(f"{sha256_file(asset)}  {asset.name}\n" for asset in assets),
                         encoding="ascii")
    return [*assets, checksums]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--package", type=Path, help="Use an already verified compiled package.")
    parser.add_argument("--installer-source", action="store_true",
                        help="Also package the allowlisted source installer for the first installation.")
    args = parser.parse_args(argv)
    source = args.source.resolve()
    output = (args.output or source / "dist").resolve()
    version = source_version(source)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="iomarques-release-") as temporary:
        work = Path(temporary)
        package = args.package
        if package is None:
            package = build_package(source, work / "package", work, sys.executable,
                                    output / "release-build.log")
        assets = package_release(package, output, version,
                                 source if args.installer_source else None)
    for asset in assets:
        print(asset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
