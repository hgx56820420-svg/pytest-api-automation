"""把三个演示服务的 OpenAPI 导出成 JSON，不需要启动服务。

用法（项目根目录下）：
    python scripts/export_openapi.py contracts        # 第一次：建立基线，随 PR 提交
    python scripts/export_openapi.py build/openapi    # CI：导出当前代码的接口，和基线对比
"""

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SERVICES = {
    "mini_shop": "examples.mini_shop.main",
    "library": "examples.library.main",
    "meeting": "examples.meeting.main",
}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/export_openapi.py <output-dir>")
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, module in SERVICES.items():
        spec = importlib.import_module(module).app.openapi()
        path = out_dir / f"{name}.openapi.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
