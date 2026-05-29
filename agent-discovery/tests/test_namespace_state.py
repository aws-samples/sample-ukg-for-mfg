"""
Unit tests for namespace-scoped state keys in the S3 Tables multi-namespace
discovery fix.

Tests verify that:
- save_state / load_phase_results construct the correct DynamoDB sort keys
  when a namespace is (or is not) provided.
- _save_and_summarize passes the namespace through to save_state.
- analyze_schema, correlate_fields, register_all use scoped keys.
- discover_s3tables_bucket processes multiple namespaces and returns
  consolidated results.

All DynamoDB interactions are mocked via unittest.mock — no real AWS calls.
"""

import asyncio
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: add agent-discovery root to sys.path and stub uninstalled deps
# ---------------------------------------------------------------------------
_AGENT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

# Stub out third-party packages that are not installed locally
_strands_mod = types.ModuleType("strands")
_strands_mod.tool = lambda fn=None, **kw: fn if fn else (lambda f: f)
_strands_mod.Agent = MagicMock
sys.modules.setdefault("strands", _strands_mod)

_strands_models = types.ModuleType("strands.models")
_strands_models.BedrockModel = MagicMock
sys.modules.setdefault("strands.models", _strands_models)

sys.modules.setdefault("boto3", MagicMock())
sys.modules.setdefault("boto3.dynamodb", MagicMock())
sys.modules.setdefault("boto3.dynamodb.conditions", MagicMock())
sys.modules.setdefault("httpx", MagicMock())

# Stub concepts module (imported by tools.analyze)
_concepts_mod = types.ModuleType("concepts")
_concepts_mod.CANONICAL_CONCEPTS = {}
_concepts_mod.get_all_concepts_serializable = lambda: {}
sys.modules.setdefault("concepts", _concepts_mod)

# Stub tools.register (imported by tools.analyze)
_register_mod = types.ModuleType("tools.register")
_register_mod.log_discovery_session = MagicMock(return_value='{"success": true}')
_register_mod._get_dynamodb_client = MagicMock()
_register_mod._get_table_name = MagicMock(return_value="test-registry-table")
_register_mod._build_metadata_item = MagicMock(return_value={})
_register_mod._build_schema_item = MagicMock(return_value={})
_register_mod._build_field_item = MagicMock(return_value={})
_register_mod._build_equivalence_item = MagicMock(return_value={})
_register_mod._batch_write_items = MagicMock(return_value=(["ok"], []))
_register_mod._get_registered_system_tables = MagicMock(return_value=set())
sys.modules.setdefault("tools.register", _register_mod)

# Ensure the env var required by _get_table_name() is always set
os.environ["REGISTRY_TABLE_NAME"] = "test-registry-table"

# Now safe to import the modules under test
from tools.state import save_state, load_phase_results
from tools.inspect import _save_and_summarize
from tools.analyze import (
    _load_inspect_data,
    analyze_schema,
    correlate_fields,
    register_all,
    discover_s3tables_bucket,
)


# ---------------------------------------------------------------------------
# 6.1 & 6.2  save_state SK key construction
# ---------------------------------------------------------------------------
class TestSaveState(unittest.TestCase):
    """Verify save_state builds the correct DynamoDB sort key."""

    @patch("tools.state._get_table_name", return_value="test-registry-table")
    @patch("tools.state._get_client")
    def test_save_state_with_namespace(self, mock_get_client, mock_get_table):
        """6.1 — SK should be PHASE#{phase}#{namespace} when namespace given."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = save_state("inspect", '{"data": "test"}', namespace="erp")

        self.assertTrue(result)
        mock_client.put_item.assert_called_once()
        item = mock_client.put_item.call_args[1]["Item"]
        self.assertEqual(item["SK"]["S"], "PHASE#inspect#erp")
        self.assertEqual(item["PK"]["S"], "DISCOVERY_STATE#current")

    @patch("tools.state._get_table_name", return_value="test-registry-table")
    @patch("tools.state._get_client")
    def test_save_state_without_namespace(self, mock_get_client, mock_get_table):
        """6.2 — SK should be PHASE#{phase} when no namespace given."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = save_state("inspect", '{"data": "test"}')

        self.assertTrue(result)
        item = mock_client.put_item.call_args[1]["Item"]
        self.assertEqual(item["SK"]["S"], "PHASE#inspect")

    @patch("tools.state._get_table_name", return_value="test-registry-table")
    @patch("tools.state._get_client")
    def test_save_state_with_namespace_none_explicit(self, mock_get_client, mock_get_table):
        """6.2 — Explicit namespace=None should behave identically to omitted."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        save_state("understand", '{}', namespace=None)

        item = mock_client.put_item.call_args[1]["Item"]
        self.assertEqual(item["SK"]["S"], "PHASE#understand")


# ---------------------------------------------------------------------------
# 6.3 & 6.4  load_phase_results SK key construction
# ---------------------------------------------------------------------------
class TestLoadPhaseResults(unittest.TestCase):
    """Verify load_phase_results reads from the correct DynamoDB sort key."""

    def _make_ddb_response(self, data_dict):
        """Helper — build a DynamoDB get_item response."""
        return {
            "Item": {
                "PK": {"S": "DISCOVERY_STATE#current"},
                "SK": {"S": "PHASE#inspect"},
                "phase": {"S": "inspect"},
                "data": {"S": json.dumps(data_dict)},
            }
        }

    @patch("tools.state._get_table_name", return_value="test-registry-table")
    @patch("tools.state._get_client")
    def test_load_with_namespace(self, mock_get_client, mock_get_table):
        """6.3 — Should load from SK=PHASE#{phase}#{namespace}."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_item.return_value = self._make_ddb_response(
            {"tables": [{"table_name": "orders", "columns": []}]}
        )

        load_phase_results(phase="inspect", namespace="erp")

        key = mock_client.get_item.call_args[1]["Key"]
        self.assertEqual(key["SK"]["S"], "PHASE#inspect#erp")

    @patch("tools.state._get_table_name", return_value="test-registry-table")
    @patch("tools.state._get_client")
    def test_load_without_namespace(self, mock_get_client, mock_get_table):
        """6.4 — Should load from SK=PHASE#{phase} when no namespace."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_item.return_value = self._make_ddb_response(
            {"tables": [{"table_name": "orders", "columns": []}]}
        )

        load_phase_results(phase="inspect")

        key = mock_client.get_item.call_args[1]["Key"]
        self.assertEqual(key["SK"]["S"], "PHASE#inspect")

    @patch("tools.state._get_table_name", return_value="test-registry-table")
    @patch("tools.state._get_client")
    def test_load_returns_summary_for_inspect(self, mock_get_client, mock_get_table):
        """6.3 — Inspect phase should return a summary with table_count."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_item.return_value = self._make_ddb_response(
            {"tables": [{"table_name": "t1", "columns": [{"column_name": "id"}]}]}
        )

        result_json = load_phase_results(phase="inspect", namespace="erp")
        result = json.loads(result_json)
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("table_count"), 1)


# ---------------------------------------------------------------------------
# 6.5  _save_and_summarize namespace passthrough
# ---------------------------------------------------------------------------
class TestSaveAndSummarize(unittest.TestCase):
    """Verify _save_and_summarize passes namespace through to save_state."""

    @patch("tools.inspect.save_state")
    def test_with_namespace(self, mock_save_state):
        """6.5 — namespace='erp' should be forwarded to save_state."""
        full_result = {"tables": [{"table_name": "t1", "columns": []}]}
        _save_and_summarize(full_result, "s3tables", namespace="erp")

        mock_save_state.assert_called_once()
        args, kwargs = mock_save_state.call_args
        self.assertEqual(args[0], "inspect")
        self.assertEqual(kwargs.get("namespace"), "erp")

    @patch("tools.inspect.save_state")
    def test_without_namespace(self, mock_save_state):
        """6.5 — No namespace should pass namespace=None to save_state."""
        full_result = {"tables": [{"table_name": "t1", "columns": []}]}
        _save_and_summarize(full_result, "s3tables")

        mock_save_state.assert_called_once()
        args, kwargs = mock_save_state.call_args
        self.assertEqual(args[0], "inspect")
        self.assertIsNone(kwargs.get("namespace"))

    @patch("tools.inspect.save_state")
    def test_returns_compact_summary(self, mock_save_state):
        """6.5 — Should return a compact summary JSON, not the full data."""
        full_result = {
            "tables": [
                {"table_name": "orders", "columns": [{"column_name": "id"}, {"column_name": "status"}]},
                {"table_name": "items", "columns": [{"column_name": "item_id"}]},
            ],
        }
        summary_json = _save_and_summarize(full_result, "s3tables", namespace="erp")
        summary = json.loads(summary_json)

        self.assertTrue(summary.get("success"))
        self.assertEqual(summary.get("table_count"), 2)
        self.assertTrue(summary.get("_saved_to_ddb"))


# ---------------------------------------------------------------------------
# 6.6  analyze_schema, correlate_fields, register_all with namespace
# ---------------------------------------------------------------------------
class TestAnalyzeWithNamespace(unittest.TestCase):
    """Verify _load_inspect_data uses namespace-scoped keys."""

    @patch("tools.analyze._get_table_name", return_value="test-registry-table")
    @patch("tools.analyze._get_client")
    def test_load_inspect_data_with_namespace(self, mock_get_client, mock_get_table):
        """6.6 — _load_inspect_data(namespace='erp') → SK=PHASE#inspect#erp."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_item.return_value = {
            "Item": {"data": {"S": json.dumps({"tables": []})}}
        }

        _load_inspect_data(namespace="erp")

        key = mock_client.get_item.call_args[1]["Key"]
        self.assertEqual(key["SK"]["S"], "PHASE#inspect#erp")

    @patch("tools.analyze._get_table_name", return_value="test-registry-table")
    @patch("tools.analyze._get_client")
    def test_load_inspect_data_without_namespace(self, mock_get_client, mock_get_table):
        """6.6 — _load_inspect_data() without namespace → SK=PHASE#inspect."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_item.return_value = {
            "Item": {"data": {"S": json.dumps({"tables": []})}}
        }

        _load_inspect_data()

        key = mock_client.get_item.call_args[1]["Key"]
        self.assertEqual(key["SK"]["S"], "PHASE#inspect")


class TestCorrelateWithNamespace(unittest.TestCase):
    """Verify correlate_fields uses namespace-scoped keys for load and save."""

    @patch("tools.analyze.save_state")
    @patch("tools.analyze._get_table_name", return_value="test-registry-table")
    @patch("tools.analyze._get_client")
    def test_loads_from_scoped_key(self, mock_get_client, mock_get_table, mock_save):
        """6.6 — correlate_fields(namespace='erp') loads from PHASE#understand#erp."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        understand_data = {
            "system_id": "erp-system",
            "fields_by_table": {
                "orders": [{"field_name": "id", "concept_id": "", "concept_confidence": 0.0}]
            },
        }
        mock_client.get_item.return_value = {
            "Item": {"data": {"S": json.dumps(understand_data)}}
        }

        async def _run():
            chunks = []
            async for chunk in correlate_fields(namespace="erp"):
                chunks.append(str(chunk))
            return "".join(chunks)

        asyncio.run(_run())

        # Verify get_item used the scoped SK
        first_key = mock_client.get_item.call_args_list[0][1]["Key"]
        self.assertEqual(first_key["SK"]["S"], "PHASE#understand#erp")

        # Verify save_state was called with namespace="erp" for correlate phase
        mock_save.assert_called_once()
        save_args, save_kwargs = mock_save.call_args
        self.assertEqual(save_args[0], "correlate")
        self.assertEqual(save_kwargs.get("namespace"), "erp")


class TestRegisterAllWithNamespace(unittest.TestCase):
    """Verify register_all loads from namespace-scoped understand/correlate keys."""

    @patch("tools.analyze._get_table_name", return_value="test-registry-table")
    @patch("tools.analyze._get_client")
    def test_loads_scoped_keys(self, mock_get_client, mock_get_table):
        """6.6 — register_all(namespace='erp') loads from PHASE#understand#erp
        and PHASE#correlate#erp."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        understand_data = {
            "system_id": "erp-system",
            "system_name": "ERP",
            "system_type": "ERP",
            "isa95_level": 3,
            "source_type": "s3tables",
            "schemas": [{"table_name": "orders"}],
            "fields_by_table": {
                "orders": [
                    {"field_name": "id", "data_type": "string",
                     "is_key": True, "nullable": False,
                     "concept_id": "", "concept_confidence": 0.0}
                ]
            },
        }
        correlate_data = {"equivalences": []}

        def fake_get_item(**kwargs):
            sk = kwargs["Key"]["SK"]["S"]
            if "understand" in sk:
                return {"Item": {"data": {"S": json.dumps(understand_data)}}}
            elif "correlate" in sk:
                return {"Item": {"data": {"S": json.dumps(correlate_data)}}}
            return {}

        mock_client.get_item.side_effect = fake_get_item

        async def _run():
            chunks = []
            async for chunk in register_all(namespace="erp"):
                chunks.append(str(chunk))
            return "".join(chunks)

        try:
            asyncio.run(_run())
        except Exception:
            pass  # May fail on register internals; we verify DDB calls below

        # Verify get_item was called with both scoped keys
        sk_values = [
            c[1]["Key"]["SK"]["S"]
            for c in mock_client.get_item.call_args_list
            if "Key" in (c[1] if c[1] else {})
        ]
        self.assertIn("PHASE#understand#erp", sk_values)
        self.assertIn("PHASE#correlate#erp", sk_values)


# ---------------------------------------------------------------------------
# 6.7  discover_s3tables_bucket consolidated results
# ---------------------------------------------------------------------------
class TestDiscoverS3TablesBucket(unittest.TestCase):
    """Verify discover_s3tables_bucket processes multiple namespaces and
    returns a consolidated summary."""

    @patch("tools.analyze._load_phase_data")
    @patch("tools.analyze.remember_discovery")
    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_processes_multiple_namespaces(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
        mock_remember, mock_load_phase,
    ):
        """6.7 — Should process 3 namespaces and return namespace_count=3."""
        mock_load_phase.return_value = {}
        mock_remember.return_value = "stored:x.md"
        mock_list_ns.return_value = json.dumps({
            "success": True,
            "namespaces": ["erp", "mes", "cmms"],
            "namespace_count": 3,
        })

        mock_inspect.return_value = json.dumps({
            "success": True, "table_count": 5,
        })

        async def fake_analyze(namespace=None):
            yield json.dumps({
                "success": True,
                "system_id": f"{namespace}-system",
                "system_name": f"{namespace} System",
                "system_type": "ERP",
                "field_count": 20,
                "concepts_mapped": 10,
            })
        mock_analyze.side_effect = fake_analyze

        async def fake_correlate(namespace=None):
            yield json.dumps({"success": True, "equivalence_count": 3})
        mock_correlate.side_effect = fake_correlate

        async def fake_register(namespace=None):
            yield json.dumps({
                "success": True,
                "system_id": f"{namespace}-system",
                "equivalences_registered": 2,
                "equivalences_rejected": 0,
            })
        mock_register.side_effect = fake_register

        mock_log.return_value = json.dumps({"success": True})

        async def _run():
            chunks = []
            async for chunk in discover_s3tables_bucket(
                bucket_name="test-bucket",
                workgroup="primary",
                output_location="s3://test/output/",
            ):
                chunks.append(json.loads(str(chunk)))
            return chunks

        yields = asyncio.run(_run())
        # Last yield is the summary
        result = yields[-1]

        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("namespace_count"), 3)
        self.assertEqual(result.get("namespaces_processed"), 3)
        self.assertEqual(len(result.get("results", [])), 3)

        ns_names = [r["namespace"] for r in result["results"]]
        self.assertEqual(ns_names, ["erp", "mes", "cmms"])
        self.assertEqual(mock_inspect.call_count, 3)
        self.assertEqual(mock_log.call_count, 3)

    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_empty_bucket_returns_zero(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
    ):
        """6.7 — Empty bucket should return namespace_count=0."""
        mock_list_ns.return_value = json.dumps({
            "success": True, "namespaces": [], "namespace_count": 0,
        })

        async def _run():
            chunks = []
            async for chunk in discover_s3tables_bucket(bucket_name="empty-bucket"):
                chunks.append(json.loads(str(chunk)))
            return chunks

        yields = asyncio.run(_run())
        result = yields[0]

        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("namespace_count"), 0)
        self.assertEqual(result.get("namespaces_processed"), 0)
        mock_inspect.assert_not_called()
        mock_analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
