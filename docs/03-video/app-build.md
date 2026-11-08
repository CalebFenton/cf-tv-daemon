# Building and deploying a NUI app for this TV

Updated 2026-10-24. Covers `apps/*.csproj`. The only NUI app today is the cftv
agent (`apps/cftv.cs`), which `tv/cftv.py` builds and deploys by itself
(`docs/03-video/cftv.md`). This file is the manual recipe and the rules
behind it. Rules for drawing on screen are in
`docs/03-video/transparent-overlay.md`.

## 1. Target: .NET Core 3.1

The TV runs .NET Core 3.1.3. `libcoreclr.so` reports `3.1.3`, and
`/usr/share/dotnet/shared/Microsoft.NETCore.App/` holds 2.0.0, 2.1.0, 2.1.1,
2.1.4, 3.0.0 and 3.1.3 (verified 2026-10-24). Target `netcoreapp3.1`, and do
not use .NET 5+ APIs: for example, use
`Process.GetCurrentProcess().Id`, not `Environment.ProcessId`. quiet-tizen's
binaries target net6.0 (per the 2026-10-22 notes; the clone is not in this
repo), so they cannot run here.

## 2. Toolchain

| SDK | state |
|---|---|
| `.dotnet8/` 8.0.426 | builds `netcoreapp3.1`. The only warning is `NETSDK1138` (target is EOL; 2026-10-22) |
| `.dotnet31/` 3.1.426 | unusable on this host (Ubuntu 24.04.5): aborts with "Couldn't find a valid ICU package" (verified 2026-10-24) |

## 3. Build

`tv/cftv.py build()` runs this command:

```sh
PATH=$PWD/.dotnet8:$PATH DOTNET_CLI_HOME=/tmp/dnethome8 DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  dotnet build apps/cftv.csproj -c Release --nologo -o build/cftv \
  -p:TV_FRAMEWORK_DIR=$PWD/build/tv-framework \
  -p:BaseIntermediateOutputPath=$PWD/build/obj/cftv/
```

Output: `build/cftv/Cftv.dll` (19,456 bytes on 2026-10-24) and
`Cftv.runtimeconfig.json`. `tv/cftv.py` rebuilds only when `apps/cftv.cs` or
`apps/cftv.csproj` is newer than the DLL.

Other projects use the same command with their own csproj, their own `-o`,
and their own `BaseIntermediateOutputPath`, as `tv/cftv.py` does. Every
csproj lives in `apps/`, so without that they would all share `apps/obj/`.
Pass `TV_FRAMEWORK_DIR` only when the project references Tizen assemblies.

csproj conventions (`apps/cftv.csproj`):

- `OutputType` `Exe`, `TargetFramework` `netcoreapp3.1`, `UseAppHost`
  `false`, `DebugType` `none`, `GenerateDependencyFile` `false`,
  `AppendTargetFrameworkToOutputPath` `false`.
- `EnableDefaultCompileItems` `false` plus one explicit `<Compile>`. All the
  `.cs` files share `apps/`, and default globbing would compile them together
  (`CS0017`, multiple entry points).
- Tizen references use `<HintPath>$(TV_FRAMEWORK_DIR)/X.dll</HintPath>` and
  `<Private>false</Private>`, so Samsung's assemblies are never copied into the
  output.

## 4. Reference assemblies: `build/tv-framework/`

These are the TV's own assemblies from `/usr/share/dotnet.tizen/framework/`,
used at compile time only. `apps/cftv.csproj` references all six:

| DLL | bytes |
|---|---|
| `Tizen.NUI.dll` | 1,815,040 |
| `Tizen.Applications.Common.dll` | 100,864 |
| `Tizen.Multimedia.MediaPlayer.dll` | 91,136 |
| `Tizen.Multimedia.dll` | 86,528 |
| `Tizen.System.dll` | 23,040 |
| `Tizen.dll` | 13,824 |

All six match the TV's copies by md5 (verified 2026-10-24). How to pull them:
`VENDOR.md`.

## 5. Writing the app

These are API facts from the TV's `Tizen.NUI.dll`:

- **The entry point is yours to write:** `static void Main(string[] args) {
  new App().Run(args); }`. This is the pattern in the shipped `SimpleText.dll`
  IL, and `apps/cftv.cs` uses it.
- **Main window:** use `NUIApplication.GetDefaultWindow()`. `Window.Default`
  does not exist (`CS1061`). For more windows, use `new Window(name, new
  Rectangle(x, y, w, h), true)`. The constructor
  `NUIApplication(string style, WindowMode, Size2D, Position2D)` sets up the
  main window.
- **`OnCreate`** is `protected virtual`.
- **`Window.KeyEvent`** is `EventHandler<Window.KeyEventArgs>`. It does not
  return bool.
- **`System.Text.Json`** is part of netcoreapp3.1 and works; cftv uses it.
- **TTS (`TTSPlayer`, nested enums):** `docs/04-system/control.md` §3.

## 6. How apps run on the TV

- **NUI apps:** the app framework must launch them from an app slot. The
  running cftv agent's `/proc/<pid>/exe` is `/usr/bin/dotnet-hydra-loader`.
  It maps `Tizen.NUI.ni.dll`, `Tizen.Multimedia*.ni.dll` and
  `Tizen.TV.NUI.ni.dll` (verified 2026-10-24).
- **Plain `/usr/bin/dotnet` does not work for them:** `/usr/bin/dotnet App.dll`
  fails with `FileNotFoundException: Tizen.NUI, Version=4.0.0.0`. The shipped
  `SimpleText.dll` fails the same way (for `Tizen.TV.NUI`), because `Tizen.*`
  is resolved by the loader, not by plain dotnet (2026-10-22).
- **Console programs:** those that use no Tizen types, such as
  `CaptureProbe.dll`, run under plain `/usr/bin/dotnet` as root through the
  channel. Their label is `User`, set by dotnet's SMACK64EXEC.
- **Native code** (P/Invoke, shellcode, the ELF loader):
  `docs/01-root/native-code.md`.

## 7. Deploying into the borrowed slot

The slot is `com.samsung.tv.social-chat-app`. Its manifest (verified
2026-10-24) is `ui-application`, `type="dotnet-inhouse"`,
`nodisplay="false"`, `multiple="false"`, `launch_mode="single"`,
`api-version` 5.5. Its entry DLL is
`/opt/usr/apps/com.samsung.tv.social-chat-app/bin/Com.Samsung.TV.SocialChatApp.dll`.

Manual install (this is what `tv/cftv.py ensure()` does):

```sh
A=com.samsung.tv.social-chat-app
D=/opt/usr/apps/$A/bin/Com.Samsung.TV.SocialChatApp.dll
app_launcher -t $A                        # if running; never -k
cp <new>.dll $D && chown 201:0 $D && chmod 755 $D && chsmack -a "User::Pkg::$A::RO" $D
app_launcher -u 5001 -s $A                # start; `app_launcher -r $A` = is it running
```

Get `<new>.dll` onto the TV with `tv/chan.py put` (it lands in the agent
staging directory, `docs/01-root/access-contexts.md` §2), then `cp`.

Rules:

- **Replace the entry DLL in place and leave `app_exec` alone.** Edits to
  `package_app_info.app_exec` are ignored because the launcher resolves through
  `/opt/dbspace/.AppBinaryManager.db`. On 2026-10-22, with `app_exec` pointed
  at a new DLL, the app "launched" but `/proc/<pid>/cmdline` showed the old one.
- **Leave the slot's stock `Com.Samsung.TV.SocialChatApp.runtimeconfig.json`**
  (`netcoreapp2.0`, 2.0.0) as it is. The netcoreapp3.1 agent runs with it.
- **Installed files** (`bin/`, `res/`) are owned by 201:0 (`tizenglobalapp`)
  and labelled `User::Pkg::<app>::RO`. `res/` files are mode 644 (verified
  2026-10-24).
- **Identity:** the app runs as uid 5001 (`owner`), gid 100, with label
  `User::Pkg::<app>` (verified 2026-10-24).
- **Writable files:** the app writes under
  `/opt/usr/home/owner/apps_rw/<app>/`. That directory itself is labelled
  `User::Home`, and its `data/` subdirectory is `User::Pkg::<app>`
  (transmute; verified 2026-10-24). Anything root creates for the app must be
  chowned to 5001 (or made mode 666) and labelled `User::Pkg::<app>`, or the
  app's writes fail silently. On 2026-10-22 a log that root had pre-created
  kept showing the previous run's content, which looked like the app had not
  restarted. `tv/cftv.py` prepares `cftv/` with `chown -R 5001:5001` and
  `chsmack -a`.
- **No arguments:** `app_launcher` does not pass argv. On 2026-10-22 the app
  received a keyboard-map string instead. Configure the app through files;
  cftv uses a command queue.
- **Stopping:** any exit (`Exit()`, `app_launcher -t`) hands focus to Smart
  Hub, and its bar slides up; resume the previously focused app right after
  (`docs/03-video/transparent-overlay.md` §5).
- **Leftovers:** files from the deleted demo apps are still on the TV
  (`res/alpha_test.png`, `res/ball_*.png`, `apps_rw/<app>/probe.log`). cftv
  does not use them.

## 8. Restoring the stock app

The stock DLL is backed up as `/opt/usr/share/tv-backups/hello-02/orig.dll`
(603,648 bytes, md5 `a977e986128daa7351f1775646742c90`, recorded in
`orig.md5`; present on 2026-10-24).

```sh
A=com.samsung.tv.social-chat-app
D=/opt/usr/apps/$A/bin/Com.Samsung.TV.SocialChatApp.dll
app_launcher -t $A
cp /opt/usr/share/tv-backups/hello-02/orig.dll $D && chown 201:0 $D && chmod 755 $D \
  && chsmack -a "User::Pkg::$A::RO" $D
```

The next `bin/cftv` command other than `log` reinstalls the agent, because
the md5 differs.

## 9. Earlier slot (2026-10-22)

The first build ran in `com.samsung.tv.simpletext-tpl`, an ambient template
marked `nodisplay="true"`. The assembly loaded (`HelloScreen.dll` and
`Tizen.NUI.ni.dll` were mapped), but nothing showed on screen. It was
reverted; the backup is in `/opt/usr/share/tv-backups/hello-01/`. A visible app
needs a `nodisplay="false"` slot, which is why social-chat-app is used.

## 10. Why not install our own app

From the 2026-10-20/21 attempts.

- **Package install is closed:** `pkgcmd -i` of an unsigned package fails at
  27% with `Check certificate error [-12]` (`libtpk-installer`
  `StepTpkRecoverSignature`, `author-signature.xml`, RSA-SHA256). Adding a row
  to `.pkgmgr_cert.db` doesn't help. A hand-made app dir is invisible to
  `appmgr_tool` and `pkgcmd -l`.
- **Registry injection gets close but stops short:** rows in
  `.pkgmgr_parser.db` (5 tables) and `.security-manager.db`, plus cloned
  `smackfs/load2` rules, made `app_launcher -l` and `appmgr_tool` resolve the
  app; `pkgmgr-server`/`security-manager` cache their DBs and must be
  restarted. But the target binary was never opened (atime unchanged, no
  `/run/aul/apps/5001/<pid>`) with `dotnet-inhouse`, `webapp` and an
  awk-shebang app. The edits were reverted; they can corrupt the app registry.
- **Untested:** every injected launch ran `app_launcher` as uid 0 ("not
  available for the user 0"), never with `-u 5001`, and
  `.AppBinaryManager.db` (which the launcher actually resolves through, §7)
  was never populated. That may be the missing piece.

Replacing a shipped app's DLL (§7) is the working route.
