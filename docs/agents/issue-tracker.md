# Issue tracker: GitHub

Issues and specifications for this repository live in GitHub Issues. Use the `gh` CLI.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open`
- Comment: `gh issue comment <number> --body "..."`
- Add/remove labels: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Infer the repository from the configured GitHub remote when this directory becomes a clone.

## Pull requests as a triage surface

PRs as a request surface: no.

## Publishing

When a skill says to publish to the issue tracker, create a GitHub issue.
