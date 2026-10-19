from typing import Any, Dict, Optional, Mapping
import inspect
import secrets
import logging

from rpy2 import robjects
import duckdb
from duckdb import DuckDBPyConnection, DuckDBPyRelation

from countess.core.plugins import DuckdbSimplePlugin
from countess.utils.duckdb import duckdb_escape_identifier
from countess.core.parameters import TextParam

VERSION = '0.0.1'

logger = logging.getLogger(__name__)

class RStatementPlugin(DuckdbSimplePlugin):
    name = "R Statement Plugin"
    description = "Run an R statement, row-wise"
    version = VERSION

    rcode = TextParam("R Code")

    def execute(
        self, ddbc: DuckDBPyConnection, source: DuckDBPyRelation, row_limit: Optional[int] = None
    ) -> DuckDBPyRelation:
        with robjects.conversion.localconverter(robjects.default_converter):
            with robjects.environments.local_context() as environ:
                robjects.r(self.rcode.value)
                functions = {
                    name: robjects.functions.wrap_r_function(rfunc, name)
                    for name, rfunc in environ.items()
                    if isinstance(rfunc, robjects.functions.SignatureTranslatedFunction)
                }
    
                schema = dict(zip(source.columns, source.dtypes))
                schema.update({k: str for k in functions.keys()})
                return_type = duckdb.struct_type(schema)
    
                def wrapper(data: dict[str, Any]) -> Optional[Dict[str, Any]]:
                    with robjects.conversion.localconverter(robjects.default_converter):
                        for fname, func in functions.items():
                            try:
                                data.update({ fname: func(**{
                                    k: data[k] for k in func.__signature__.parameters.keys()
                                })[0] })
                            except Exception as exc:
                                data.update({ fname: None })
                                logger.warning(exc)
                        return data
    
                function_name = "f_" + secrets.token_hex(16)
                ddbc.create_function( # type: ignore[call-overload]
                    function_name,
                    wrapper,
                    return_type=return_type,
                    null_handling="special",
                )
                sql = f"SELECT UNNEST({function_name}(_ROW)) FROM (SELECT * FROM {source.alias}) AS _ROW"
                logger.debug(sql)
                return ddbc.sql(sql)
