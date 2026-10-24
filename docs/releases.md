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

1. Update the version in `pyproject.toml` and `omarchy-plugin/manifest.json`, refresh `uv.lock` if
   needed, and add release notes under that version in `CHANGELOG.md`.
2. Update user docs and `docs/ipc.md` for changed behavior. Run the contributor checks, including Qt
   tests, and smoke-test an install on an Omarchy session. Keep live test results separate from
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

5. Run `auren update check` from an existing install and verify the tag and commit. Check that a
   clean install using the README's tagged clone works. Confirm later pushes to `main` leave the
   installed release current.

GitHub supplies source archives for the tag. Auren currently installs from a Git checkout with
`install.sh`; there is no separate binary or AUR package. Publishing a release does not change the
repository's visibility.

Never move a published version tag. Fix a bad release with another version. Drafts and prereleases
can be used for review and testing, but the updater deliberately ignores them.
