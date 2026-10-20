from typing import Any, Dict, Optional
import secrets
import logging

import pandas
from rpy2 import robjects
from rpy2.rinterface_lib.embedded import RRuntimeError
import duckdb
from duckdb import DuckDBPyConnection, DuckDBPyRelation

from countess.core.plugins import DuckdbSimplePlugin
from countess.core.parameters import TextParam, DataTypeChoiceParam
from countess.utils.duckdb import duckdb_dtype_is_boolean, duckdb_dtype_is_integer, duckdb_dtype_is_numeric

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

                def wrapper(df: dict[str, Any]) -> Optional[Dict[str, Any]]:
                    with robjects.conversion.localconverter(robjects.default_converter):
                        for fname, func in functions.items():
                            parameters = {
                                k: df[k]
                                for k in func.__signature__.parameters.keys()
                                if k in df
                            }
                            try:
                                df.update({ fname: schema[fname](func(**parameters)[0]) })
                            except (RRuntimeError, duckdb.InvalidInputException) as exc:
                                df.update({ fname: None })
                                logger.warning("CAUGHT EXCEPTION %s", str(exc))
                        return df

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

class RColumnsPlugin(DuckdbSimplePlugin):
    name = "R Columns Plugin"
    description = "R plugin which converts every column to an R vector and back"
    version = VERSION

    rcode = TextParam("R Code")

    def execute(
        self, ddbc: DuckDBPyConnection, source: DuckDBPyRelation, row_limit: Optional[int] = None
    ) -> DuckDBPyRelation:
        df = source.df()
        table_name = f"t_{id(self)}"

        with robjects.conversion.localconverter(robjects.default_converter):
            with robjects.environments.local_context() as environ:
                for cname, dtype in zip(source.columns, source.dtypes):
                    if duckdb_dtype_is_boolean(dtype):
                        vec = robjects.BoolVector(df[cname])
                    elif duckdb_dtype_is_integer(dtype):
                        vec = robjects.IntVector(df[cname])
                    elif duckdb_dtype_is_numeric(dtype):
                        vec = robjects.FloatVector(df[cname])
                    else:
                        vec = robjects.StrVector(df[cname])
                    environ[cname] = vec
                if self.rcode.value:
                    ret = robjects.r(self.rcode.value)
                    if ret is None:
                        for name, robj in environ.items():
                            if name not in df and not name.startswith("_"):
                                df[name] = robj[0] if len(robj) == 1 else list(robj)
                    elif isinstance(ret, robjects.ListVector):
                        dd = {
                            (f"{name}.{n}" if len(ee) > 1 else name): [ e ]
                            for name, ee in ret.items()
                            for n, e in enumerate(ee)
                        }
                        df = pandas.DataFrame(dd)
                    elif isinstance(ret, robjects.Vector):
                        dd = { 'result': list(ret) }
                        df = pandas.DataFrame(dd)
        ddbc.register(table_name, df)
        return ddbc.table(table_name)
