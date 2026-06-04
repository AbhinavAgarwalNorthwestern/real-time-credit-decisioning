## What this changes

<!-- 1-2 sentences. What does this PR do, and why? -->

## Type of change

<!-- Check all that apply -->

- [ ] Bug fix
- [ ] New feature / service
- [ ] Refactor (no behavior change)
- [ ] Docs / ADR
- [ ] Infra (Kustomize / Terraform / CI)
- [ ] Performance
- [ ] Test only

## Checklist

- [ ] Linting passes locally (`just lint`)
- [ ] Type-check passes locally (`just typecheck`)
- [ ] Tests pass locally (`just test`)
- [ ] New code has tests (or there's a justified exception in the PR description)
- [ ] If this introduces or changes an architectural pattern, there is an ADR
      in `docs/decisions/` (or a clear reason no ADR is needed)
- [ ] No secrets committed (run `git diff --staged | grep -i secret` if unsure)
- [ ] No hardcoded paths (use env vars from `env.shared` / `.env.local`)
- [ ] If this changes infra, the cost/risk is documented in the PR description

## Screenshots / logs / measurements

<!-- For UI: screenshots. For perf: before/after numbers. For infra: terraform plan output. -->

## Related ADRs / docs

<!-- e.g. "implements ADR 004", "updates docs/repo_layout.md" -->
