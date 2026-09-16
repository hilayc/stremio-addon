# Releasing

PRs targeting `main` and pushes to `main` run tests and a Docker build without
publishing. To release, push a new semantic-version tag with a lowercase `v`
prefix, for example:

```sh
git tag v1.0.4
git push origin v1.0.4
```

The tag must include the workflow and `scripts/release_version.py`. The workflow
runs tests, publishes `ghcr.io/hilayc/stremio-addon:1.0.4`, and creates the GitHub
Release `v1.0.4` with its image archive. It then updates only the top-level
`version` in `addon/config.yaml` on `main` to `"1.0.4"`. The Docker build version
label also uses `1.0.4`. No `main` or `latest` Docker tag is published.

The automated config commit uses the workflow's `GITHUB_TOKEN` and includes
`[skip ci]`, so it does not trigger CI. Other configuration or code edits still
receive normal CI. The release tag remains on its original commit.

The updater preserves unrelated configuration, retries concurrent edits, and
never downgrades the version advertised by Home Assistant. If the current
version is equal or newer, it does nothing. Re-running the release is safe for
the config update. Repository rules must permit the workflow token to update
`main`; the workflow does not bypass branch protection.

Tags such as `v1.1.0-rc.1` are supported and map to `1.1.0-rc.1`. Build metadata
such as `+build.1` is rejected because `+` is not valid in Docker tags. Tags
without the `v` prefix do not start the release workflow.
