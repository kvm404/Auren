# Updates and releases

## Updating an installation

Auren checks [GitHub's latest published release](https://docs.github.com/en/rest/releases/releases#get-the-latest-release). It fetches that release's tag and installs its exact
commit. A push to `main`, a tag without a published release, a draft release, or a prerelease does
not trigger an update. Release tags must be `vMAJOR.MINOR.PATCH` or `MAJOR.MINOR.PATCH`.

```bash
auren update                    # check, show changes, and ask before installation
auren update check              # check only
auren update check --json        # result consumed by the panel
auren update apply              # explicitly install in the background, without another prompt
auren update status             # last check or install result
```

The panel can notify you about new releases and checks every six hours. Until the first release is
published, the checker reports that no published release is available. A private repository also
needs a `gh` login, `GH_TOKEN`, or `GITHUB_TOKEN` with read access. Public checks do not require `gh`.

### The Arch package

The `auren` package is pacman's to update. `auren update check` compares its version with the latest
release; `auren update` (and the panel's Update button, in a terminal it opens) waits until Auren is
idle, stops it, runs `yay -S --needed auren` (or `paru`), which asks for your password, and starts it
again as it was. A changed bar plugin or mascot restarts the Omarchy shell. Updating the whole system
with `yay -Syu` updates Auren too; restart it afterwards (pacman's message says how).

### Homebrew (macOS)

`brew install kvm404/auren/auren` installs Auren's source, and `auren setup` builds the install from it
(install-macos.sh --brew, recorded as mode `brew` in `~/.local/share/auren/install.json`). `auren update
check` compares the installed version with the latest release, like the Arch package, and also offers a
newer version Homebrew has already (after a `brew upgrade` that wasn't followed by `auren setup`).
Updating runs in the background: it waits until Auren is idle, stops its launch agents, runs `brew
update` and `brew upgrade auren`, runs the new version's installer, and starts Auren again as it was. `auren setup` keeps a copy of the source it set up in `~/.cache/auren/brew-rollback/<version>`, outside
the Cellar, and a failed install sets the old version up again from there (the update makes the copy
first if it's missing, and changes nothing if it can't). A version Homebrew has already is set up
without GitHub. A release the tap doesn't have yet changes nothing. A Homebrew install never
uses the git clone below, and a clone's install never runs brew.

### install.sh installs

A normal install updates from its own clone in `~/.cache/auren/source/`. The checkout you originally
installed from is left alone. The updater waits until Auren is idle, stops its services, installs the
release, and restores the prior commit if installation fails. It preserves whether each service was
running and enabled. A changed bar plugin or mascot also restarts the Omarchy shell.

Progress is written to `$XDG_RUNTIME_DIR/auren/update.json`; installation output is in
`~/.local/state/auren/update.log`. If rollback also fails, the status includes the checkout to reinstall
from. A moved release tag is refused: published tags must not be rewritten.

### Development installs

For `./install.sh --dev`, updates still target releases. The updater fast-forwards only a clean `main`
checkout. A branch with edits or a different name is left for you to update manually. If your installed
commit is ahead of the latest release, the updater considers it current and does not downgrade it.
A checkout whose history diverged from the release cannot be fast-forwarded.

To follow unreleased development work, use your usual Git workflow and rerun `./install.sh --dev` as
needed. Do not use the release updater to manage a feature branch.

## Publishing a release

Releases are published after the release-preparation PR is reviewed, merged, and CI passes on the
merged commit. The first release is **v0.1.0**, an early release for Omarchy users and contributors.
It is a regular GitHub release so the release-only updater can discover it.

1. Update the version in `pyproject.toml`, `omarchy-plugin/manifest.json`, `pkgver` in
   `packaging/arch/PKGBUILD` (reset `pkgrel` to 1) and the `url` in `packaging/homebrew/auren.rb`,
   refresh `uv.lock` if needed, and add release notes under that version in `CHANGELOG.md`.
2. Update user docs and `docs/ipc.md` for changed behavior. Run the contributor checks, including Qt
   tests, and smoke-test an install on an Omarchy session: build the package from the release commit
   with `packaging/arch/build-local.sh`, install it, and run `auren setup`. Keep live test results separate from
   automated test results in the PR.
3. Merge the PR and confirm CI passes on the merged commit. From a clean checkout, fetch and check
   out that exact commit. Do not tag an unmerged branch or a working tree with edits.
4. Create and push an annotated version tag, then publish a GitHub release with that tag. For the
   first release, copy the v0.1.0 section of `CHANGELOG.md` into a temporary release-notes file:

   ```bash
   git tag -a v0.1.0 -m "Auren v0.1.0"
   git push origin v0.1.0
   gh release create v0.1.0 --verify-tag --title "Auren v0.1.0" --notes-file /tmp/auren-release-notes.md --latest
   ```

5. The `aur` workflow publishes the release to the AUR: it sets the PKGBUILD's version and the
   checksum of GitHub's source tarball for the tag, writes `.SRCINFO`, and pushes both to
   `aur.archlinux.org/auren.git`. Check that it passed and that `yay -Si auren` shows the new
   version. If it failed, fix the cause and run it again from Actions (`aur`, with the tag).
6. The `brew` workflow publishes the release to the Homebrew tap: it sets the formula's `url` to the
   tag's source tarball and its `sha256` to the tarball's checksum, and pushes it to
   `kvm404/homebrew-auren` as `Formula/auren.rb`. Check that it passed and that `brew info
   kvm404/auren/auren` (after `brew update`) shows the new version. If it failed, fix the cause and run
   it again from Actions (`brew`, with the tag). It refuses to replace a newer version in the tap
   (publishing the same version again is fine); to go back to an older one on purpose, run it with
   `force`.
7. Run `auren update check` from an existing install and verify the tag. Check that a clean
   `yay -S auren` works, and on a Mac `brew install kvm404/auren/auren` then `auren setup`. Confirm later
   pushes to `main` leave the installed release current.

Publishing a release does not change the repository's visibility.

### The AUR, once

The package belongs to the AUR account that first pushed it. To set it up:

1. Make an account on [aur.archlinux.org](https://aur.archlinux.org) and add an SSH public key to it
   (a key made only for this: `ssh-keygen -t ed25519 -f aur -C "auren release"`).
2. Add the private key to the repository's Actions secrets as `AUR_SSH_KEY`.
3. Publish a release (or run the `aur` workflow for an existing tag): the first push creates the
   `auren` package.

An AUR key works for every package of its account, not just `auren`. If the account maintains other
packages, the `AUR_SSH_KEY` secret can push to them too. Moving the package to an account of its own
fixes that: add the new account as a co-maintainer on the package's AUR page (or have the old one
disown it to the new one), put a new key on the new account, replace the secret, and take the old
key off the old account.

### The Homebrew tap, once

`brew install kvm404/auren/auren` reads the formula from the tap repository `kvm404/homebrew-auren`
(Homebrew adds the `homebrew-` itself). To set it up:

1. Create the public repository `kvm404/homebrew-auren` with a README (so it has a `main` branch), for
   example: "Homebrew tap for Auren: `brew install kvm404/auren/auren`, then `auren setup`. The formula
   is published from packaging/homebrew/ in kvm404/Auren on each release: don't edit it here."
2. Make an SSH key for the workflow, add its public half to the tap as a deploy key with write access,
   and its private half to kvm404/Auren's Actions secrets as `HOMEBREW_TAP_KEY`, then delete the key
   files. A deploy key can push to that one repository only, and doesn't expire:

   ```bash
   ssh-keygen -t ed25519 -N "" -C "auren release workflow (homebrew tap)" -f tapkey
   gh repo deploy-key add tapkey.pub --repo kvm404/homebrew-auren --allow-write --title "auren release workflow"
   gh secret set HOMEBREW_TAP_KEY --repo kvm404/Auren < tapkey
   shred -u tapkey && rm tapkey.pub
   ```

3. Run the `brew` workflow from Actions for the latest tag, then check `brew install
   kvm404/auren/auren` on a Mac.

The formula in this repository has a placeholder `sha256` (all zeros); only the tap's copy carries the
checksum, filled in by the workflow from the published tarball.

### The phone app, once

Publishing a release also runs the `android-release` workflow: it builds the phone app with Phonon's
library, signs it, and attaches `auren-phone-X.Y.Z.apk` (and its `.sha256`) to the release. The app has
Auren's version (from `pyproject.toml`), so each release's APK installs over the one before.

Android only installs an update signed with the same key as the app already on the phone, so the key is
made once and kept for good. **Back it up** somewhere outside this machine (a password manager): if it is
lost, no later APK installs over the old one, and every user has to uninstall the app (losing its pairing
and inbox) to get the new one. To set it up:

1. Make the key (keytool comes with any JDK; it asks for a password, use the same one for both):

   ```sh
   keytool -genkeypair -v -keystore auren-release.jks -alias auren -keyalg RSA -keysize 4096 \
     -validity 10000 -dname "CN=Auren"
   ```

2. Add four Actions secrets to the repository: `ANDROID_KEYSTORE` (the file, base64:
   `base64 -w0 auren-release.jks | gh secret set ANDROID_KEYSTORE`), `ANDROID_KEYSTORE_PASSWORD`,
   `ANDROID_KEY_ALIAS` (`auren`) and `ANDROID_KEY_PASSWORD`.
3. Back up `auren-release.jks` and its password, then delete the file here.
4. Publish a release (or run the `android-release` workflow for an existing tag).

The PKGBUILD in this repository has `sha256sums=('SKIP')`; only the copy on the AUR carries the
checksum, filled in by the workflow from the published tarball.

Never move a published version tag. Fix a bad release with another version. Drafts and prereleases
can be used for review and testing, but the updater deliberately ignores them.
