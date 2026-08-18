# Git workflow reference — merging `dev` into `main` without losing local work

## Do you need all of this every time? No.

This is the **exceptional** path, not the everyday one. Most of the time,
merging `dev` into `main` is just:
```bash
git add -A && git commit -m "..." && git push origin dev
```
then open GitHub and click **Create pull request → Merge pull request**. No
stash, no manual conflict resolution, nothing else — because most of the time
`main` hasn't changed since you last synced, so there's nothing to conflict
with.

**The whole stash → merge → resolve → PR → pop dance below is only needed when
`dev` and `main` have actually diverged** — i.e. `main` picked up a commit
`dev` doesn't have (a teammate merged something directly to `main`, or you
pushed to `main` through a different route) at the same time `dev` also moved
forward on its own. That's the one situation a plain PR can't merge cleanly.

**Check for that BEFORE doing anything else:**
```bash
git fetch origin
git log --oneline origin/dev..origin/main    # commits on main that dev doesn't have
git log --oneline origin/main..origin/dev    # commits on dev that main doesn't have
```
- **Second line has commits, first line is empty** → normal case. `dev` is
  simply ahead. Just commit, push, PR, merge — done, skip everything below.
- **Both lines have commits** → branches have diverged. GitHub will show
  "Can't automatically merge" on the compare page. Follow the steps below.

This documents the exact situation we hit last time and the steps that
resolved it, so the same workflow can be repeated whenever that divergence
happens again.

## The situation

- `dev` had 2 commits not on `main` (already pushed to `origin/dev`).
- `main` had 1 commit not on `dev` (a parallel change, e.g. a PR merged directly
  to `main` while `dev` kept moving on its own).
- The working directory had a large set of **uncommitted** local changes (a
  whole session's worth of work) that needed to survive, but should NOT be part
  of the `dev` → `main` pull request.
- GitHub's compare view showed **"Can't automatically merge"** — a real
  conflict, not just a formality, because both branches had touched the same
  files with different content.

## Why order matters here

If you commit local changes onto `dev` and push before creating the PR, those
changes become part of what gets merged into `main` — even if that's not what
you wanted. The fix is to get the uncommitted work **out of the way** (stash),
resolve the branch-level conflict on its own, land that in `main` via a clean
PR, and only then bring the local work back and push it separately.

## Steps taken, in order

### 1. Set local changes aside without committing them
```bash
git stash push -u -m "session work in progress"
```
`-u` also stashes untracked (new) files, not just modified ones. This leaves
the working directory exactly matching the last commit on `dev` — clean enough
to merge safely.

### 2. Merge `main`'s new commit into `dev` locally
```bash
git fetch origin
git merge origin/main
```
This is where the real conflicts surface — git marks every file it can't
auto-resolve and stops, waiting for you to fix them:
```
CONFLICT (content): Merge conflict in <file>
Automatic merge failed; fix conflicts and then commit the result.
```

### 3. Resolve the conflicts
For each conflicted file, git leaves `<<<<<<< HEAD` / `=======` / `>>>>>>> origin/main`
markers around the differing sections. Two ways to resolve, depending on the
situation:

- **Manually edit** the file, decide what to keep from each side (or a
  combination), delete the markers, save.
- **Take one side entirely** for a file, when you know the whole file should
  just be your version (or theirs):
  ```bash
  git checkout --ours  -- <file>   # keep YOUR (current branch's) version entirely
  git checkout --theirs -- <file>  # keep THEIRS (the branch being merged in) entirely
  git add <file>                   # mark it resolved either way
  ```
  In this session, all 5 conflicted files were resolved with `--ours` (keep
  `dev`'s version), since `dev` was the more current/complete state and
  `main`'s incoming changes were superseded.

Check nothing was missed:
```bash
git status                 # should say "All conflicts fixed but you are still merging"
```

### 4. Complete the merge commit
```bash
git commit --no-edit
```
This finalizes the merge as one commit on `dev`.

### 5. Push the resolved `dev`
```bash
git push origin dev
```
At this point `origin/dev` contains `main`'s content too, conflict-free — a PR
from `dev` → `main` will now show as cleanly mergeable.

### 6. Create and merge the PR
- GitHub → **Compare & pull request** (base: `main`, compare: `dev`)
- Confirm no conflict warning this time
- Merge it

### 7. Bring the local work back
```bash
git stash pop
```
Re-applies everything set aside in step 1, on top of the now-merged `dev`. If
the merge touched the exact same lines your stash touched, you can get a
*second*, usually much smaller, round of conflicts here — resolve the same way
as step 3.

### 8. Commit and push the restored work
```bash
git add -A          # or add specific files after reviewing `git status`
git commit -m "..."
git push origin dev
```

## Quick reference: when do I need to do this again?

Run this to check for drift before starting any new work:
```bash
git fetch origin
git log --oneline origin/dev..origin/main    # commits on main that dev doesn't have
git log --oneline origin/main..origin/dev    # commits on dev that main doesn't have
```
- Both empty → branches are in sync, nothing to do.
- Only the second has commits → normal case, dev is just ahead, PR will merge cleanly.
- Both have commits → branches have diverged; expect a conflict, follow the steps above.

## Key things to remember

- **`git stash` is local-only** — nothing is pushed or lost; `git stash list`
  shows what's saved, `git stash pop` restores the most recent one.
- **`--ours` / `--theirs` during a `git merge`** refers to: `--ours` = the
  branch you're currently on (here, `dev`), `--theirs` = the branch you're
  merging in (here, `origin/main`). (Note: during a `git rebase`, these two
  are swapped — worth double-checking which command you're in before trusting
  the label.)
- **Always `git fetch` before deciding anything** — it's read-only (updates
  your knowledge of the remote branches) and never changes your working
  directory or any local branch.
- **A PR reflects the remote branch at the time you look at it** — local
  commits that haven't been pushed yet never show up in it, which is exactly
  what let step 1 (stash) keep local work out of the merge.
