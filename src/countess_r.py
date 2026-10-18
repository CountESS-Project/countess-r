from typing import Any, Dict, Optional

from countess.core.plugins import DuckdbTransformPlugin

VERSION = '0.0.1'

class RStatementPlugin(DuckdbTransformPlugin):
    name = "R Statement Plugin"
    description = "Run an R statement, row-wise"
    version = VERSION

    def transform(self, data: dict[str, Any]) -> Optional[Dict[str,Any]]:
        return data
