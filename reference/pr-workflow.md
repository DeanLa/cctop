# PR Workflow

Standard process for shipping a self-contained PR, whether it comes from a backlog item, a pr-groups file, or an ad-hoc request.

## Steps

1. **Branch**: create a worktree with a descriptive branch name (e.g. `feat/table-columns`, `fix/turns-count`). Use `EnterWorktree`.
2. **Plan**: enter plan mode, read the relevant context (backlog items, issue description, user request), explore the touched files, write up an approach, and get user approval before proceeding.
3. **Implement**: make changes with detailed, granular commits (one commit per logical change, not one big squash).
4. **Test**: run existing tests, add new tests where appropriate.
5. **Reinstall**: run `./install.sh --dev` and validate in a fresh Claude session. **Skip this step when other worktrees are running in parallel**, the install is global and worktrees would overwrite each other. Instead, install and validate one PR at a time when ready, or defer to after merge.
6. **Push & PR**: push the branch, open a PR with a summary of what changed.
7. **Review**: self-review or get CR before merging.
8. **Close the loop**: after merge, switch to `main`, pull, and update any tracking docs (e.g. mark items done in `BACKLOG.md`, check off the group in a pr-groups file). Do this on `main`, not in the worktree, to avoid conflicts when multiple PRs run in parallel.

## When to use

- Working on a PR group from `plans/pr-groups.md`
- Picking up a single backlog item
- Ad-hoc feature or fix the user asks for ("add X", "fix Y")
- Any change that deserves its own branch and review

## Adapting for context

- **Backlog items**: reference item numbers in commits and PR description, mark done in `BACKLOG.md` after merge.
- **PR groups**: also check off the group in the pr-groups file after merge.
- **Ad-hoc work**: no tracking file to update, just close the loop on `main` (pull, verify clean state).
