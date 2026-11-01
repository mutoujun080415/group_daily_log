"""把仓库地址写进 manifest 的 repository_url / homepage / icon_url。

仓库建好后跑一次，之后每次发版都不用再手填这三项。

用法::

    python scripts/set_repo_meta.py --repo https://github.com/you/group_daily_log
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_PATTERN = re.compile(r"^https://[^/]+/[^/]+/[^/]+$")


def main() -> int:
    parser = argparse.ArgumentParser(description="写入 manifest 的仓库元数据")
    parser.add_argument("--repo", required=True, help="形如 https://github.com/<owner>/<repo>")
    parser.add_argument(
        "--manifest",
        default=str(Path(__file__).resolve().parent.parent / "manifest.json"),
        help="manifest.json 路径，默认插件根目录下那个",
    )
    parser.add_argument("--branch", default="main", help="默认分支名，用于拼 icon 的 raw 地址")
    args = parser.parse_args()

    repo = args.repo.strip().rstrip("/")
    if not REPO_PATTERN.match(repo):
        print(f"仓库地址应形如 https://github.com/<owner>/<repo>，收到：{args.repo}", file=sys.stderr)
        return 1

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"找不到 manifest：{manifest_path}", file=sys.stderr)
        return 1

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    icon_url = f"{repo}/raw/{args.branch}/icon.png"
    data["repository_url"] = repo
    data["homepage"] = repo
    data["icon_url"] = icon_url

    manifest_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("已写入 manifest：")
    print(f"  repository_url = {repo}")
    print(f"  homepage       = {repo}")
    print(f"  icon_url       = {icon_url}")
    print()
    print("接下来：")
    print("  git add manifest.json")
    print("  git commit -m 'manifest: 补仓库与图标元数据'")
    print("  git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
