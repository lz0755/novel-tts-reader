"""Push the preserved stage commits in order, without overwriting upstream history."""

import argparse
import subprocess


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


def git(*args, capture=False):
    r = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE if capture else None,
        check=True,
    )
    return r.stdout.strip() if capture else None


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--remote", default="origin")
    cli.add_argument("--branch", default="main")
    args = cli.parse_args()
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
    actual = git("ls-remote", args.remote, ref, capture=True).split()[0]
    if actual != git("rev-parse", "HEAD", capture=True):
        raise SystemExit("远端提交核对失败")
    print("verified", actual)


if __name__ == "__main__":
    main()
