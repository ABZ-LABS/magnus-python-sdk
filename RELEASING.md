# Releasing

Releases are published to PyPI as [`iamagnus`](https://pypi.org/project/iamagnus/)
by the `Release` workflow, through PyPI trusted publishing: no token lives in
this repository.

## One-time setup

1. On PyPI, under *Your account → Publishing*, add a **pending trusted
   publisher**: project `iamagnus`, owner `MeGrimlock`, repository
   `magnus-python-sdk`, workflow `release.yml`, environment `pypi`.
2. On GitHub, under *Settings → Environments*, create an environment named
   `pypi`. Requiring a reviewer there turns every release into a one-click
   approval.

## Each release

1. Set the same version in `pyproject.toml` and in `iamagnus/__init__.py`.
2. Run `magnus-livecheck` against the production API with a test agent. All
   fourteen checks must pass.
3. Commit, then tag and push:

   ```bash
   git tag v0.1.0
   git push origin main v0.1.0
   ```

The workflow refuses a tag that does not match both version strings, runs the
suite, builds, checks the metadata with `twine check --strict` and publishes.

If `CONTRACT.md` changed, it changes identically in the Node and Go SDKs.
