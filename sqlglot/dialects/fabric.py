from __future__ import annotations

import logging
import typing as t

from sqlglot import exp
from sqlglot.dialects.tsql import TSQL
from sqlglot.dialects.dialect import NormalizationStrategy

logger = logging.getLogger("sqlglot")


class Fabric(TSQL):
    """
    Microsoft Fabric Warehouse Dialect.

    Inherits from TSQL and enforces correct casing and database qualification
    for INFORMATION_SCHEMA system views, which is a common requirement
    in Fabric Warehouses.
    """

    NORMALIZATION_STRATEGY = NormalizationStrategy.CASE_SENSITIVE

    class Generator(TSQL.Generator):
        def datatype_sql(self, expression: exp.DataType) -> str:
            """
            Forces a precision of 6 for temporal types, as Fabric does not
            support the default T-SQL precision of 7 in CAST statements.
            """
            type_sql = super().datatype_sql(expression)

            if type_sql.upper() in ("DATETIME2", "DATETIMEOFFSET"):
                if "(" in type_sql:
                    return f"{type_sql.split('(')[0]}(6)"
                return f"{type_sql}(6)"

            return type_sql

        def table_sql(self, expression: exp.Table, sep: str = "") -> str:
            """Handles special quoting and casing for INFORMATION_SCHEMA views."""
            if isinstance(expression.db, exp.Identifier) and isinstance(
                expression.this, exp.Identifier
            ):
                db_name = expression.db.name
                table_name = expression.this.name

                if db_name.upper() == "INFORMATION_SCHEMA" and table_name.upper() in (
                    "TABLES",
                    "SCHEMATA",
                    "COLUMNS",
                ):
                    if not expression.catalog:
                        logger.warning(
                            f"Cannot generate fully qualified name for '{expression.sql(dialect='tsql')}' in Fabric dialect: "
                            "Database (catalog) name is missing."
                        )
                        return super().table_sql(expression, sep=sep)

                    catalog_sql = self.sql(expression.catalog)
                    schema_sql = self.quote("INFORMATION_SCHEMA")
                    table_sql = self.quote(table_name.upper())
                    return f"{catalog_sql}{sep}{schema_sql}{sep}{table_sql}"

            return super().table_sql(expression, sep=sep)

        def _create_if_not_exists_check(self, expression: exp.Create) -> t.Optional[exp.Select]:
            """Builds the SELECT query used to check for an object's existence."""
            kind = (expression.kind or "").upper()
            target = expression.this

            catalog = getattr(target, "catalog", None)
            if not isinstance(catalog, exp.Identifier):
                return None
            catalog_name = catalog.name

            if kind == "SCHEMA" and isinstance(target, exp.Identifier):
                return (
                    exp.select("1")
                    .from_(exp.table_("SCHEMATA", db="INFORMATION_SCHEMA", catalog=catalog_name))
                    .where(exp.column("SCHEMA_NAME").eq(target.name))
                )

            table = getattr(target, "this", None)
            if kind == "TABLE" and isinstance(table, exp.Identifier):
                db = getattr(target, "db", None)
                where_clauses = [exp.column("TABLE_NAME").eq(table.name)]
                if isinstance(db, exp.Identifier):
                    where_clauses.append(exp.column("TABLE_SCHEMA").eq(db.name))

                return (
                    exp.select("1")
                    .from_(exp.table_("TABLES", db="INFORMATION_SCHEMA", catalog=catalog_name))
                    .where(exp.and_(*where_clauses))
                )

            if kind == "INDEX" and isinstance(target, exp.Identifier):
                on_table = expression.find(exp.Table)
                if not (on_table and isinstance(on_table.this, exp.Identifier)):
                    return None

                table_catalog = on_table.catalog or catalog
                if not isinstance(table_catalog, exp.Identifier):
                    return None

                table_db = on_table.db
                schema_name = table_db.name if isinstance(table_db, exp.Identifier) else "dbo"
                qualified_name = f"{table_catalog.name}.{schema_name}.{on_table.this.name}"

                object_id_func = exp.func("OBJECT_ID", exp.Literal.string(qualified_name))

                return (
                    exp.select("1")
                    .from_(exp.table_("indexes", db="sys", catalog=table_catalog.name))
                    .where(
                        exp.and_(
                            exp.column("object_id").eq(object_id_func),
                            exp.column("name").eq(target.name),
                        )
                    )
                )
            return None

        def create_sql(self, expression: exp.Create) -> str:
            if not expression.args.get("exists"):
                return super().create_sql(expression)

            # Fabric doesn't support IF NOT EXISTS
            create_expression = expression.copy()
            create_expression.args.pop("exists")

            check_sql = self._create_if_not_exists_check(create_expression)
            if not check_sql:
                kind = (create_expression.kind or "OBJECT").upper()
                target_sql = create_expression.this.sql(dialect="tsql")
                logger.warning(
                    f"Could not build IF NOT EXISTS check for {kind} {target_sql}. This can happen if the "
                    "database/schema/table is an unresolved macro or is missing. Falling back to a standard "
                    "CREATE statement, which may fail if the object already exists."
                )
                return super().create_sql(create_expression)

            core_create_sql = super().create_sql(create_expression)
            core_create_sql_literal = exp.Literal.string(core_create_sql)

            return (
                f"IF NOT EXISTS ({self.sql(check_sql)}) EXEC({self.sql(core_create_sql_literal)})"
            )
