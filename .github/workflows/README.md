# CI/CD setup

Both workflows here are manual-only (`workflow_dispatch`) — trigger them from the
**Actions** tab, they never run on push or PR. Each checks that its required
secrets/variables exist before doing any real work, so a missing secret fails
in seconds instead of after an hour-long ISO build.

## `build-publish-iso.yml`

Builds `iso-builder/LinuxTV.iso` via `sudo ./build-iso.sh` (live-build, ~30-90
min) and, if `publish` is left checked, uploads it to SourceForge over rsync/SSH.
The ISO is also attached to the workflow run as a downloadable artifact either way.

## `build-publish-android.yml`

Builds `linuxtvremote`'s release `.aab` and `.apk` (signed with your release
keystore), attaches both as workflow artifacts, and optionally:
- uploads the `.apk` to SourceForge
- publishes the `.aab` to Google Play via the Play Developer API

The Play Store step only works because this app already has a listing with at
least one manually-uploaded release — Google requires that first upload to go
through the Play Console UI by hand; the API can't create a listing from
scratch. Since a bad `production` push is hard to take back, `play_track`
defaults to `internal` — pick `production` deliberately when you mean it.

## Secrets to add (repo Settings → Secrets and variables → Actions → Secrets)

| Secret | Used by | What it is |
|---|---|---|
| `SOURCEFORGE_SSH_KEY` | both | Private half of an SSH key added to your SourceForge account (Account → SSH Keys). Paste the whole `-----BEGIN ... KEY-----` block. |
| `SOURCEFORGE_USER` | both | Your SourceForge username (not email). |
| `ANDROID_KEYSTORE_BASE64` | android | Your release `keystore.jks`, base64-encoded: `base64 -w0 keystore.jks` (macOS: `base64 -i keystore.jks`). Paste the output. |
| `ANDROID_KEYSTORE_PASSWORD` | android | The keystore's store password. |
| `ANDROID_KEY_ALIAS` | android | The key alias inside the keystore. |
| `ANDROID_KEY_PASSWORD` | android | That key's password (often same as the store password). |
| `PLAY_SERVICE_ACCOUNT_JSON` | android (Play publish only) | The full JSON key content for a Play Console service account with Release Manager access to this app. Paste the raw JSON. |

## Variables to add (same page, **Variables** tab, not Secrets)

| Variable | Used by | What it is |
|---|---|---|
| `SOURCEFORGE_PROJECT` | both | Your SourceForge project slug — the `<slug>` in `sourceforge.net/projects/<slug>`. Not secret, just project-specific, so it's a variable rather than a secret. |

Uploads land in `/home/frs/project/<SOURCEFORGE_PROJECT>/desktop-iso/` and
`/home/frs/project/<SOURCEFORGE_PROJECT>/android-apk/` on SourceForge — create
those two folders once via the SourceForge file manager before the first run
(rsync won't create missing parent directories).

## Notes / things I couldn't verify from here

- I don't have network access in this environment, so I haven't run either
  workflow end to end — double-check the first run of each.
- `r0adkll/upload-google-play@v1` is a long-standing, widely-used action for
  this; worth a quick glance at its repo for any changes before relying on it.
- `build-iso.sh` now passes explicit `--mirror-bootstrap`/`--mirror-chroot`/
  `--mirror-binary` (+ `-security`) flags pointing at `deb.debian.org` /
  `security.debian.org`. Without them, `live-build` fell back to the *build
  host's* default apt mirror to bootstrap Debian trixie — harmless on a
  Debian host, but on the Ubuntu GitHub Actions runner that meant trying to
  fetch trixie from `archive.ubuntu.com`, which doesn't have it
  (`Failed getting release file .../dists/trixie/Release`). This is a real
  fix to the script itself, not a CI-only workaround, so it also makes local
  builds reproducible regardless of what host they're run from.
- `build-iso.sh` also now passes `--architectures amd64` and
  `--linux-packages linux-image` explicitly. Without them, `lb config`
  combined `--linux-flavours amd64` into a package name of `linux-amd64`
  instead of the real Debian package `linux-image-amd64`
  (`E: Unable to locate package linux-amd64`, exit 123 at the
  `lb_chroot_install-packages` stage).
- `build-iso.sh` also now passes `--mode debian` explicitly. Without it,
  `live-build` auto-detects its mode from the *build host's* OS (logged as
  `Updating config tree for a ubuntu/amd64 system`), which on the Ubuntu
  runner pulled in Ubuntu's live-session package `casper` instead of
  Debian's equivalent -- `casper` doesn't exist in Debian's repos at all
  (`E: Unable to locate package casper`, exit 100 at `lb_chroot_live-packages`).
- The Android workflow downloads a fresh Android SDK cmdline-tools build
  itself rather than relying on `android-actions/setup-android@v3` (fails
  trying to touch the removed legacy `tools` package) or whatever SDK
  happens to be preinstalled on the runner image (its presence/path proved
  inconsistent across a few attempts). It then installs `platforms;android-35`,
  `build-tools;35.0.0` and `ndk;27.1.12297006` — the current defaults baked
  into `expo-root-project`'s Gradle plugin (see
  `node_modules/expo-modules-autolinking/android/expo-gradle-plugin/expo-autolinking-plugin/src/main/kotlin/expo/modules/plugin/ExpoRootProjectPlugin.kt`).
  If you bump the `expo` package to a new SDK version, check that file for
  new defaults and update these three strings to match.
