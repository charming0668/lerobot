# 基于他人仓库的 Git 开发实战教程

本文面向「已经 `git clone` 了别人的项目，并要在此基础上长期开发」的场景。目标不是罗列 Git 命令，而是按你真正会遇到的阶段，说明：**这时仓库处于什么状态、风险是什么、一般怎么处理、什么时候不要乱动。**

阅读约定：

- `origin`：你日常 `push` / `pull` 的远程。若你有写权限，通常就是原仓库；若没有，通常是你自己的 fork。
- `upstream`：原作者仓库。没有写权限时，用它来同步最新代码。
- 默认分支可能叫 `main`、`master` 或 `develop`，下文统一写成 `main`。
- 文中命令均需在仓库根目录执行。执行破坏性操作前，先用 `git status` 和 `git log --oneline -n 10` 确认当前状态。

---

## 1. 先搞清楚你拿到的是哪一种仓库

克隆之后，先判断协作模式。后续几乎所有操作都取决于这一点。

### 1.1 你有直接写权限（同事仓库 / 自己被加为 collaborator）

典型特征：

- `git remote -v` 只有一个 `origin`，指向原仓库。
- 你可以直接向 `origin` 推送分支。

一般做法：在本地建功能分支，推到 `origin`，再提 Pull Request / Merge Request 到 `main`。不要直接在 `main` 上开发。

### 1.2 你没有写权限（开源项目 / 外部仓库）

典型特征：

- 原仓库不允许你 `push`。
- 正确做法是先 fork，再克隆自己的 fork，并把原仓库加为 `upstream`。

如果你当时直接克隆了原仓库，后面会推不上去。这时不要重新从头开始，按第 3 节补远程即可。

### 1.3 克隆下来后先做一次体检

```bash
git status
git remote -v
git branch -vv
git log --oneline --decorate -n 15
```

你应确认四件事：

1. 当前是否在默认分支上。
2. 工作区是否干净。
3. 远程叫什么、指向哪里。
4. 本地分支是否跟踪了远程分支。

如果克隆后立刻有大量改动，多半是换行符（CRLF/LF）、文件权限或 Git LFS 未拉取，而不是你已经开始开发了。这时先处理环境，再写代码。

---

## 2. 第一次配置：只做一次，但必须做对

### 2.1 身份信息

提交会带上作者名和邮箱。建议至少在本仓库设置：

```bash
git config user.name "Your Name"
git config user.email "you@example.com"
```

不要用别人的身份提交。开源项目如果要求签署 DCO / CLA，邮箱通常要和 GitHub / GitLab 账号一致。

### 2.2 是否改全局配置

可以改、但要克制：

- 适合全局：`user.name`、`user.email`（若你所有仓库都用同一身份）、`init.defaultBranch`、`pull.rebase`（团队有约定时）。
- 不要随便全局开启：`core.autocrlf`、`core.filemode`、别名覆盖常用命令。

Linux 上一般保持：

```bash
git config --global core.autocrlf input
```

### 2.3 上游默认分支与跟踪关系

```bash
git branch --set-upstream-to=origin/main main
```

之后在 `main` 上可以直接 `git pull` / `git push`，不必每次写远程和分支名。

### 2.4 大文件与子模块

克隆后如果构建缺文件、图片/模型是指针文件、或目录是空的，检查：

```bash
git lfs install
git lfs pull
git submodule update --init --recursive
```

这三类问题看起来像「代码不完整」，其实是仓库内容没按原作者的方式取全。

---

## 3. 远程仓库：origin、upstream 与多远程

### 3.1 标准 fork 布局

```text
你的电脑
  ├── origin    -> 你的 fork（可写）
  └── upstream  -> 原作者仓库（只读，用来同步）
```

补齐远程：

```bash
git remote add upstream https://github.com/original/repo.git
git remote -v
git fetch upstream
```

SSH 与 HTTPS 不要混用得自己都分不清。选一种，并保证 `git fetch` 能通。

### 3.2 你克隆错了对象时怎么补

情况 A：克隆了原仓库，后来才 fork。

```bash
git remote rename origin upstream
git remote add origin https://github.com/you/repo.git
git fetch origin
git fetch upstream
```

情况 B：克隆了自己的 fork，但没加原仓库。

```bash
git remote add upstream https://github.com/original/repo.git
git fetch upstream
```

情况 C：远程 URL 写错，或要从 HTTPS 改成 SSH。

```bash
git remote set-url origin git@github.com:you/repo.git
```

### 3.3 多个 fork / 多个合作者远程

可以继续加远程，例如 `alice`、`bob`。用途是临时取别人的分支：

```bash
git remote add alice https://github.com/alice/repo.git
git fetch alice
git checkout -b review-alice-feat alice/feat-x
```

用完可以删：

```bash
git remote remove alice
```

不要把别人的远程当成自己的日常 `push` 目标。

---

## 4. 日常开发的主干流程

这是你 80% 时间会重复的路径。

### 4.1 从最新默认分支拉出功能分支

```bash
git checkout main
git fetch origin
git pull --ff-only origin main
git checkout -b feat/short-name
```

分支命名建议带意图：`feat/`、`fix/`、`docs/`、`refactor/`、`chore/`。名字要能在 PR 列表里一眼看懂。

`--ff-only` 的意义：如果 `main` 不能快进，说明本地 `main` 被你污染了，这时不要盲目生成合并提交，先停下来看第 8 节。

### 4.2 改代码、查看、暂存、提交

```bash
git status
git diff
git add -p                 # 按块暂存，避免把无关改动塞进一次提交
git commit
```

提交信息写「为什么」，不要只写「改了什么」。一次提交对应一个可回退的意图。

不要做的事：

- 在 `main` 上直接开发。
- 把格式化、重构、功能实现打进同一个提交。
- 提交生成物、密钥、大数据、本地日志。

### 4.3 推送到你可写的远程

```bash
git push -u origin feat/short-name
```

第一次加 `-u`，之后同一分支只需 `git push`。

### 4.4 打开 Pull Request / Merge Request

目标分支通常是原仓库的 `main`，而不是你 fork 的 `main`。

检查清单：

- 基线是否足够新，避免审查者先帮你解决冲突。
- 是否包含不该出现的文件。
- CI 是否要求特定提交信息、测试、签署。

### 4.5 审查意见来了之后怎么改

继续在同一功能分支上提交，然后 `git push`。PR 会自动更新。

如果仓库要求「一个 PR 一个提交」，用第 9 节的压缩历史，而不是继续堆小提交。推送前先确认该分支没有别人基于它继续开发。

---

## 5. 如何把原作者的更新同步过来

这是 clone 他人项目后最核心、也最容易做错的操作。

### 5.1 更新本地 `main`（有写权限，只有 origin）

```bash
git checkout main
git fetch origin
git pull --ff-only origin main
```

### 5.2 更新本地 `main`（fork 工作流，有 upstream）

```bash
git checkout main
git fetch upstream
git merge --ff-only upstream/main
git push origin main
```

效果：本地 `main` 与原作者快进对齐，再把这个快进结果推回你的 fork。你的 fork 的 `main` 应尽量保持「纯净镜像」，不要在上面堆自己的提交。

### 5.3 把最新 `main` 合进正在开发的功能分支

有两种主流做法，选团队习惯，不要混用。

**合并（更安全，保留历史节点）：**

```bash
git checkout feat/short-name
git fetch upstream          # 或 origin，取决于你跟踪谁
git merge main
```

**变基（历史更直，但改写了你的分支）：**

```bash
git checkout feat/short-name
git fetch upstream
git rebase main
```

变基后如果该分支已经推送过：

```bash
git push --force-with-lease
```

只对**你自己的功能分支**使用强制推送。永远不要对 `main` 做这件事。

### 5.4 原作者使用了 squash merge 或 rebase merge

你的功能分支一旦合入，远程 `main` 上可能看不到你原来的那些提交哈希。这是正常的。合入后：

```bash
git checkout main
git pull --ff-only
git branch -d feat/short-name
git push origin --delete feat/short-name
```

本地不要再拿旧功能分支去对齐 `main`，直接删掉，从新的 `main` 再开分支。

### 5.5 原作者 force-push 了默认分支

较少见，但开源项目会偶尔发生。症状：`git pull --ff-only` 失败，提示历史分叉。

处理原则：

1. 先确认不是你本地误提交造成的分叉。
2. 再看远程公告、Issue、聊天记录，确认上游确实重写了历史。
3. 如果你还没有基于旧 `main` 做重要工作：

```bash
git fetch upstream
git checkout main
git reset --hard upstream/main
git push --force-with-lease origin main     # 仅当你维护的是自己的 fork
```

4. 如果你已经有功能分支：把功能分支 rebase 到新的 `upstream/main`，冲突会比平时多。必要时把补丁导出后再重新申请。

---

## 6. 冲突：什么时候出现，怎么解

冲突不是错误，而是 Git 明确告诉你：同一区域有两份改动，它无法代你决定。

### 6.1 常见触发点

- `git pull`（远程和本地都改了同一文件）
- `git merge main`
- `git rebase main`
- `git cherry-pick`
- `git stash pop`
- 多人改了同一功能分支

### 6.2 标准解除步骤

```bash
git status                  # 看哪些文件 unmerged
# 编辑冲突文件，删掉 <<<<<<< ======= >>>>>>> 标记
git add <已解决的文件>
git status                  # 确认没有未解决冲突
```

若你在 merge 中：

```bash
git commit                  # 完成合并提交
```

若你在 rebase / cherry-pick 中：

```bash
git rebase --continue
# 或
git cherry-pick --continue
```

### 6.3 解不下去时如何中止

```bash
git merge --abort
git rebase --abort
git cherry-pick --abort
```

中止后仓库回到操作前。这是你最该记住的安全出口。不要在一堆未解决冲突上继续新功能。

### 6.4 冲突解决的判断标准

- 语义冲突比文本冲突更危险：两边都能合并，但逻辑互斥。测试必须跑。
- 不要「全部接受我的」或「全部接受他们的」，除非你真的理解另一边改了什么。
- 二进制文件无法文本合并，只能选一边，或用新文件替换后再 `git add`。

查看某一文件两边版本：

```bash
git show :2:path/to/file    # ours
git show :3:path/to/file    # theirs
```

注意：rebase 时 ours/theirs 的含义和 merge 相反。不确定就用 `git status` 和文件内容判断，不要死记标签。

---

## 7. 暂存、中断与现场保护

### 7.1 正在改一半，却必须切分支

```bash
git stash push -u -m "wip: login form"
git checkout main
# ... 做别的事
git checkout feat/short-name
git stash pop
```

`-u` 会连未跟踪文件一起收起来。`stash pop` 可能冲突，处理方式与 merge 相同。

若你只是想把改动先存着、稍后再看：

```bash
git stash list
git stash show -p stash@{0}
git stash apply stash@{0}    # 应用但保留
git stash drop stash@{0}     # 确认无误再删
```

### 7.2 什么时候不该用 stash

- 改动已经很大，值得做成真正的提交（哪怕标成 `WIP:`）。
- 你要切到很久以前的提交做实验：用 worktree，而不是在同一工作区来回 stash。
- 你准备 rebase / merge：先提交或 stash，不要让未提交改动和历史改写缠在一起。

### 7.3 并行开发：worktree

同一仓库需要同时改两个分支时：

```bash
git worktree add ../repo-hotfix hotfix/crash
```

两个目录共享对象库，但工作区独立。这比反复 stash 干净得多。

用完：

```bash
git worktree remove ../repo-hotfix
```

---

## 8. 把错误提交纠正回来

先判断两件事：**提交是否已推送**，以及 **是否已有别人基于它开发**。

### 8.1 还没提交，只是改乱了工作区

丢弃某个文件的未暂存修改：

```bash
git restore path/to/file
```

取消暂存，但保留修改：

```bash
git restore --staged path/to/file
```

全部回到最近一次提交（危险）：

```bash
git restore --staged --worktree .
git status
```

`git restore` 比旧的 `git checkout --` 更不容易误切分支。

### 8.2 最新一次提交信息写错，或漏改了一个文件

仅当这是你自己的提交、且尚未被他人使用：

```bash
git add path/to/missing-file
git commit --amend --no-edit     # 只补文件
git commit --amend               # 同时改说明
```

若已推送：

```bash
git push --force-with-lease
```

公开默认分支上不要 amend。

### 8.3 不该提交的内容已经进了最近一次 commit

如果是刚刚发生、尚未推送：

```bash
git reset --soft HEAD~1         # 回退提交，保留改动在暂存区
# 或
git reset HEAD~1                # 回退提交，改动留在工作区
```

然后重新挑选要提交的文件。

### 8.4 提交已经推送，需要公开撤销

不要改写已共享历史，用新提交抵消：

```bash
git revert <commit-sha>
git push
```

撤销一次 merge 提交时通常需要：

```bash
git revert -m 1 <merge-commit-sha>
```

`-m 1` 表示保留第一亲本（通常是主分支）的方向。这是少数需要小心阅读 `git log --graph` 的操作。

### 8.5 提交推到了错误的分支

例如本该在 `feat/x`，却提交到了 `main`。

若尚未推送：

```bash
git branch feat/x               # 先用当前提交建出正确分支
git reset --hard origin/main    # 再把 main 退回远程
git checkout feat/x
```

若已经推送到你自己的 fork 的 `main`，且确定没有其他人拉过：

```bash
git push --force-with-lease origin main
```

若已经推到共享的官方 `main`：不要 reset。用 `revert`，再把正确改动 cherry-pick 到功能分支。

### 8.6 误删分支或「提交不见了」

```bash
git reflog
git checkout -b recover <reflog中的sha>
```

`reflog` 是本地安全网，通常能找回最近 90 天内 HEAD 走过的位置。远程没有你的 reflog。

### 8.7 密钥、密码、token 被提交

这不是「从 Git 里删掉文件」就能结束的事。

1. 立刻轮换密钥，把旧凭证作废。
2. 用新提交从后续历史中移除文件，并加入 `.gitignore`。
3. 如果密钥已经推到公开远程，假定它已泄露。
4. 是否改写全部历史，取决于仓库是否已广泛克隆。改写历史不能让已经 clone 的人自动忘掉那个文件。

---

## 9. 整理提交历史（在分享之前）

### 9.1 把多个本地提交压成一个

功能做完、准备提 PR 前：

```bash
git fetch origin
git rebase -i origin/main
```

在编辑器里把需要合并的提交改成 `squash` 或 `fixup`，保存后 Git 会让你编一版最终说明。

仅在你独有的分支上做。交互式 rebase 会改提交哈希。

### 9.2 调整提交顺序、拆分提交

同样进入 `git rebase -i`：

- `reword`：只改说明
- `edit`：停在该提交，把一次提交拆成多次
- `drop`：丢掉该提交

拆分时：

```bash
git reset HEAD~1
git add -p
git commit
# 重复直到工作区干净
git rebase --continue
```

### 9.3 cherry-pick：只要某一次提交

```bash
git checkout feat/other
git cherry-pick <sha>
```

适用：hotfix 已经在某分支，需要同样的修复出现在另一条线上。不要用 cherry-pick 当日常同步手段，否则同一改动会出现两个哈希，后续合并更乱。

冲突时：

```bash
git cherry-pick --continue
# 或
git cherry-pick --abort
```

### 9.4 导出补丁与从补丁应用

没有共同远程、或只想邮件式传递时：

```bash
git format-patch origin/main --stdout > my.patch
git apply --check my.patch
git am < my.patch
```

这在嵌入式、内网、或对方不接受你直接 push 的场景仍常见。

---

## 10. 拉取策略：merge、rebase 与 fast-forward

`git pull` 默认可能是 merge，也可能被配置成 rebase。不要依赖默认，把意图写出来。

### 10.1 快进

本地没有独有提交，只是落后：

```bash
git pull --ff-only
```

这是更新 `main` 的首选。失败就说明出现了你没意识到的分叉。

### 10.2 pull --rebase

功能分支上，你有本地提交，远程同事也推了新提交：

```bash
git pull --rebase
```

你的提交会接到远程最新之后，历史更直。若已有冲突中的 rebase，先处理完，不要再套一层 pull。

### 10.3 普通 merge pull

会多一个 merge commit。适合明确想保留「何时集成」的节点，或团队禁止 rebase。

### 10.4 不要做的 pull

- 在脏工作区 `git pull`。
- 在共享 `main` 上 `git pull --rebase` 后再 force-push。
- 连续失败后改用 `reset --hard` 而不看将丢失什么。

---

## 11. 强制推送：唯一允许的几种情况

命令本身：

```bash
git push --force-with-lease
```

永远优先 `--force-with-lease`，而不是 `--force`。前者会在远程分支已被别人更新时拒绝覆盖。

允许：

- 你刚 amend / rebase 了**自己的**功能分支。
- 你刚把误提交从**自己的 fork 的功能分支**上拿掉。
- 原作者声明默认分支已重写，你在同步自己的 fork 镜像。

禁止：

- 官方 `main` / `release`。
- 别人正在基于该分支工作，且没有事先约定。
- 用 force-push「清理」已经进入评审、且审查者按旧哈希评论过的历史——可以做，但要在 PR 里说明，避免评论错位。

---

## 12. 标签、版本与发布

### 12.1 查看与创建

```bash
git fetch --tags
git tag -l
git tag -a v1.2.3 -m "Release 1.2.3"
git push origin v1.2.3
```

轻量标签只是一个名字；附注标签带说明和签名信息，发布更常用后者。

### 12.2 你通常不该动别人的标签

标签一旦发布，视为不可变。下游、打包系统和用户会钉住它。发现 tag 打错时，优先发新版本号，而不是移动旧 tag。

若必须删除未广泛使用的 tag：

```bash
git tag -d v1.2.3
git push origin :refs/tags/v1.2.3
```

并通知所有已拉取该 tag 的人。

---

## 13. 子模块、子树与 monorepo 外围仓库

### 13.1 子模块

别人项目里出现只记录某个 commit 的目录，就是 submodule。

初始化：

```bash
git submodule update --init --recursive
```

更新到上游指定版本：

```bash
git submodule update --remote
git add path/to/submodule
git commit -m "Bump dependency submodule"
```

常见坑：

- 只更新了子模块工作区，却忘了在父仓库提交新的指针。
- 直接在子模块里改代码却不推送子模块仓库，别人 clone 后会指向不存在的 commit。
- `git clone` 忘了 `--recurse-submodules`。

删除或迁移子模块容易残留配置，应按该仓库文档操作，不要只删目录。

### 13.2 子树 subtree

把另一个仓库的历史并入子目录。对使用者更透明，但后续拆回独立仓库更麻烦。遇到 `git subtree pull/push` 时，先读该项目 README，不要按 submodule 的方式处理。

---

## 14. Git LFS、大文件与误提交的体积问题

### 14.1 正常使用

```bash
git lfs install
git lfs track "*.pt"
git add .gitattributes
git lfs pull
```

克隆后看到几十字节的文本指针而不是模型文件，说明 LFS 对象没取下来。

### 14.2 不小心把大文件提交进普通 Git

哪怕立刻删掉文件，历史里仍有那份 blob，克隆会一直慢。

尚未推送：用 `reset` 回到提交前，把文件加入 `.gitignore` 再重新提交。

已经推送：需要历史过滤（`git filter-repo` 等），并让所有协作者重新克隆。这是高成本操作，先和仓库维护者确认。

---

## 15. 忽略规则、换行符、权限与「幽灵改动」

### 15.1 .gitignore

只忽略不会被源码引用的生成物、密钥、环境文件、编辑器目录。已经跟踪的文件，加入 `.gitignore` 不会自动失效，需要：

```bash
git rm --cached path/to/file
git commit
```

这只是停止跟踪，不是删除你磁盘上的文件。

### 15.2 全仓库突然变成「全部修改」

常见原因：

- Windows/Linux 换行符转换不一致。
- `core.filemode` 把可执行位变化当成内容变化。
- 大小写不敏感文件系统上重命名文件。

先不要提交。核对：

```bash
git config core.autocrlf
git config core.filemode
git diff --stat
```

若只是文件模式：

```bash
git config core.filemode false     # 仅在该仓库、且团队确认后
```

### 15.3 大小写重命名

Git 在某些磁盘上认为 `Foo.py` 和 `foo.py` 是同一文件。应分两步：

```bash
git mv Foo.py Foo.py.tmp
git mv Foo.py.tmp foo.py
```

---

## 16. 分离头指针、检查旧版本、二分查找

### 16.1 detached HEAD

`git checkout <sha>` 或检出 tag 后，你会离开分支。可以看代码、跑实验，但新的提交不属于任何分支，一离开就难找。

要基于该点开发：

```bash
git switch -c feat/from-old-tag
```

只是看看，然后回去：

```bash
git switch main
```

### 16.2 对比版本

```bash
git log -S "function_name" --source --all
git blame path/to/file
git diff main...HEAD
git diff origin/main...feat/short-name
```

`A...B`（三点）看的是自共同祖先以来 B 的独有改动，适合看 PR 实际引入了什么。

### 16.3 用 bisect 找哪次提交引入了 bug

```bash
git bisect start
git bisect bad HEAD
git bisect good v1.0.0
# 每次编译/测试后：
git bisect good
# 或
git bisect bad
git bisect reset
```

这是排查「从某天开始坏了」最有效的 Git 操作，比凭感觉回滚更可靠。

---

## 17. 与原作者、与其他贡献者协作时的特殊情况

### 17.1 你的 PR 被要求 rebase，但远程分支已更新

```bash
git fetch upstream
git rebase upstream/main
git push --force-with-lease
```

### 17.2 维护者直接在你的 PR 分支上加了提交

有些项目允许维护者 push 到 fork 的 PR 分支。这时不要 rebase 后强推，除非沟通确认。应：

```bash
git fetch origin
git pull --rebase origin feat/short-name
```

或直接 merge，避免覆盖维护者的提交。

### 17.3 同时给多个 PR、分支互相依赖

尽量让每个 PR 可独立合并。若 B 依赖 A：

- 先把 A 合入。
- 再把 B rebase 到新的 `main`。
- 不要让 B 长期以 A 的功能分支为上游，除非团队明确接受 stacked PR。

### 17.4 你改了别人正在审的同一文件

先同步 `main`，再解决冲突，并在 PR 描述里说明冲突来源。不要在冲突里「顺手重构」无关代码。

### 17.5 原仓库改了默认分支名

例如 `master` 改 `main`：

```bash
git fetch origin
git remote set-head origin -a
git branch -m master main
git branch --set-upstream-to=origin/main main
```

### 17.6 仓库被转移、重命名或换成镜像

```bash
git remote set-url origin <new-url>
git fetch origin
```

旧 URL 还能用时不要拖太久，否则某一天权限或重定向会断。

### 17.7 你需要上游尚未合并的别人的 PR

```bash
git fetch upstream pull/123/head:pr-123
git checkout pr-123
```

GitHub 可用 `pull/ID/head`；GitLab 常用 `merge-requests/ID/head`。这只是本地只读取用，不要直接往这个分支 push。

---

## 18. 保护现场的检查清单（每次危险操作前）

在 `reset --hard`、`rebase`、`push --force-with-lease` 之前：

1. `git status` 是否干净。
2. `git branch --show-current` 是否真的是你以为的分支。
3. 该分支是否已经推送、是否有别人使用。
4. 需要时先建备份分支：

```bash
git branch backup/feat-short-name-before-rebase
```

出问题可以立刻：

```bash
git reset --hard backup/feat-short-name-before-rebase
```

---

## 19. 按场景速查

| 场景 | 一般操作 | 不要做 |
| --- | --- | --- |
| 刚 clone，准备开发 | 体检远程与分支，从最新 `main` 拉功能分支 | 直接在 `main` 改 |
| 没有写权限 | fork + `origin`/`upstream` 双远程 | 对着原仓库反复 `push` 失败后改 `--force` |
| 原作者更新了 | `fetch` + 快进 `main`，再 merge/rebase 到功能分支 | 在脏工作区 pull |
| 功能做完 | 推送功能分支，提 PR | 把功能分支合进自己 fork 的 `main` 后再提 PR（除非项目要求） |
| 提交信息写错且未推送 | `commit --amend` | 已经有人拉取后还 amend 官方分支 |
| 提交已进共享历史 | `git revert` | `reset --hard` 后强推 `main` |
| 切分支但改了一半 | `stash` 或临时 commit，或 worktree | 带着脏工作区硬切 |
| 冲突 | 手动解决后 `add`，再 `commit` / `--continue` | 盲目 `checkout --theirs` |
| 历史要变直 | 对**自己的**分支 `rebase` + `--force-with-lease` | 对 `main` rebase |
| 只要某一次修复 | `cherry-pick` | 用 cherry-pick 当长期同步 |
| 代码突然全红 | 查换行符/权限/LFS | 立刻巨大 commit |
| 提交丢失 | `reflog` 恢复 | 立刻 `gc --prune=now` |
| 密钥进历史 | 先作废密钥 | 只删文件以为没事 |
| 并行修两个 bug | worktree | 同一目录反复 stash 到迷路 |
| 找引入点 | `bisect` | 凭感觉连续 `reset` |

---

## 20. 推荐的日常节奏（可直接照做）

**开始一天：**

```bash
git checkout main
git fetch --all --prune
git pull --ff-only
git checkout feat/short-name
git merge main                 # 或 rebase，与团队一致
```

`--prune` 会删掉远程已不存在的跟踪分支引用，避免本地一直看到已合并并删除的旧分支。

**开发过程中：**

```bash
git status
git diff
git add -p
git commit
git push
```

**准备请人看代码：**

```bash
git fetch upstream
git rebase upstream/main       # 若团队用 rebase
git log --oneline upstream/main..HEAD
git push --force-with-lease    # 仅当 rebase 过
```

然后开 PR。

**PR 合入之后：**

```bash
git checkout main
git pull --ff-only
git branch -d feat/short-name
git fetch --prune
```

---

## 21. 原则：记住这五条，就不易把仓库搞坏

1. **默认分支保持纯净。** 自己的工作只放功能分支。
2. **先 fetch，再决定 merge 还是 rebase。** 不要让 `git pull` 替你做未声明的策略。
3. **已共享的历史用 revert 补救，未共享的历史才能改写。**
4. **强制推送只对准自己的功能分支，并且用 `--force-with-lease`。**
5. **不确定时先建备份分支。** Git 很少真正丢掉对象，真正危险的是你在错误分支上执行了 `reset --hard` 和强推。

---

## 22. 附录：最常用命令的职责边界

| 命令 | 解决什么 | 是否改写历史 | 典型风险 |
| --- | --- | --- | --- |
| `git fetch` | 只下载远程信息 | 否 | 几乎无 |
| `git pull --ff-only` | 安全更新落后分支 | 否 | 不能快进时会失败，这是优点 |
| `git merge` | 集成另一条线 | 否 | 冲突；多余 merge commit |
| `git rebase` | 把你的提交接到新基线上 | 是 | 已推送分支需强推 |
| `git stash` | 临时搁置未提交改动 | 否 | pop 冲突；stash 忘记丢 |
| `git reset --soft` | 撤提交、留暂存 | 是 | 已推送时会分叉 |
| `git reset --hard` | 丢工作区与提交 | 是 | 未提交内容不可恢复 |
| `git revert` | 用新提交抵消旧提交 | 否 | merge 提交要选 `-m` |
| `git cherry-pick` | 复制某次提交 | 否 | 重复提交、后续合并重复 |
| `git commit --amend` | 改最后一次提交 | 是 | 已推送需强推 |
| `git push --force-with-lease` | 更新已改写的远程分支 | 覆盖远程引用 | 用错分支会丢别人的提交 |

把这些边界记住后，遇到新情况也可以自己归类：这是「同步」，还是「改写」，还是「公开撤销」。分类对了，命令就不会选错。
