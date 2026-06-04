# Branch protection rules (documented in code)

These are the GitHub branch-protection settings for `main`. Documented
here so the rules are reviewable in the repo, not buried in the GitHub UI.

## `main` branch

| Setting | Value |
|---------|-------|
| Require a pull request before merging | **Yes** |
| Required approving reviews | **1** |
| Dismiss stale reviews on new commit | **Yes** |
| Require review from Code Owners | **Yes** (enforces `CODEOWNERS`) |
| Require status checks before merging | **Yes** |
| Required status check: `ci / lint` | **Yes** |
| Required status check: `ci / typecheck` | **Yes** |
| Required status check: `ci / test` | **Yes** |
| Require branches to be up to date before merging | **Yes** |
| Require conversation resolution | **Yes** |
| Require signed commits | **No** (revisit when this goes public) |
| Require linear history | **Yes** (squash-merge only) |
| Lock branch | **No** |
| Do not allow bypassing the above settings | **Yes** |

## Why these rules

- `linear history` + `squash-merge only` keeps `main` readable. Each PR
  becomes one commit on `main`.
- `Code Owners review required` matches the responsibility boundaries
  documented in `.github/CODEOWNERS`: ADRs, infra, and secret-handling
  paths get extra eyes by default.
- `CI status required` makes the lint/typecheck/test invariants
  enforceable rather than aspirational.

## Setting them

When this repo goes to GitHub:

```bash
# via the GitHub CLI
gh api -X PUT repos/:owner/:repo/branches/main/protection \
  --input .github/branch_protection.json
```

(`branch_protection.json` to be generated on first push from this doc.)
