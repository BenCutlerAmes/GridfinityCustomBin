# Contributing to Tooltracer

Thanks for helping! This is a small project with one maintainer, so a little structure keeps things moving.

## Before you start

- **Bugs and ideas:** open an [issue](../../issues/new/choose). For a bad trace, attach the photo (crop out anything
  private) and say which reference (paper / marker sheet) and detection (Classic / AI) you used.
- **Larger changes:** please discuss in an issue first, so you don't spend time on something that won't be merged.
- **Licence:** the project is under the [PolyForm Noncommercial 1.0.0](LICENSE) licence. By submitting a
  contribution you agree it is licensed the same way and that you have the right to submit it.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Git.

```bash
git clone https://github.com/<your-username>/GridfinityCustomBins.git   # your fork
cd GridfinityCustomBins
uv sync --extra ai --group dev
uv run tooltracer
```

See the [developer guide](README.md#for-developers) for how the code fits together.

## Making a change

1. **Fork** the repository and create a branch from `main`: `git switch -c fix/short-description`.
2. **Keep it focused.** One change per pull request; avoid unrelated reformatting.
3. **Add tests** in `tests/` for behaviour you add or fix (geometry, library, API, tracing).
4. **Run the tests:**
   ```bash
   uv run --group dev pytest
   ```
5. **Try it in the app** if you touched the UI (`uv run tooltracer`); UI code is plain JS in `tooltracer/static/`
   with no build step.
6. **Commit** with a clear message that says what and why.
7. **Open a pull request** against `main` and fill in what changed and how you tested it.

## Pull request checks and review

- The **Build check** runs automatically (tests, then builds and tests the app on Windows, macOS and Linux). Your
  first pull request from a fork needs the maintainer to approve the run. A pull request can't be merged until the
  check passes.
- Every pull request needs the maintainer's review ([CODEOWNERS](.github/CODEOWNERS)). Expect questions or
  requested changes; push more commits to the same branch and the check re-runs.
- Only the maintainer merges to `main` and publishes releases. You never need to touch versions:
  `tooltracer/_version.py` is stamped by the release workflow.

## Style

- Python 3.12, type hints where they help, short docstrings or comments for *why*, not *what*.
- Match the surrounding code; keep dependencies minimal (anything new must also work in the packaged builds).
- Keep everything local: no code that uploads user photos or data anywhere.

## Reporting security issues

Please don't open a public issue. Use GitHub's **Report a vulnerability** option under the repository's
*Security* tab.
