"""前端指纹资源的缓存策略。"""

import re

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_FINGERPRINT = re.compile(r"-[\w-]{8}\.[\w.]+$", re.ASCII)


class FrontendAssets(StaticFiles):
    """指纹资源可长期复用，未带指纹的文件每次重新验证。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 206, 304):
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if _FINGERPRINT.search(path)
                else "no-cache"
            )
        return response
