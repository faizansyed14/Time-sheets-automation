# Git workflow — local → dev → main

## 1. Push local changes to dev
```bash
git add -A
git commit -m "describe the change"
git push origin dev
```

## 2. Test on dev

## 3. Create PR: dev → main
GitHub → **Compare & pull request** (base: `main`, compare: `dev`)

## 4. Merge the PR
Click **"Create a merge commit."** Never Squash and merge, never Rebase and merge.

## 5. Sync dev back up
```bash
git checkout dev
git pull origin main
git push origin dev
```

---

## If step 3 shows "Can't automatically merge"

```bash
git stash push -u -m "wip"
git fetch origin
git merge origin/main
```
Fix each conflicted file, then:
```bash
git checkout --ours  -- <file>   # keep dev's version
git checkout --theirs -- <file>  # keep main's version
git add <file>
```
```bash
git status            # must say "All conflicts fixed but you are still merging"
git commit --no-edit
git push origin dev
```
Now repeat steps 3-5 above. Then restore your local work:
```bash
git stash pop
git add -A
git commit -m "describe the change"
git push origin dev
```
