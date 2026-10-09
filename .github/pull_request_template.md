[English](pull_request_template.md) · [简体中文](pull_request_template.zh-CN.md)

## Summary

<!-- What does this PR do? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactor / chore

## Testing

- [ ] `make test-api` (API tests; the six current-invocation consumers run in acceptance)
- [ ] `npm run test` (ui)
- [ ] Manual smoke test (describe):

## Checklist

- [ ] Documentation updated if needed
- [ ] No secrets or credentials in diff
- [ ] Follows existing code style

- [ ] Bilingual docs and indexes synchronized; architecture diagrams regenerated with the skill and SVG/PNG reviewed
- [ ] `./scripts/check-docs.sh`
- [ ] `make quality-check`
