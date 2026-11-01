"""把群日报发布到 Neo-MoFox 插件市场。

市场的作者认证走 **GitHub 令牌**（`Authorization: Bearer <GitHub PAT>`），
`mfox_...` 那种是机器令牌，只能读订阅，发不了插件。

前提：GitHub 仓库已经打好 tag 并创建了 Release，且 Release 附件就是本地这个
`.mfp`（市场按 asset_download_url + checksum_sha256 取包）。

用法::

    set MARKET_GITHUB_TOKEN=ghp_xxx
    python scripts/publish_market.py --dry-run      # 只看要发什么
    python scripts/publish_market.py                # 真正提交

令牌只需要 `public_repo`（仓库是公开的）或 `repo` 权限，用完建议在 GitHub 撤销。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MARKET = "https://39.96.71.162"
PLUGIN_ID = "group_daily_log"
SUPPORTED_PLATFORMS = ["windows", "linux", "macos"]


def build_payloads(root: Path, package: Path, repo: str, tag: str) -> tuple[dict, dict]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    readme = (root / "README.md").read_text(encoding="utf-8")
    icon_path = root / "icon.png"
    icon_b64 = base64.b64encode(icon_path.read_bytes()).decode("ascii") if icon_path.is_file() else ""

    api_versions = manifest.get("api_version") or {}
    if isinstance(api_versions, dict) and api_versions:
        # 市场这一列是单值字符串；取声明里最高的那个版本
        plugin_api_version = max(api_versions.values())
    else:
        plugin_api_version = str(api_versions or "1.0.0")

    plugin = {
        "plugin_id": PLUGIN_ID,
        "display_name": manifest.get("display_name") or PLUGIN_ID,
        "summary": manifest.get("summary") or manifest.get("description", "")[:120],
        "description": manifest.get("description", ""),
        "icon_png_base64": icon_b64,
        "homepage": manifest.get("homepage") or repo,
        "repository_url": repo,
        "license": manifest.get("license") or "MIT",
        "categories": manifest.get("categories") or ["tool"],
        "tags": manifest.get("tags") or [],
        "maintainers": manifest.get("maintainers") or [manifest.get("author", "")],
        "plugin_dependencies": [],
        "readme_markdown": readme,
    }

    data = package.read_bytes()
    asset_name = package.name
    version_payload = {
        "version": version,
        "release_tag": tag,
        "release_title": f"{tag} — 群日报",
        "release_url": f"{repo}/releases/tag/{tag}",
        "asset_name": asset_name,
        "asset_download_url": f"{repo}/releases/download/{tag}/{asset_name}",
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "file_size": len(data),
        "is_prerelease": False,
        "plugin_api_version": plugin_api_version,
        "min_host_version": manifest.get("min_core_version") or "1.3.0-alpha.0",
        "supported_platforms": SUPPORTED_PLATFORMS,
        "readme_markdown": readme,
    }
    return plugin, version_payload


def call(market: str, path: str, token: str, payload: dict | None = None) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(market + path, data=data, method="POST" if data else "GET")
    request.add_header("Authorization", f"Bearer {token}")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="发布群日报到插件市场")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="市场地址")
    parser.add_argument("--token", default=os.environ.get("MARKET_GITHUB_TOKEN", ""), help="GitHub PAT")
    parser.add_argument("--repo", default="https://github.com/mutoujun080415/group_daily_log")
    parser.add_argument("--tag", default="v2.0.1", help="GitHub Release 的 tag")
    parser.add_argument("--package", default=str(Path(__file__).resolve().parent.parent / "dist" / "group_daily_log-2.0.1.mfp"))
    parser.add_argument("--dry-run", action="store_true", help="只打印将要提交的内容")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    package = Path(args.package)
    if not package.is_file():
        print(f"找不到安装包：{package}", file=sys.stderr)
        return 1

    plugin, version = build_payloads(root, package, args.repo.rstrip("/"), args.tag)

    if args.dry_run:
        preview = dict(plugin)
        preview["icon_png_base64"] = f"<{len(plugin['icon_png_base64'])} 字符>"
        preview["readme_markdown"] = f"<{len(plugin['readme_markdown'])} 字符>"
        print("== 注册插件 POST /api/v1/plugins ==")
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print("\n== 提交版本 POST /api/v1/plugins/%s/versions ==" % PLUGIN_ID)
        print(json.dumps(version, ensure_ascii=False, indent=2))
        return 0

    if not args.token:
        print("缺少 GitHub 令牌：设置 MARKET_GITHUB_TOKEN 或传 --token", file=sys.stderr)
        return 1

    status, text = call(args.market, f"/api/v1/plugins/{PLUGIN_ID}", args.token)
    if status == 200:
        print(f"插件已存在，跳过注册（{status}）")
    else:
        status, text = call(args.market, "/api/v1/plugins", args.token, plugin)
        print(f"注册插件 -> {status} {text[:300]}")
        if status >= 400:
            return 1

    status, text = call(args.market, f"/api/v1/plugins/{PLUGIN_ID}/versions", args.token, version)
    print(f"提交版本 -> {status} {text[:500]}")
    return 0 if status < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
