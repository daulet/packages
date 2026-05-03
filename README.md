# Daulet Packages

Static APT repository source for Daulet command line tools.

The repository is generated from `packages.json`, signed in GitHub Actions, and
published with GitHub Pages. The expected public URL is:

```text
https://daulet.github.io/packages
```

## Install On Ubuntu

```sh
sudo install -d -m 0755 /etc/apt/keyrings

curl -fsSL https://daulet.github.io/packages/daulet-archive-keyring.gpg \
  | sudo tee /etc/apt/keyrings/daulet-archive-keyring.gpg >/dev/null

echo "deb [signed-by=/etc/apt/keyrings/daulet-archive-keyring.gpg] https://daulet.github.io/packages stable main" \
  | sudo tee /etc/apt/sources.list.d/daulet.list >/dev/null

sudo apt update
sudo apt install mot
```

APT will select `amd64` or `arm64` from the machine architecture.

Updates use the normal Ubuntu flow:

```sh
sudo apt update
sudo apt upgrade
```

## Published Packages

Currently seeded:

- `codex` `0.124.0` for `amd64` and `arm64`
- `mot` `0.3.2` for `amd64` and `arm64`

The remaining Homebrew-distributed packages should be added after their release
workflows publish `.deb` assets for both architectures.

## Release Operations

1. Update `packages.json` with the new GitHub release tag, asset names, and
   SHA-256 checksums.
2. Open a PR and let `CI` build the unsigned repository.
3. Merge to `main`.
4. `Deploy APT Repository` builds, signs, and publishes the repository to Pages.

Source package release workflows publish public `.deb` assets to release tags in
this repository, then update `packages.json` to point at those assets. That keeps
private source repositories private while making the installable binaries public.

The manifest update step is:

```sh
python3 scripts/update-package.py \
  --name mot \
  --repo daulet/packages \
  --tag mot-v0.3.2 \
  --version 0.3.2 \
  --asset amd64:mot_0.3.2_amd64.deb:SHA256 \
  --asset arm64:mot_0.3.2_arm64.deb:SHA256
```

Required repository setup:

- Create `daulet/packages`.
- Enable GitHub Pages with `GitHub Actions` as the source.
- Add `APT_SIGNING_KEY` as a repository secret.
- Optionally add `APT_SIGNING_KEY_ID` and `APT_SIGNING_KEY_PASSPHRASE`.
- Add a `PACKAGES_REPO_TOKEN` secret to each source package repo that should
  update this repository automatically. The token only needs contents write
  access to `daulet/packages`.

Generate a signing key locally with:

```sh
gpg --batch --quick-generate-key "Daulet Packages <packages@daulet.dev>" ed25519 sign 2y
gpg --armor --export-secret-keys "Daulet Packages <packages@daulet.dev>"
```

Use the exported private key as `APT_SIGNING_KEY`. Rotate by publishing a new
keyring file, updating install docs, and keeping the old key trusted until old
repository metadata has expired from user caches.

## Local Build

```sh
python3 scripts/build-apt-repo.py --output site
```

Signing requires `gpg` and `APT_SIGNING_KEY`:

```sh
APT_SIGNING_KEY="$(gpg --armor --export-secret-keys KEY_ID)" \
  bash scripts/sign-apt-repo.sh site
```
