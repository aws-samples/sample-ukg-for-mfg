"""
Unit tests for incremental yield behavior of discover_s3tables_bucket.

Tests verify that:
- The function yields the correct number and structure of results
  (1 progress + N namespace_results + 1 summary) for multi-namespace buckets.
- Partial failures yield per-namespace results with correct status.
- All-fail scenarios yield a summary with success=False.
- Empty buckets yield a single result (existing behavior preserved).
- Single-namespace buckets yield 3 results (progress + result + summary).
- Catastrophic loop exceptions still produce a partial summary.

All sub-tool interactions are mocked via unittest.mock — no real AWS calls.
"""

import asyncio
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

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

# Now safe to import the module under test
from tools.analyze import discover_s3tables_bucket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _collect_yields(bucket_name="test-bucket"):
    """Run the async generator and collect all yields as parsed JSON."""
    async def _run():
        results = []
        async for chunk in discover_s3tables_bucket(bucket_name=bucket_name):
            results.append(json.loads(str(chunk)))
        return results
    return asyncio.run(_run())


def _filter_yields(yields, *types):
    """Filter yields to only include specified type(s)."""
    return [y for y in yields if y.get("type") in types]


def _success_mocks(mock_list_ns, mock_inspect, mock_analyze,
                   mock_correlate, mock_register, mock_log,
                   namespaces=None):
    """Configure mocks for a fully-successful discovery of given namespaces."""
    if namespaces is None:
        namespaces = ["erp", "mes", "cmms"]

    mock_list_ns.return_value = json.dumps({
        "success": True,
        "namespaces": namespaces,
        "namespace_count": len(namespaces),
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


# ---------------------------------------------------------------------------
# 4.1  Multi-namespace success — validates Property 1
# ---------------------------------------------------------------------------
class TestMultiNamespaceSuccess(unittest.TestCase):
    """Verify 3 successful namespaces yield exactly 5 results with correct types."""

    @patch("tools.analyze._load_phase_data")
    @patch("tools.analyze.remember_discovery")
    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_multi_namespace_success(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
        mock_remember, mock_load_phase,
    ):
        """4.1 — 3 namespaces all succeeding → 5 yields (1 progress + 3 namespace_result + 1 summary)."""
        _success_mocks(mock_list_ns, mock_inspect, mock_analyze,
                       mock_correlate, mock_register, mock_log,
                       namespaces=["erp", "mes", "cmms"])
        mock_load_phase.return_value = {}
        mock_remember.return_value = "stored:documents/learned/discovery/x.md"

        yields = _collect_yields()
        key_yields = _filter_yields(yields, "progress", "namespace_result", "summary")

        # 5 key yields (excluding phase_update)
        self.assertEqual(len(key_yields), 5)

        # Also verify phase_update yields exist
        phase_updates = _filter_yields(yields, "phase_update")
        # 3 namespaces × 6 phases = 18 phase updates
        self.assertEqual(len(phase_updates), 18)

        # Phase 6 (REMEMBER) writes institutional memory once per completed namespace
        self.assertEqual(mock_remember.call_count, 3)

        # First key yield: progress
        self.assertEqual(key_yields[0]["type"], "progress")
        self.assertEqual(key_yields[0]["namespace_count"], 3)
        self.assertEqual(key_yields[0]["namespaces"], ["erp", "mes", "cmms"])

        # Middle 3 key yields: namespace_result
        for i, ns in enumerate(["erp", "mes", "cmms"], start=1):
            self.assertEqual(key_yields[i]["type"], "namespace_result")
            self.assertEqual(key_yields[i]["namespace"], ns)
            self.assertEqual(key_yields[i]["status"], "completed")
            self.assertEqual(key_yields[i]["system_id"], f"{ns}-system")
            self.assertEqual(key_yields[i]["tables"], 5)
            self.assertEqual(key_yields[i]["fields"], 20)
            self.assertEqual(key_yields[i]["concepts_mapped"], 10)
            self.assertEqual(key_yields[i]["equivalences"], 2)
            self.assertEqual(key_yields[i]["progress"], f"{i}/3")

        # Last key yield: summary
        summary = key_yields[4]
        self.assertEqual(summary["type"], "summary")
        self.assertTrue(summary["success"])
        self.assertEqual(summary["namespace_count"], 3)
        self.assertEqual(summary["namespaces_processed"], 3)
        self.assertEqual(summary["namespaces_failed"], 0)
        self.assertEqual(len(summary["results"]), 3)


# ---------------------------------------------------------------------------
# 4.2  Multi-namespace partial failure
# ---------------------------------------------------------------------------
class TestMultiNamespacePartialFailure(unittest.TestCase):
    """Verify middle namespace failing yields correct statuses and summary counts."""

    @patch("tools.analyze._load_phase_data")
    @patch("tools.analyze.remember_discovery")
    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_partial_failure_middle_namespace(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
        mock_remember, mock_load_phase,
    ):
        """4.2 — Middle namespace (mes) fails inspect → 5 yields, 2 completed + 1 failed."""
        mock_load_phase.return_value = {}
        mock_remember.return_value = "stored:x.md"
        mock_list_ns.return_value = json.dumps({
            "success": True,
            "namespaces": ["erp", "mes", "cmms"],
            "namespace_count": 3,
        })

        # Inspect succeeds for erp/cmms, fails for mes
        def fake_inspect(database, catalog, workgroup, output_location):
            if database == "mes":
                return json.dumps({"success": False, "error": "Connection timeout"})
            return json.dumps({"success": True, "table_count": 5})
        mock_inspect.side_effect = fake_inspect

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

        yields = _collect_yields()
        key_yields = _filter_yields(yields, "progress", "namespace_result", "summary")

        # 5 key yields: 1 progress + 3 namespace_result + 1 summary
        self.assertEqual(len(key_yields), 5)

        # Progress
        self.assertEqual(key_yields[0]["type"], "progress")

        # erp: completed
        self.assertEqual(key_yields[1]["type"], "namespace_result")
        self.assertEqual(key_yields[1]["namespace"], "erp")
        self.assertEqual(key_yields[1]["status"], "completed")

        # mes: failed
        self.assertEqual(key_yields[2]["type"], "namespace_result")
        self.assertEqual(key_yields[2]["namespace"], "mes")
        self.assertEqual(key_yields[2]["status"], "failed")
        self.assertIn("Connection timeout", key_yields[2]["error"])

        # cmms: completed
        self.assertEqual(key_yields[3]["type"], "namespace_result")
        self.assertEqual(key_yields[3]["namespace"], "cmms")
        self.assertEqual(key_yields[3]["status"], "completed")

        # Summary
        summary = key_yields[4]
        self.assertEqual(summary["type"], "summary")
        self.assertFalse(summary["success"])
        self.assertEqual(summary["namespaces_processed"], 2)
        self.assertEqual(summary["namespaces_failed"], 1)


# ---------------------------------------------------------------------------
# 4.3  All namespaces fail
# ---------------------------------------------------------------------------
class TestAllNamespacesFail(unittest.TestCase):
    """Verify all namespaces failing yields correct failed statuses and summary."""

    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_all_namespaces_fail(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
    ):
        """4.3 — All 3 namespaces fail inspect → 5 yields, all failed, success=False."""
        mock_list_ns.return_value = json.dumps({
            "success": True,
            "namespaces": ["erp", "mes", "cmms"],
            "namespace_count": 3,
        })

        mock_inspect.return_value = json.dumps({
            "success": False, "error": "Connection refused",
        })

        mock_log.return_value = json.dumps({"success": True})

        yields = _collect_yields()
        key_yields = _filter_yields(yields, "progress", "namespace_result", "summary")

        # 5 key yields: 1 progress + 3 namespace_result + 1 summary
        self.assertEqual(len(key_yields), 5)

        # Progress
        self.assertEqual(key_yields[0]["type"], "progress")

        # All 3 namespace_results should be failed
        for i in range(1, 4):
            self.assertEqual(key_yields[i]["type"], "namespace_result")
            self.assertEqual(key_yields[i]["status"], "failed")
            self.assertIn("Connection refused", key_yields[i]["error"])

        # Summary
        summary = key_yields[4]
        self.assertEqual(summary["type"], "summary")
        self.assertFalse(summary["success"])
        self.assertEqual(summary["namespaces_processed"], 0)
        self.assertEqual(summary["namespaces_failed"], 3)


# ---------------------------------------------------------------------------
# 4.4  Empty bucket
# ---------------------------------------------------------------------------
class TestEmptyBucket(unittest.TestCase):
    """Verify empty bucket yields a single result with namespace_count=0."""

    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_empty_bucket(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
    ):
        """4.4 — 0 namespaces → single yield with namespace_count=0."""
        mock_list_ns.return_value = json.dumps({
            "success": True,
            "namespaces": [],
            "namespace_count": 0,
        })

        yields = _collect_yields()

        # Single yield
        self.assertEqual(len(yields), 1)
        self.assertTrue(yields[0]["success"])
        self.assertEqual(yields[0]["namespace_count"], 0)
        self.assertEqual(yields[0]["namespaces_processed"], 0)

        # No sub-tools should have been called
        mock_inspect.assert_not_called()
        mock_analyze.assert_not_called()


# ---------------------------------------------------------------------------
# 4.5  Single namespace
# ---------------------------------------------------------------------------
class TestSingleNamespace(unittest.TestCase):
    """Verify single namespace yields 3 results (progress + result + summary)."""

    @patch("tools.analyze._load_phase_data")
    @patch("tools.analyze.remember_discovery")
    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_single_namespace(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
        mock_remember, mock_load_phase,
    ):
        """4.5 — 1 namespace succeeding → 3 yields (1 progress + 1 namespace_result + 1 summary)."""
        _success_mocks(mock_list_ns, mock_inspect, mock_analyze,
                       mock_correlate, mock_register, mock_log,
                       namespaces=["erp"])
        mock_load_phase.return_value = {}
        mock_remember.return_value = "stored:documents/learned/discovery/erp.md"

        yields = _collect_yields()
        key_yields = _filter_yields(yields, "progress", "namespace_result", "summary")

        # 3 key yields
        self.assertEqual(len(key_yields), 3)

        # Verify phase_update yields (1 namespace × 6 phases)
        phase_updates = _filter_yields(yields, "phase_update")
        self.assertEqual(len(phase_updates), 6)

        # Progress
        self.assertEqual(key_yields[0]["type"], "progress")
        self.assertEqual(key_yields[0]["namespace_count"], 1)
        self.assertEqual(key_yields[0]["namespaces"], ["erp"])

        # Namespace result
        self.assertEqual(key_yields[1]["type"], "namespace_result")
        self.assertEqual(key_yields[1]["namespace"], "erp")
        self.assertEqual(key_yields[1]["status"], "completed")
        self.assertEqual(key_yields[1]["system_id"], "erp-system")
        self.assertEqual(key_yields[1]["tables"], 5)
        self.assertEqual(key_yields[1]["fields"], 20)
        self.assertEqual(key_yields[1]["concepts_mapped"], 10)
        self.assertEqual(key_yields[1]["equivalences"], 2)
        self.assertEqual(key_yields[1]["progress"], "1/1")

        # Summary
        summary = key_yields[2]
        self.assertEqual(summary["type"], "summary")
        self.assertTrue(summary["success"])
        self.assertEqual(summary["namespace_count"], 1)
        self.assertEqual(summary["namespaces_processed"], 1)
        self.assertEqual(summary["namespaces_failed"], 0)
        self.assertEqual(len(summary["results"]), 1)


# ---------------------------------------------------------------------------
# 4.6  Guaranteed final yield on loop exception
# ---------------------------------------------------------------------------
class TestGuaranteedFinalYieldOnLoopException(unittest.TestCase):
    """Verify catastrophic loop exception still produces a partial summary."""

    @patch("time.time")
    @patch("tools.analyze._load_phase_data")
    @patch("tools.analyze.remember_discovery")
    @patch("tools.analyze.log_discovery_session")
    @patch("tools.analyze.register_all")
    @patch("tools.analyze.correlate_fields")
    @patch("tools.analyze.analyze_schema")
    @patch("tools.analyze.inspect_athena_source")
    @patch("tools.analyze.list_s3tables_namespaces")
    def test_catastrophic_loop_exception(
        self, mock_list_ns, mock_inspect, mock_analyze,
        mock_correlate, mock_register, mock_log,
        mock_remember, mock_load_phase, mock_time,
    ):
        """4.6 — Catastrophic exception during 2nd namespace → partial summary yielded.

        The outer try/except catches the exception and falls through to the
        summary-building code, which yields whatever partial results accumulated.
        """
        # Setup: 2 namespaces, first succeeds fully
        _success_mocks(mock_list_ns, mock_inspect, mock_analyze,
                       mock_correlate, mock_register, mock_log,
                       namespaces=["erp", "mes"])
        mock_load_phase.return_value = {}
        mock_remember.return_value = "stored:erp.md"

        # time.time() call sequence for 1 successful namespace:
        #   1: overall_start
        #   2: ns_start (erp)
        #   3: ns_duration in Phase 5 LOG
        #   4: duration in bottom yield
        #   5: ns_start (mes) — RAISE HERE to trigger outer except
        # (Phase 6 REMEMBER adds no time.time() calls.)
        call_count = [0]

        def counting_time():
            call_count[0] += 1
            if call_count[0] == 5:
                raise RuntimeError("Catastrophic failure in loop")
            return 1000.0 + call_count[0]
        mock_time.side_effect = counting_time

        yields = _collect_yields()

        # Should get: 1 progress + 1 namespace_result (erp) + 1 summary = 3 yields
        self.assertGreaterEqual(len(yields), 2, "Should yield at least progress + summary")

        # Find the summary (last yield)
        summary = yields[-1]
        self.assertEqual(summary["type"], "summary")

        # The first namespace completed, so namespaces_processed >= 1
        self.assertGreaterEqual(summary["namespaces_processed"], 1)

        # Summary should reflect partial results
        self.assertIn("results", summary)

        # Verify progress yield exists
        self.assertEqual(yields[0]["type"], "progress")


if __name__ == "__main__":
    unittest.main()
