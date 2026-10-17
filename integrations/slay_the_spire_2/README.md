# Slay the Spire 2 local mod patch

This directory contains the reviewable patch and build recipe for
[Gennadiyev/STS2MCP](https://github.com/Gennadiyev/STS2MCP), pinned to commit
`55e064850a68f3b4cde7e5fd525bf9b2dec4e885` (upstream mod `0.4.0`, MIT).
The original copyright and license are in `LICENSE.upstream`.
The game itself and its assemblies are not included.

The GameLab bridge requires the `ai-test-v1` marker. An unpatched upstream mod
must not be used for this integration: its observation path opens shops and
treasure chests, which can mutate a run during `doctor` or `observe`.

## Build

Use a .NET 9 SDK and the installed game's assemblies. The helper checks out the
pinned commit into a temporary directory, applies the patch, builds it, and
creates a **new** output directory. It does not install the mod or modify game
files. It only copies the compiled mod, manifest, license and provenance to the
output; game assembly hashes are recorded without distributing those files.

```sh
python3 integrations/slay_the_spire_2/build_mod.py \
  --dotnet /private/tmp/ai-test-dotnet9/dotnet \
  --game-data "$HOME/Library/Application Support/Steam/steamapps/common/Slay the Spire 2/SlayTheSpire2.app/Contents/Resources/data_sts2_macos_arm64" \
  --output /private/tmp/ai-test-sts2-mod-package
```

If the upstream repository is already cloned, add
`--source /private/tmp/ai-test-sts2mcp-20261017` to avoid the clone network request.
Only committed objects at the pinned revision are used; local edits are ignored.

The .NET SDK used for the initial local build was `9.0.319`. It was downloaded
from Microsoft's official `https://dot.net/v1/dotnet-install.sh`, inspected, then
installed with `--version 9.0.319 --architecture arm64 --os osx --no-path
--install-dir /private/tmp/ai-test-dotnet9`. A new machine needs an existing .NET 9
SDK or a separate SDK installation. The build helper never installs SDKs.

The initial compile against the installed macOS ARM64 game `v0.107.1` passed
with zero warnings and errors. This compile does not prove that the mod loads or
that gameplay works. Runtime verification is recorded separately in the main
integration guide.

## Installation and removal

With the game closed, copy the output's `STS2_MCP.dll` and `STS2_MCP.json` to:

```text
<Steam game folder>/SlayTheSpire2.app/Contents/MacOS/mods/
```

The files sit directly in `mods/`. Do not overwrite an existing installation
without keeping its exact files. The manifest is the upstream
`mod_manifest.json` renamed to match the `STS2_MCP` mod ID. The game prompts for
mod consent on its next launch. The mod's listener binds to localhost and
127.0.0.1, default port 15526. The upstream mod may create `STS2_MCP.conf` beside
its DLL to store that port. To remove this integration, close the game and
remove only the installed STS2_MCP files; do not remove the entire mods folder.

## Patch contract

- `GET /` adds `gamelab_patch: "ai-test-v1"` and `mod_version: "0.4.0"`.
- `GET /api/v1/singleplayer?format=json` returns `gamelab_revision`: the SHA256
  of canonical JSON for the raw state before adding the token. Object keys are
  sorted; array order is preserved. Identical state has an identical token.
- Reading state no longer opens normal/fake merchants or treasure chests.
  Closed UIs report `can_open` and `manual_open_required`. Opening them remains
  a manual action in this first integration.
- `POST /api/v1/singleplayer` accepts the existing action fields plus optional
  `expected_revision` and integer `deadline_unix_ms`. Legacy callers without a
  revision are not protected; GameLab always supplies the observed revision.
- One main-thread callback builds the state, compares the token, checks the
  deadline, then dispatches the action synchronously. Mismatch returns
  `status: "error", stale: true`. The server caps queue lifetime at five seconds;
  an expired callback returns `status: "error", expired: true` without dispatch.
  A client may set a shorter deadline. The deadline is checked again after the
  state read so expensive observation cannot consume the action's validity.
- `status: "ok"` acknowledges dispatch only. Game actions can resolve later.
  The Python bridge must re-observe and confirm a changed state before claiming
  an applied action, and must not retry uncertain actions automatically.
- The patch does not lock the whole game while an async action resolves. Human
  input or another client can still act after dispatch; use one controller for
  evaluation. The state token does not make asynchronous game resolution atomic.
- The existing main-thread drain is corrected to check its ten-item budget
  before dequeueing, preventing an eleventh queued request from being dropped.

The shared multiplayer state builder's shop auto-open was removed as well.
GameLab singleplayer actions are rejected again on the main thread if a
multiplayer run has become active. No multiplayer integration is provided.
