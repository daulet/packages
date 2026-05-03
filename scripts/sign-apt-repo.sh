#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <site-dir>" >&2
  exit 2
fi

site_dir="$1"
release_file="${site_dir}/dists/stable/Release"

if [[ ! -f "${release_file}" ]]; then
  echo "Release file not found: ${release_file}" >&2
  exit 1
fi

if [[ -z "${APT_SIGNING_KEY:-}" ]]; then
  echo "APT_SIGNING_KEY is required. Set it to an ASCII-armored private key or base64-encoded private key." >&2
  exit 1
fi

gpg_home="$(mktemp -d)"
cleanup() {
  rm -rf "${gpg_home}"
}
trap cleanup EXIT

chmod 700 "${gpg_home}"
export GNUPGHOME="${gpg_home}"

key_file="${gpg_home}/private-key.asc"
if [[ "${APT_SIGNING_KEY}" == *"BEGIN PGP PRIVATE KEY BLOCK"* ]]; then
  printf '%s\n' "${APT_SIGNING_KEY}" > "${key_file}"
else
  printf '%s' "${APT_SIGNING_KEY}" | base64 --decode > "${key_file}"
fi

gpg --batch --import "${key_file}"

key_id="${APT_SIGNING_KEY_ID:-}"
if [[ -z "${key_id}" ]]; then
  key_id="$(gpg --batch --list-secret-keys --with-colons | awk -F: '/^sec:/ {print $5; exit}')"
fi
if [[ -z "${key_id}" ]]; then
  echo "Could not infer APT signing key id." >&2
  exit 1
fi

gpg --batch --yes --armor --output "${site_dir}/daulet-archive-keyring.asc" --export "${key_id}"
gpg --batch --yes --output "${site_dir}/daulet-archive-keyring.gpg" --export "${key_id}"
if [[ -n "${APT_SIGNING_KEY_PASSPHRASE:-}" ]]; then
  gpg --batch --yes --pinentry-mode loopback --passphrase "${APT_SIGNING_KEY_PASSPHRASE}" \
    --default-key "${key_id}" --clearsign \
    --output "${site_dir}/dists/stable/InRelease" "${release_file}"
  gpg --batch --yes --pinentry-mode loopback --passphrase "${APT_SIGNING_KEY_PASSPHRASE}" \
    --default-key "${key_id}" --detach-sign --armor \
    --output "${site_dir}/dists/stable/Release.gpg" "${release_file}"
else
  gpg --batch --yes --default-key "${key_id}" --clearsign \
    --output "${site_dir}/dists/stable/InRelease" "${release_file}"
  gpg --batch --yes --default-key "${key_id}" --detach-sign --armor \
    --output "${site_dir}/dists/stable/Release.gpg" "${release_file}"
fi

echo "signed ${release_file}"
