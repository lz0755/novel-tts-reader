"""Push the preserved stage commits in order, without overwriting upstream history."""

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path


STAGES = (
    "m0-implementation",
    "m1-implementation",
    "m2-implementation",
    "m3-implementation",
    "m4-adapter",
    "m5-adapter",
    "m6-build",
    "m7-adapter",
)
TITLES = (
    "工程骨架与契约",
    "完整 Mock 闭环",
    "文本、角色与纠音",
    "任务、缓存、播放与恢复",
    "多供应商小说标注",
    "云端语音",
    "Windows 与 Ubuntu 核心包",
    "本地 Qwen 双模型",
)
START = "<!-- novel-tts-acceptance:start -->"
END = "<!-- novel-tts-acceptance:end -->"


def git(*args, capture=False):
    r = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE if capture else None,
        check=True,
    )
    return r.stdout.strip() if capture else None


def gh_api(path, method="GET", body=None):
    command = ["gh", "api", path]
    if body is not None:
        command += ["--method", method, "--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit(result.stderr.strip())
    return json.loads(result.stdout)


def update_tracker(repo, revision):
    path = "repos/" + repo + "/milestones"
    existing = {}
    page = 1
    while True:
        milestones = gh_api(f"{path}?state=all&per_page=100&page={page}")
        existing.update((item["title"], item) for item in milestones)
        if len(milestones) < 100:
            break
        page += 1
    data = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/evidence/acceptance.json"
        ).read_text(encoding="utf-8")
    )
    for number, (name, tag) in enumerate(zip(TITLES, STAGES)):
        stage = f"M{number}"
        title = stage + " " + name
        items = [i for i in data["items"] if i["stage"] == stage]
        if not items:
            raise SystemExit(f"{stage} 缺少验收数据")
        counts = Counter(i["status"] for i in items)
        url = f"https://github.com/{repo}"
        section = (
            f"{START}\n{items[0]['id']} 至 {items[-1]['id']}。"
            + "，".join(f"{n} 项{s}" for s, n in counts.items())
            + "。\n\n"
            + f"[阶段提交]({url}/tree/{tag}) · [阶段记录]({url}/blob/{revision}/docs/evidence/{stage}.md)"
            + f" · [逐项验收]({url}/blob/{revision}/docs/ACCEPTANCE.md)\n{END}"
        )
        current = existing.get(title)
        old = (current.get("description") or "") if current else ""
        if START in old and END in old:
            prefix, tail = old.split(START, 1)
            description = prefix + section + tail.split(END, 1)[1]
        else:
            description = (old.rstrip() + "\n\n" if old.strip() else "") + section
        state = "closed" if counts["通过"] == len(items) else "open"
        if current and description == old and state == current["state"]:
            continue
        body = dict(title=title, description=description, state=state)
        result = (
            gh_api(path + "/" + str(current["number"]), "PATCH", body)
            if current
            else gh_api(path, "POST", body)
        )
        print(result["html_url"], result["state"])


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--remote", default="origin")
    cli.add_argument("--branch", default="main")
    cli.add_argument(
        "--github",
        metavar="OWNER/REPO",
        help="同时按逐项验收记录更新 GitHub Milestones",
    )
    args = cli.parse_args()
    if args.github:
        remote = git(
            "remote", "get-url", "--push", args.remote, capture=True
        ).removesuffix(".git")
        targets = (
            f"https://github.com/{args.github}",
            f"git@github.com:{args.github}",
            f"ssh://git@github.com/{args.github}",
        )
        if not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.github
        ) or remote.casefold() not in {t.casefold() for t in targets}:
            cli.error("GitHub Milestones 的目标必须和远端仓库相同")
    git("check-ref-format", "--branch", args.branch)
    ref = "refs/heads/" + args.branch
    upstream = git("ls-remote", args.remote, ref, capture=True)
    tip = None
    if upstream:
        git("fetch", args.remote, ref)
        tip = git("rev-parse", "FETCH_HEAD", capture=True)
        if subprocess.run(
            ["git", "merge-base", "--is-ancestor", tip, "HEAD"]
        ).returncode:
            cli.error("远端已有独立历史。请在保留现有代码的项目分支上同步。")
    for stage in STAGES:
        commit = git("rev-parse", stage + "^{commit}", capture=True)
        if (
            tip
            and subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, tip]
            ).returncode
            == 0
        ):
            git("push", args.remote, "refs/tags/" + stage)
        else:
            git(
                "push",
                "--atomic",
                args.remote,
                commit + ":" + ref,
                "refs/tags/" + stage,
            )
    git("push", args.remote, "HEAD:" + ref)
    packages = git(
        "tag", "--points-at", "HEAD", "--list", "desktop-*", capture=True
    ).splitlines()
    if packages:
        git("push", args.remote, *["refs/tags/" + tag for tag in packages])
    actual = git("ls-remote", args.remote, ref, capture=True).split()[0]
    if actual != git("rev-parse", "HEAD", capture=True):
        raise SystemExit("远端提交核对失败")
    print("verified", actual)
    if args.github:
        update_tracker(args.github, actual)


if __name__ == "__main__":
    main()
