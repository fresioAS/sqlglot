from __future__ import annotations

import logging
import typing as t

from sqlglot import exp, generator
from sqlglot.dialects.tsql import TSQL  
from sqlglot.dialects.dialect import NormalizationStrategy
logger = logging.getLogger(__name__)


class FABRIC(TSQL):
    """
    Microsoft Fabric Warehouse Dialect.

    Inherits from TSQL and enforces correct casing and database qualification
    for INFORMATION_SCHEMA system views, which is a common requirement
    in Fabric Warehouses. SQLMesh will load this dialect from the project path.
    """

    DIALECT = "fabric"
    NORMALIZATION_STRATEGY = NormalizationStrategy.CASE_SENSITIVE
    class Generator(TSQL.Generator):
        def table_sql(self, expression: exp.Table) -> str:
            catalog = expression.catalog
            schema = expression.db
            table = expression.this

            schema_name_str = None
            if schema is not None:
                try:
                    schema_name_str = exp.to_identifier(schema).name
                except Exception as e:
                     logger.warning(f"Could not get name from schema part '{schema}' (type: {type(schema)}) in table '{expression}'. Error: {e}")


            if (
                schema_name_str is not None
                and schema_name_str.upper() == "INFORMATION_SCHEMA"
                and table
                and table.name.upper() in ("TABLES", "SCHEMATA", "COLUMNS")
            ):
                if catalog:
                    catalog_sql = self.sql(catalog)
                    schema_sql = self.dialect.quote_identifier("INFORMATION_SCHEMA")
                    table_sql = self.dialect.quote_identifier(table.name.upper())

                    return f"{catalog_sql}.{schema_sql}.{table_sql}"
                else:
                    logger.error(
                        f"Cannot generate fully qualified name for '{expression.sql(dialect='tsql')}' in Fabric dialect: Database (catalog) name is missing."
                    )


            return super().table_sql(expression)

        def create_sql(self, expression: exp.Create) -> str:
            exists = expression.args.pop("exists", None)

            target = expression.this
            kind = expression.kind.upper()

            table_name_exp = target.this if isinstance(target, exp.Schema) else target

            database_name_exp = None
            if hasattr(target, 'catalog'):
                 database_name_exp = target.catalog


            core_create_sql = super().create_sql(expression)

            if exists:
                identifier_literal_sql = None
                check_sql_expression = None
                database_name_str = None

                if database_name_exp:
                     database_name_str = exp.to_identifier(database_name_exp).name


                if not database_name_str:
                     target_name_for_log = target.sql(dialect='fabric') if target else 'unknown'
                     logger.error(f"Expression for {kind} {target_name_for_log} is missing catalog information required for Fabric-specific IF NOT EXISTS check. The adapter/calling code must ensure the catalog is set on the expression.")
                     return core_create_sql


                if kind == "SCHEMA":
                    schema_name_exp = target
                    schema_name_str = None
                    if schema_name_exp.db:
                         schema_name_str = exp.to_identifier(schema_name_exp.db).name


                    if schema_name_str:
                        from_table = exp.Table(
                            this=exp.to_identifier("SCHEMATA", quoted=True),
                            db=exp.to_identifier("INFORMATION_SCHEMA", quoted=True),
                            catalog=exp.to_identifier(database_name_str)
                        )
                        check_sql_expression = (
                            exp.select("1")
                            .from_(from_table)
                            .where(exp.column("SCHEMA_NAME").eq(self.sql(exp.Literal.string(schema_name_str.strip('[]')), quotes=False)))
                        )
                        identifier_literal_sql = self.sql(exp.Literal.string(schema_name_str))

                elif kind == "TABLE":
                    schema_name_str = None
                    if table_name_exp.db:
                         schema_name_str = exp.to_identifier(table_name_exp.db).name

                    table_name_str = None
                    if table_name_exp.this and table_name_exp.this.name:
                         table_name_str = exp.to_identifier(table_name_exp.this).name


                    if table_name_str:
                        from_table = exp.Table(
                            this=exp.to_identifier("TABLES", quoted=True),
                            db=exp.to_identifier("INFORMATION_SCHEMA", quoted=True),
                            catalog=exp.to_identifier(database_name_str)
                        )
                        where_clauses = [exp.column("TABLE_NAME").eq(self.sql(exp.Literal.string(table_name_str.strip('[]')), quotes=False))]
                        if schema_name_str:
                             where_clauses.append(exp.column("TABLE_SCHEMA").eq(self.sql(exp.Literal.string(schema_name_str.strip('[]')), quotes=False)))


                        check_sql_expression = (
                            exp.select("1")
                            .from_(from_table)
                            .where(exp.and_(*where_clauses) if len(where_clauses) > 1 else where_clauses[0])
                        )
                        qualified_name_parts = [self.dialect.quote_identifier(database_name_str)]
                        if schema_name_str:
                             qualified_name_parts.append(self.dialect.quote_identifier(schema_name_str))
                        qualified_name_parts.append(self.dialect.quote_identifier(table_name_str))
                        identifier_literal_sql = self.sql(exp.Literal.string(".".join(qualified_name_parts)))


                elif kind == "INDEX":
                     index_table_exp = expression.find(exp.Table)

                     index_name = expression.this.text("this")

                     table_schema_str = None
                     table_name_str = None

                     if index_table_exp and index_table_exp.db:
                         table_schema_str = exp.to_identifier(index_table_exp.db).name
                     if index_table_exp and index_table_exp.this and index_table_exp.this.name:
                         table_name_str = exp.to_identifier(index_table_exp.this).name


                     if table_name_str and index_name:
                         from_table = exp.Table(
                            this=exp.to_identifier("indexes", quoted=True),
                            db=exp.to_identifier("sys", quoted=True),
                            catalog=exp.to_identifier(database_name_str)
                         )
                         qualified_table_name_parts = [self.dialect.quote_identifier(database_name_str)]
                         if table_schema_str:
                             qualified_table_name_parts.append(self.dialect.quote_identifier(table_schema_str))
                         qualified_table_name_parts.append(self.dialect.quote_identifier(table_name_str))
                         qualified_table_str = ".".join(qualified_table_name_parts)

                         object_id_func = self.func("object_id", exp.Literal.string(qualified_table_str))

                         check_sql_expression = (
                            exp.select("1")
                            .from_(from_table)
                            .where(exp.and_(
                                exp.column("object_id").eq(object_id_func),
                                exp.column("name").eq(index_name)
                            ))
                         )
                         identifier_literal_sql = self.sql(exp.Literal.string(qualified_table_str))


                if identifier_literal_sql is not None and check_sql_expression is not None:
                     check_sql_string = self.sql(check_sql_expression)

                     core_create_sql_literal = self.sql(exp.Literal.string(core_create_sql))

                     return f"""IF NOT EXISTS ({check_sql_string}) EXEC({core_create_sql_literal})"""
                else:
                     target_name_for_log = target.sql(dialect='tsql') if target else 'unknown'
                     logger.error(f"Could not build check SQL for {kind} {target_name_for_log} even with database name. Falling back to core CREATE.")
                     return core_create_sql


            return core_create_sql