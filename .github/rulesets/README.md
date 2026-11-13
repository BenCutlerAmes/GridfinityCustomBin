# Repository protection

GitHub keeps branch and tag protection in the repository *settings*, not in the code, so these files are
ready-to-import copies of what the maintainer applies. They also document the intent.

| Ruleset | What it enforces |
| --- | --- |
| `protect-main.json` | `main` cannot be deleted or force-pushed; changes arrive only through a pull request with a code-owner review ([CODEOWNERS](../CODEOWNERS)), resolved conversations and a passing **Build check** on Windows, macOS and Linux. Only the repository admin (the owner) may bypass: they can push straight to `main` and merge their own pull requests (GitHub never lets you approve your own). |
| `protect-release-tags.json` | `v*` tags can only be created, moved or deleted by the repository admin, so nobody else can trigger or fake a release. |

## Apply them (maintainer, once)

On GitHub: **Settings → Rules → Rulesets → New ruleset → Import a ruleset**, and pick each file.
The Build check names (`build / windows`, `build / macos`, `build / linux`) only become selectable after the
check has run once on a pull request.

## Also set (Settings) — applied

- **General → Pull Requests**: merge commits off (main needs a linear history); squash and rebase on;
  *Automatically delete head branches* on.
- **Actions → General → Fork pull request workflows**: *Require approval for all external contributors*.
- **Actions → General → Workflow permissions**: *Read repository contents*, and Actions may not approve pull requests.
- **Code security**: secret scanning with push protection, Dependabot alerts and security updates, and
  *Private vulnerability reporting* (see [SECURITY.md](../SECURITY.md)).
- **Collaborators**: add nobody with Write access you do not trust; forks and pull requests need none.

## Workflows

- Third-party actions are pinned to exact commits (the version is in a comment). [dependabot.yml](../dependabot.yml)
  opens a weekly pull request to move the pins forward.
- The build job is read-only even when the release workflow calls it; only the publish job can write.

## Releases

[`release.yml`](../workflows/release.yml) refuses to run unless it was started by the repository owner on code
that is already on `main`, and it never overwrites an existing version. Together with the tag ruleset, only the
owner can publish.
