import logging

from sqlglot import exp, parse_one
from tests.dialects.test_dialect import Validator

logger = logging.getLogger("sqlglot")


class TestFabric(Validator):
    dialect = "fabric"

    def test_datatype(self):
        self.validate_identity("CAST(x AS DATETIME2(7))", "CAST(x AS DATETIME2(6))")
        self.validate_identity("CAST(x AS DATETIMEOFFSET(5))", "CAST(x AS DATETIMEOFFSET(6))")
        self.validate_identity("CAST(x AS DATETIME2)", "CAST(x AS DATETIME2(6))")
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
        )

        # Lowercase should be corrected
        self.validate_identity(
            "SELECT * FROM my_db.information_schema.tables",
            "SELECT * FROM [my_db].[information_schema].[tables]",
        )

        # Missing catalog should trigger a warning and fall back
        with self.assertLogs(logger) as cm:
            self.validate_identity(
                "SELECT * FROM INFORMATION_SCHEMA.TABLES",
                "SELECT * FROM [INFORMATION_SCHEMA].[TABLES]",
            )
            self.assertIn("Cannot generate fully qualified name", cm.output[0])

        # Normal tables should not be affected
        self.validate_identity(
            "SELECT * FROM my_db.dbo.my_table",
            "SELECT * FROM [my_db].[dbo].[my_table]",
        )

    def test_if_not_exists(self):
        # --- SCHEMA ---
        self.validate_identity(
            "CREATE SCHEMA IF NOT EXISTS my_schema",
            "IF NOT EXISTS (SELECT 1 FROM [db].[INFORMATION_SCHEMA].[SCHEMATA] WHERE [SCHEMA_NAME] = 'my_schema') EXEC('CREATE SCHEMA [my_schema]')",
            # We add a catalog to the parsed expression to simulate a qualified schema
            prepare=lambda ast: ast.transform(
                lambda n: exp.to_table("my_schema", db="db")
                if isinstance(n, exp.Identifier)
                else n,
                copy=False,
            ),
        )

        with self.assertLogs(logger) as cm:
            self.validate_identity(
                "CREATE SCHEMA IF NOT EXISTS my_schema", "CREATE SCHEMA [my_schema]"
            )
            self.assertIn("Could not build IF NOT EXISTS check", cm.output[0])

        # --- TABLE ---
        self.validate_identity(
            "CREATE TABLE IF NOT EXISTS my_db.my_schema.my_table (a INT)",
            "IF NOT EXISTS (SELECT 1 FROM [my_db].[INFORMATION_SCHEMA].[TABLES] WHERE [TABLE_NAME] = 'my_table' AND [TABLE_SCHEMA] = 'my_schema') EXEC('CREATE TABLE [my_db].[my_schema].[my_table] (a INT)')",
        )

        with self.assertLogs(logger) as cm:
            self.validate_identity(
                "CREATE TABLE IF NOT EXISTS my_table (a INT)",
                "CREATE TABLE [my_table] (a INT)",
            )
            self.assertIn("Could not build IF NOT EXISTS check", cm.output[0])

        # --- INDEX ---
        self.validate_identity(
            "CREATE INDEX my_index ON my_db.my_schema.my_table (col) IF NOT EXISTS",
            "IF NOT EXISTS (SELECT 1 FROM [my_db].[sys].[indexes] WHERE [object_id] = OBJECT_ID('my_db.my_schema.my_table') AND [name] = 'my_index') EXEC('CREATE INDEX [my_index] ON [my_db].[my_schema].[my_table]([col])')",
        )

        with self.assertLogs(logger) as cm:
            # Missing catalog on the table
            self.validate_identity(
                "CREATE INDEX my_index ON my_schema.my_table (col) IF NOT EXISTS",
                "CREATE INDEX [my_index] ON [my_schema].[my_table]([col])",
            )
            self.assertIn("Could not build IF NOT EXISTS check", cm.output[0])

        # --- Standard CREATE (no IF NOT EXISTS) ---
        self.validate_identity(
            "CREATE TABLE my_db.my_schema.my_table (a INT)",
            "CREATE TABLE [my_db].[my_schema].[my_table] (a INT)",
        )

    def test_fabric(self):
        # Test case-sensitive normalization
        self.validate_identity("SELECT Foo FROM Bar", "SELECT [Foo] FROM [Bar]")

        # Test separator in table_sql
        self.validate_identity(
            "SELECT * FROM MyCatalog.MySchema.MyTable",
            "SELECT * FROM [MyCatalog].[MySchema].[MyTable]",
        )
        ast = parse_one("SELECT * FROM MyCatalog.MySchema.MyTable", read=self.dialect)
        self.assertEqual(
            ast.find(exp.Table).sql(dialect=self.dialect, sep=".."),
            "[MyCatalog]..[MySchema]..[MyTable]",
        )
