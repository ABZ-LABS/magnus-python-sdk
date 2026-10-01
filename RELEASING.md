# Releasing

**English** · [Español](RELEASING.es.md)

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

## Installing from GitHub depends on the tag

The README's *Installing without PyPI* section installs from a tag of this
repository, so it works as soon as the repository is public and the tag is
pushed, whether or not PyPI accepted the upload. When the version changes,
update the tag in that section of `README.md` and `README.es.md`, and in the
Magnus dashboard (`sdk_links_section.dart` in the front end).

If `CONTRACT.md` changed, it changes identically in the Node and Go SDKs, and
so does its translation, `CONTRACT.es.md`.
