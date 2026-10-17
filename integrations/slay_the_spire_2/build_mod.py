#!/usr/bin/env python3
"""Build the pinned GameLab patch; never install files into the game directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
UPSTREAM = json.loads((HERE / "upstream.json").read_text())


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*args: str, **kwargs) -> None:
    subprocess.run(args, check=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True,
                        help="Installed folder containing sts2.dll, GodotSharp.dll and 0Harmony.dll")
    parser.add_argument("--dotnet", default="dotnet", help="Path to a .NET 9 SDK executable")
    parser.add_argument("--source", type=Path,
                        help="Optional local upstream Git checkout; committed objects only, edits are ignored")
    parser.add_argument("--output", type=Path, required=True,
                        help="New package directory; an existing path is never overwritten")
    args = parser.parse_args()
    data = args.game_data.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error(f"Output already exists: {output}")
    references = [data / name for name in ("sts2.dll", "GodotSharp.dll", "0Harmony.dll")]
    for path in references:
        if not path.is_file():
            parser.error(f"Game assembly missing: {path}")
    dotnet = shutil.which(args.dotnet)
    if not dotnet:
        parser.error(f".NET SDK executable not found: {args.dotnet}")
    patch = HERE / "ai-test-v1.patch"
    source = str(args.source.expanduser().resolve()) if args.source else UPSTREAM["repository"]

    # Local build/cache state stays in the temporary workspace, not the user's home.
    with tempfile.TemporaryDirectory(prefix="gamelab-sts2-build-") as temporary:
        temp = Path(temporary)
        checkout = temp / "source"
        env = dict(os.environ, DOTNET_CLI_HOME=str(temp / "dotnet-home"),
                   DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1",
                   DOTNET_GENERATE_ASPNET_CERTIFICATE="false",
                   NUGET_PACKAGES=str(temp / "nuget"))
        sdk = subprocess.check_output([dotnet, "--version"], env=env, text=True).strip()
        if not sdk.startswith("9."):
            parser.error(f".NET 9 SDK is required, found {sdk}; use --dotnet to select it")
        run("git", "clone", "--no-checkout", source, str(checkout))
        run("git", "-C", str(checkout), "checkout", "--detach", UPSTREAM["commit"])
        run("git", "-C", str(checkout), "apply", "--check", str(patch))
        run("git", "-C", str(checkout), "apply", str(patch))
        # This project has no NuGet packages. Clear remote feeds so builds need no
        # network after cloning and cannot silently add a package dependency.
        nuget_config = temp / "NuGet.Config"
        nuget_config.write_text('<configuration><packageSources><clear /></packageSources></configuration>\n')
        run(dotnet, "build", str(checkout / "STS2_MCP.csproj"), "--configuration", "Release",
            "--configfile", str(nuget_config), f"-p:STS2GameDataDir={data}",
            "-p:ContinuousIntegrationBuild=true", env=env)
        dll = checkout / "bin" / "Release" / "net9.0" / "STS2_MCP.dll"
        provenance = dict(UPSTREAM, sdk_used=sdk, patch_sha256=sha256(patch),
                          assembly_sha256={p.name: sha256(p) for p in references},
                          mod_dll_sha256=sha256(dll))
        output.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(dll, output / "STS2_MCP.dll")
        shutil.copyfile(checkout / "mod_manifest.json", output / "STS2_MCP.json")
        shutil.copyfile(HERE / "LICENSE.upstream", output / "LICENSE.upstream")
        (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Built patched mod package: {output}")
    print("Install requires STS2_MCP.dll and STS2_MCP.json. Game files were not modified.")


if __name__ == "__main__":
    main()
