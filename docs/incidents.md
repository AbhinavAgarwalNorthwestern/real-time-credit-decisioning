# Incidents

Operational incidents and unexpected behavior, in chronological order
(oldest at top). Counted in `docs/04_results_and_metrics.md` under
the failure-mode catalog.

Format per incident:

- **Date**
- **Severity** (Low / Medium / High / Critical)
- **Summary** (one sentence)
- **Detection** (how we noticed)
- **Root cause** (what was actually wrong)
- **Resolution** (what we did)
- **Prevention** (what we changed so it doesn't recur)

---

## 2026-06-03 — Hardcoded MinIO credentials in three places

- **Severity**: Low (private repo, not yet pushed)
- **Summary**: The same MinIO access key and secret appeared verbatim
  in `services/telecom/test_mlflow.py`,
  `deployments/dev/kind/manifests/mlflow-final.yaml`, and
  `deployments/dev/kind/manifests/mlflow-minio-secret.yaml`.
- **Detection**: Day 0 infra audit (Session 1 — Action 2).
- **Root cause**: The custom `mlflow-final.yaml` Deployment was patched
  inline during the artifact-proxy debugging (see ADR 005). The
  credentials were copied into the env-var section as a quick fix and
  never refactored.
- **Resolution**:
  1. Generated cryptographically random replacement credentials
  2. Refactored `mlflow-final.yaml` to use `valueFrom: secretKeyRef:`
  3. Updated `mlflow-minio-secret.yaml` in place (now gitignored)
  4. Created `mlflow-minio-secret.yaml.example` as the committed
     placeholder
  5. Created `scripts/create-mlflow-secret.sh` to source the secret
     from `.env.local`
  6. Deleted the `services/telecom/` sandbox entirely (it was an
     abandoned experiment containing the hardcoded creds in Python)
- **Prevention**:
  - `.gitignore` patterns for `deployments/**/mlflow-minio-secret.yaml`
    and `*-secret.yaml` (with re-include for `*-secret.yaml.example`)
  - `detect-secrets` hook added to `.pre-commit-config.yaml` with a
    baseline file (Session 7)
  - Documented in `docs/day0_log.md` Action 2

---

## 2026-06-03 — Nested duplicate git repos with identical history

- **Severity**: Low
- **Summary**: The repo at `C:\Users\abhin\real-time-ml-system-cohort-4\`
  was found to be doubly nested — outer git root contained both its own
  `.git/` and an inner working-tree directory `real-time-ml-system-cohort-4/`
  which itself contained a separate `.git/`. Both clones at the same
  HEAD (`5d09345`); identical histories.
- **Detection**: Day 0 repo-rename work (Session 3).
- **Root cause**: A second clone was placed inside the first clone's
  working tree, probably accidentally. The inner clone was used as the
  active workspace; the outer was an orphan.
- **Resolution**:
  1. Verified both `.git/` repos held identical history
  2. Moved every non-`.git` item from the inner up to the outer
  3. Backed up the orphan inner `.git/` to
     `C:\Users\abhin\orphan_inner_git_backup.zip`
  4. Removed the orphan inner `.git/` and the empty inner dir
  5. Renamed the outer git root to `realtime-credit-decisioning/`
  6. Updated `.devcontainer/devcontainer.json` path
- **Prevention**: future clones go to a deliberate location; the
  layout invariants in `docs/repo_layout.md` codify the single-level
  expectation.

---

## 2026-06-04 — Crypto-domain uncommitted work lost during repo split

- **Severity**: Medium
- **Summary**: When physically removing crypto-domain code from this repo
  per ADR 007, `Remove-Item -Recurse -Force` permanently deleted several
  service directories — `candles`, `technical_indicators`, `predictor`,
  `prediction-api` — whose source had **never been committed to git**.
  The files are gone from disk and are not recoverable from the
  archive clone, which only contains commits up to `5d09345` (the
  cohort session-1 seed).
- **Detection**: Discovered immediately after the archive clone — the
  archive's `services/` directory contained only `trades` and the
  session-1 seed material, not the completed later-session work.
- **Root cause**: Two contributing factors:
  1. Months of course-session work were never `git add`-ed; the
     working tree carried substantial uncommitted state that no one
     had snapshotted
  2. The pre-delete check was a directory listing, not a `git status`
     audit. A `git status` before the destructive PowerShell would
     have flagged every doomed directory as untracked, which should
     have triggered a commit-before-delete pause
- **Resolution**: Continued with the split per ADR 007. The user
  accepted the loss and elected to focus on building the finance
  domain rather than attempt restoration from Windows Volume Shadow
  Copies, cloud sync history, or other backup channels.
- **Prevention**:
  1. **Hard rule going forward**: never run a destructive filesystem
     operation on the working tree without `git status` first. If
     anything shows up as untracked or modified beyond expectation,
     pause and explicitly ask before proceeding.
  2. Added a layout invariant to `docs/repo_layout.md`:
     *"Commit early and often. Uncommitted work has no archive."*
  3. The `.gitignore` was reviewed — the deleted directories were not
     ignored, so the loss was about un-staged rather than ignored
     state. Future risk reduction is the discipline above, not a
     `.gitignore` change.

---

## (Template for future incidents)

```
## YYYY-MM-DD — Brief title

- **Severity**: Low / Medium / High / Critical
- **Summary**: One sentence.
- **Detection**: How we noticed.
- **Root cause**: What was actually wrong.
- **Resolution**: What we did to fix it.
- **Prevention**: What we changed so it doesn't recur.
```

---

## Status

Three incidents during Day 0 and the Day-0-close transition. All
resolved with prevention measures in place. New incidents append below.
