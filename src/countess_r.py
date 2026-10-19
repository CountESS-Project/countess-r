from typing import Any, Dict, Optional, Mapping
import secrets
import logging

from rpy2 import robjects
from rpy2.rinterface_lib.embedded import RRuntimeError
import duckdb
from duckdb import DuckDBPyConnection, DuckDBPyRelation

from countess.core.plugins import DuckdbSimplePlugin
from countess.core.parameters import TextParam, DataTypeChoiceParam

VERSION = '0.0.1'

logger = logging.getLogger(__name__)


class RStatementPlugin(DuckdbSimplePlugin):
    name = "R Statement Plugin"
    description = "Run an R statement, row-wise"
    version = VERSION

    rcode = TextParam("R Code")
    ttype = DataTypeChoiceParam("Default column type")

    def _name_type_rfunc_from_environ(self, environ):
        for name, rfunc in environ.items():
            if name.endswith('__str'):
                yield name[:-5], str, rfunc
            elif name.endswith('__int'):
                yield name[:-5], int, rfunc
            elif name.endswith('__bool'):
                yield name[:-6], bool, rfunc
            elif name.endswith('__num'):
                yield name[:-5], float, rfunc
            else:
                yield name, self.ttype.get_selected_type(), rfunc

    def execute(
        self, ddbc: DuckDBPyConnection, source: DuckDBPyRelation, row_limit: Optional[int] = None
    ) -> DuckDBPyRelation:
        with robjects.conversion.localconverter(robjects.default_converter):
            with robjects.environments.local_context() as environ:
                robjects.r(self.rcode.value)

                schema = dict(zip(source.columns, source.dtypes))
                functions = {}
                for name, ttype, rfunc in self._name_type_rfunc_from_environ(environ):
                    if isinstance(rfunc, robjects.functions.SignatureTranslatedFunction):
                        functions[name] = robjects.functions.wrap_r_function(rfunc, name)
                        schema[name] = ttype
                return_type = duckdb.struct_type(schema)

                def wrapper(data: dict[str, Any]) -> Optional[Dict[str, Any]]:
                    with robjects.conversion.localconverter(robjects.default_converter):
                        for fname, func in functions.items():
                            parameters = {
                                k: data[k]
                                for k in func.__signature__.parameters.keys()
                                if k in data
                            }
                            try:
                                data.update({ fname: schema[fname](func(**parameters)[0]) })
                            except (RRuntimeError, duckdb.InvalidInputException) as exc:
                                data.update({ fname: None })
                                logger.warning("CAUGHT EXCEPTION " + str(exc))
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
