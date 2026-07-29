# AGENTS.md

Read and follow `CLAUDE.md` before making changes. It contains the project
architecture, commands, privacy constraints, coding conventions, and testing
requirements.

Before finishing an implementation:

1. Run the most relevant focused tests.
2. Run `mise run format-check` and `mise run lint`.
3. Run `mise run test-unit` when the scope warrants it.
4. Run `mise run test-e2e` for user-interface or browser-behavior changes.

Do not modify or delete local databases, backups, statement files, or other
untracked user data unless explicitly requested.
