"""启用终端交互并关闭 WebUI，其他频道仍按配置启动。"""

from __future__ import annotations

import sys

from launch import main

if __name__ == "__main__":
    main(["--cli", "--no-webui", *sys.argv[1:]])
