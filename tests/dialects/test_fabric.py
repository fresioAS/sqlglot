import logging

from sqlglot import exp, parse_one
from tests.dialects.test_dialect import Validator

logger = logging.getLogger("sqlglot")


class TestFabric(Validator):
    dialect = "fabric"

    def test_datatype(self):
        # The `identify=True` is not strictly necessary here but is good practice for consistency.
        self.validate_identity("CAST(x AS DATETIME2(7))", "CAST(x AS DATETIME2(6))", identify=True)
        self.validate_identity(
            "CAST(x AS DATETIMEOFFSET(5))", "CAST(x AS DATETIMEOFFSET(6))", identify=True
        )
        self.validate_identity("CAST(x AS DATETIME2)", "CAST(x AS DATETIME2(6))", identify=True)
        self.validate_all(
            "CAST(x AS TIMESTAMP)",
            write={
                "fabric": "CAST(x AS DATETIME2(6))",
                "tsql": "CAST(x AS DATETIME2)",
            },
        )

    def test_information_schema_table(self):
        # Correctly cased and quoted
        self.validate_identity(
            "SELECT * FROM my_db.INFORMATION_SCHEMA.TABLES",
            "SELECT * FROM [my_db].[INFORMATION_SCHEMA].[TABLES]",
            identify=True,
        )

        # Lowercase should still be quoted, but not uppercased as it's part of a string in the AST at this point
        self.validate_identity(
            "SELECT * FROM my_db.information_schema.tables",
            "SELECT * FROM [my_db].[information_schema].[tables]",
            identify=True,
        )

        # Missing catalog should trigger a warning and fall back
        with self.assertLogs(logger) as cm:
            self.validate_identity(
                "SELECT * FROM INFORMATION_SCHEMA.TABLES",
                "SELECT * FROM [INFORMATION_SCHEMA].[TABLES]",
                identify=True,
            )
            self.assertIn("Cannot generate fully qualified name", cm.output[0])

        # Normal tables should not be affected
        self.validate_identity(
            "SELECT * FROM my_db.dbo.my_table",
            "SELECT * FROM [my_db].[dbo].[my_table]",
            identify=True,
        )

    def test_if_not_exists(self):
        # --- SCHEMA ---
        # Test case where `prepare` was used. We now do it manually.
        sql = "CREATE SCHEMA IF NOT EXISTS my_schema"
        ast = self.parse_one(sql)

        # Manually transform the AST to simulate a qualified context
        def qualify_schema(node):
            if isinstance(node, exp.Identifier) and node.name == "my_schema":
                # In the context of CREATE SCHEMA, `this` is the identifier itself.
                # We need to give it a catalog for the check to work.
                node.set("catalog", "db")
                return node
            if isinstance(node, exp.Schema) and node.this.name == "my_schema":
                node.this.set("catalog", "db")
            return node

        ast.transform(qualify_schema, copy=False)
        expected_sql = "IF NOT EXISTS (SELECT 1 FROM [db].[INFORMATION_SCHEMA].[SCHEMATA] WHERE [SCHEMA_NAME] = 'my_schema') EXEC('CREATE SCHEMA [my_schema]')"
        self.assertEqual(ast.sql(dialect=self.dialect, identify=True), expected_sql)

        with self.assertLogs(logger) as cm:
            self.validate_identity(
                "CREATE SCHEMA IF NOT EXISTS my_schema", "CREATE SCHEMA [my_schema]", identify=True
            )
            self.assertIn("Could not build IF NOT EXISTS check", cm.output[0])

        # --- TABLE ---
        self.validate_identity(
            "CREATE TABLE IF NOT EXISTS my_db.my_schema.my_table (a INT)",
            "IF NOT EXISTS (SELECT 1 FROM [my_db].[INFORMATION_SCHEMA].[TABLES] WHERE [TABLE_NAME] = 'my_table' AND [TABLE_SCHEMA] = 'my_schema') EXEC('CREATE TABLE [my_db].[my_schema].[my_table] ([a] INT)')",
            identify=True,
        )

        with self.assertLogs(logger) as cm:
            self.validate_identity(
                "CREATE TABLE IF NOT EXISTS my_table (a INT)",
                "CREATE TABLE [my_table] ([a] INT)",
                identify=True,
            )
            self.assertIn("Could not build IF NOT EXISTS check", cm.output[0])

        # --- INDEX ---
        self.validate_identity(
            "CREATE INDEX my_index ON my_db.my_schema.my_table (col) IF NOT EXISTS",
            "IF NOT EXISTS (SELECT 1 FROM [my_db].[sys].[indexes] WHERE [object_id] = OBJECT_ID('my_db.my_schema.my_table') AND [name] = 'my_index') EXEC('CREATE INDEX [my_index] ON [my_db].[my_schema].[my_table]([col])')",
            identify=True,
        )

        with self.assertLogs(logger) as cm:
            self.validate_identity(
                "CREATE INDEX my_index ON my_schema.my_table (col) IF NOT EXISTS",
                "CREATE INDEX [my_index] ON [my_schema].[my_table]([col])",
                identify=True,
            )
            self.assertIn("Could not build IF NOT EXISTS check", cm.output[0])

        # --- Standard CREATE (no IF NOT EXISTS) ---
        self.validate_identity(
            "CREATE TABLE my_db.my_schema.my_table (a INT)",
            "CREATE TABLE [my_db].[my_schema].[my_table] ([a] INT)",
            identify=True,
        )

    def test_fabric(self):
        # Test case-sensitive normalization by ensuring identifiers are quoted
        self.validate_identity("SELECT Foo FROM Bar", "SELECT [Foo] FROM [Bar]", identify=True)

        # Test separator in table_sql
        self.validate_identity(
            "SELECT * FROM MyCatalog.MySchema.MyTable",
            "SELECT * FROM [MyCatalog].[MySchema].[MyTable]",
            identify=True,
        )
        ast = parse_one("SELECT * FROM MyCatalog.MySchema.MyTable", read=self.dialect)
        self.assertEqual(
            ast.find(exp.Table).sql(dialect=self.dialect, sep="..", identify=True),
            "[MyCatalog]..[MySchema]..[MyTable]",
        )

    def test_type_mappings(self):
        # Test unsupported types are correctly mapped to their alternatives
        self.validate_all("CAST(x AS TINYINT)", write={"fabric": "CAST(x AS SMALLINT)"})
        self.validate_all("CAST(x AS DATETIME)", write={"fabric": "CAST(x AS DATETIME2(6))"})
        self.validate_all("CAST(x AS SMALLDATETIME)", write={"fabric": "CAST(x AS DATETIME2(6))"})
        self.validate_all("CAST(x AS NCHAR)", write={"fabric": "CAST(x AS CHAR)"})
        self.validate_all("CAST(x AS NVARCHAR)", write={"fabric": "CAST(x AS VARCHAR)"})
        self.validate_all("CAST(x AS TEXT)", write={"fabric": "CAST(x AS VARCHAR(MAX))"})
        self.validate_all("CAST(x AS IMAGE)", write={"fabric": "CAST(x AS VARBINARY(MAX))"})
        self.validate_all("CAST(x AS MONEY)", write={"fabric": "CAST(x AS DECIMAL)"})
        self.validate_all(
            "CAST(x AS UNIQUEIDENTIFIER)", write={"fabric": "CAST(x AS VARBINARY(MAX))"}
        )
