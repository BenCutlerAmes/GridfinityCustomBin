# Repository protection

GitHub keeps branch and tag protection in the repository *settings*, not in the code, so these files are
ready-to-import copies of what the maintainer applies. They also document the intent.

| Ruleset | What it enforces |
| --- | --- |
| `protect-main.json` | `main` cannot be deleted or force-pushed; changes arrive only through a pull request with a code-owner review ([CODEOWNERS](../CODEOWNERS)), resolved conversations and a passing **Build check** on Windows, macOS and Linux. Only the repository admin (the owner) may bypass, and only via a pull request. |
| `protect-release-tags.json` | `v*` tags can only be created, moved or deleted by the repository admin, so nobody else can trigger or fake a release. |

## Apply them (maintainer, once)

On GitHub: **Settings → Rules → Rulesets → New ruleset → Import a ruleset**, and pick each file.
The Build check names (`build / windows`, `build / macos`, `build / linux`) only become selectable after the
check has run once on a pull request.

## Also set (Settings)

- **Actions → General → Fork pull request workflows**: *Require approval for all outside collaborators*.
- **Actions → General → Workflow permissions**: *Read repository contents* (workflows ask for more where needed).
- **Collaborators**: add nobody with Write access you do not trust; forks and pull requests need none.
- **Code security**: enable *Private vulnerability reporting* and *Dependabot alerts*.

## Releases

[`release.yml`](../workflows/release.yml) refuses to run unless it was started by the repository owner on code
that is already on `main`, and it never overwrites an existing version. Together with the tag ruleset, only the
owner can publish.
