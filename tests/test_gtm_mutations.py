from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.google_analytics_cli.artifact_store import canonical_json
from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.gtm_mutation_service import GtmMutationService, gtm_plan_sha256
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.measurement_policy import plan_content_sha256
from scripts.google_analytics_cli.website_context import _project_evidence


ROOT = Path(__file__).resolve().parents[1]


class FakeAuth:
    def access_token(self, profile_id: str | None = None):
        return profile_id, "access-token-not-serialized", {"identity": {"email": "test@example.invalid"}}


class FakeGtmTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.container = {"path": "accounts/1/containers/2", "containerId": "2", "publicId": "GTM-TEST123", "usageContext": ["web"], "fingerprint": "container-fp"}
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.tags: dict[str, list[dict[str, Any]]] = {}
        self.triggers: dict[str, list[dict[str, Any]]] = {}
        self.variables: dict[str, list[dict[str, Any]]] = {}
        self.status: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[str, Any]] = {}
        self.live: dict[str, Any] = {}
        self.workspace_counter = 3
        self.version_counter = 5
        self.compiler_error = False
        self.timeout_stage: str | None = None
        self.mutate_before_apply = False
        self.container_reads = 0

    def _response(self, data: Any, request_id: str = "request") -> JsonResponse:
        return JsonResponse(200, data, request_id, {})

    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        path = urlparse(url).path.removeprefix("/tagmanager/v2/")
        if method == "GET" and path == "accounts/1/containers/2":
            self.container_reads += 1
            if self.mutate_before_apply and self.container_reads >= 3:
                self.container["fingerprint"] = "changed-fp"
            return self._response(dict(self.container), "container-read")
        if method == "GET" and path == "accounts/1/containers/2/workspaces":
            return self._response({"workspace": [dict(item) for item in self.workspaces.values()]}, "workspace-list")
        if method == "GET" and path == "accounts/1/containers/2/versions/live":
            return self._response(dict(self.live), "live-read")
        if method == "GET" and path in self.workspaces:
            return self._response(dict(self.workspaces[path]), "workspace-read")
        if method == "GET" and path.endswith("/status"):
            workspace = path.removesuffix("/status")
            return self._response(json.loads(json.dumps(self.status.get(workspace, {"workspaceChange": [], "mergeConflict": []}))), "status-read")
        for suffix, store, key in (("/tags", self.tags, "tag"), ("/triggers", self.triggers, "trigger"), ("/variables", self.variables, "variable")):
            if method == "GET" and path.endswith(suffix):
                workspace = path.removesuffix(suffix)
                return self._response({key: json.loads(json.dumps(store.get(workspace, [])))}, f"{key}-list")
        if method == "GET" and path in self.versions:
            return self._response(dict(self.versions[path]), "version-read")
        if method != "POST":
            raise AssertionError(f"Unexpected request: {method} {url}")
        stage = ""
        if path.endswith("/workspaces"):
            stage = "WORKSPACE_CREATE"
        elif path.endswith(":sync"):
            stage = "WORKSPACE_SYNC"
        elif path.endswith("/bulk_update"):
            stage = "ENTITY_BULK_UPDATE"
        elif path.endswith(":quick_preview"):
            stage = "QUICK_PREVIEW"
        elif path.endswith(":create_version"):
            stage = "VERSION_CREATE"
        elif path.endswith(":publish"):
            stage = "PUBLISH"
        if self.timeout_stage == stage:
            raise AdvisorError("AMBIGUOUS_NETWORK_FAILURE", "unknown", 5, details={"ambiguous": True})
        if stage == "WORKSPACE_CREATE":
            workspace = f"accounts/1/containers/2/workspaces/{self.workspace_counter}"
            value = {"path": workspace, "workspaceId": str(self.workspace_counter), "fingerprint": "workspace-fp-1", **kwargs["payload"]}
            self.workspaces[workspace] = value
            self.tags[workspace], self.triggers[workspace], self.variables[workspace] = [], [], []
            self.status[workspace] = {"workspaceChange": [], "mergeConflict": []}
            return self._response(dict(value), "workspace-create")
        workspace = path.split(":", 1)[0].removesuffix("/bulk_update")
        if stage == "WORKSPACE_SYNC":
            return self._response({"syncStatus": {}, "mergeConflict": []}, "workspace-sync")
        if stage == "ENTITY_BULK_UPDATE":
            observed = []
            for index, change in enumerate(kwargs["payload"]["changes"], 10):
                kind = next(key for key in ("variable", "trigger", "tag") if key in change)
                entity = json.loads(json.dumps(change[kind]))
                id_key = {"variable": "variableId", "trigger": "triggerId", "tag": "tagId"}[kind]
                collection = {"variable": "variables", "trigger": "triggers", "tag": "tags"}[kind]
                entity[id_key] = str(index)
                entity["path"] = f"{workspace}/{collection}/{index}"
                entity["fingerprint"] = f"{kind}-fp-{index}"
                target = {"variable": self.variables, "trigger": self.triggers, "tag": self.tags}[kind][workspace]
                target[:] = [item for item in target if item.get("name") != entity.get("name")]
                target.append(entity)
                observed.append({kind: entity, "changeStatus": change["changeStatus"]})
            self.workspaces[workspace]["fingerprint"] = "workspace-fp-2"
            return self._response({"changes": observed}, "bulk-update")
        if stage == "QUICK_PREVIEW":
            version = {"path": "accounts/1/containers/2/versions/0", "fingerprint": "preview-fp", "name": "Quick preview"}
            return self._response({"containerVersion": version, "syncStatus": {}, "compilerError": self.compiler_error}, "quick-preview")
        if stage == "VERSION_CREATE":
            version_path = f"accounts/1/containers/2/versions/{self.version_counter}"
            version = {"path": version_path, "containerVersionId": str(self.version_counter), "fingerprint": "version-fp-5", **kwargs["payload"]}
            self.versions[version_path] = version
            old_workspace = workspace
            new_workspace = "accounts/1/containers/2/workspaces/9"
            self.workspaces.pop(old_workspace, None)
            self.workspaces[new_workspace] = {"path": new_workspace, "workspaceId": "9", "name": "Generated workspace", "description": "", "fingerprint": "workspace-fp-9"}
            self.tags[new_workspace], self.triggers[new_workspace], self.variables[new_workspace] = [], [], []
            self.status[new_workspace] = {"workspaceChange": [], "mergeConflict": []}
            return self._response({"containerVersion": version, "syncStatus": {}, "compilerError": self.compiler_error, "newWorkspacePath": new_workspace}, "version-create")
        if stage == "PUBLISH":
            version_path = path.removesuffix(":publish")
            self.live = dict(self.versions[version_path])
            return self._response({"containerVersion": dict(self.live), "compilerError": self.compiler_error}, "publish")
        raise AssertionError(f"Unexpected request: {method} {url}")


class GtmMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    def service(self, transport: FakeGtmTransport) -> GtmMutationService:
        return GtmMutationService(auth=FakeAuth(), transport=transport, now=lambda: self.now, sleep=lambda _: None, monotonic=lambda: 0.0)

    def approved_measurement(self, path: Path) -> dict[str, Any]:
        plan = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan-v2.json").read_text(encoding="utf-8"))
        plan["status"] = "approved"
        plan["approvedAt"] = "2026-08-19T11:00:00Z"
        plan["events"][0]["collectionOwner"] = "browser-gtm"
        plan["contentSha256"] = plan_content_sha256(plan)
        plan["approvalSha256"] = plan["contentSha256"]
        path.write_text(json.dumps(plan), encoding="utf-8")
        return plan

    def site_context(self, path: Path, project: Path, measurement: dict[str, Any]) -> None:
        value = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "website-context.json").read_text(encoding="utf-8"))
        value["projectRoot"] = str(project)
        value["measurementPlan"] = {"planId": measurement["planId"], "contentSha256": measurement["contentSha256"]}
        value["projectContentSha256"] = _project_evidence(project)[2]
        value["analytics"] = {"publicIds": ["GTM-TEST123"], "directIds": [], "gtmIds": ["GTM-TEST123"], "findings": [], "directLoaderPaths": [], "gtmLoaderPaths": ["index.html"]}
        value["contextSha256"] = ""
        value["contextSha256"] = __import__("hashlib").sha256(canonical_json(value)).hexdigest()
        path.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def request(path: Path, project: Path, stage: str, **extra: Any) -> None:
        value = {"schemaVersion": 1, "artifactType": "gtm-change-request", "projectRoot": str(project), "profileId": "profile-test", "container": "accounts/1/containers/2", "stage": stage, "rationale": f"Test {stage} safely.", **extra}
        path.write_text(json.dumps(value), encoding="utf-8")

    def prepared(self, project: Path, transport: FakeGtmTransport):
        measurement_path, site_path = project / "measurement.json", project / "site-context.json"
        measurement = self.approved_measurement(measurement_path)
        self.site_context(site_path, project, measurement)
        service = self.service(transport)
        context_result = service.context("profile-test", "accounts/1/containers/2", project, measurement_path, site_path)
        return service, Path(context_result["artifact"]["path"]), measurement

    def test_full_lifecycle_uses_six_separate_confirmed_plans(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, transport = Path(temp).resolve(), FakeGtmTransport()
            service, context_path, _ = self.prepared(project, transport)
            request_path = project / "request.json"
            self.request(request_path, project, "WORKSPACE_CREATE", workspaceName="Advisor isolated", workspaceDescription="Approved measurement work")
            created_plan = service.plan(context_path, request_path)
            self.assertEqual(created_plan["plan"]["planSha256"], gtm_plan_sha256(created_plan["plan"]))
            created = service.apply(Path(created_plan["artifact"]["path"]), created_plan["plan"]["planSha256"])
            workspace = created["journal"]["gtmEvidence"]["workspacePath"]
            with self.assertRaises(AdvisorError) as replay:
                service.apply(Path(created_plan["artifact"]["path"]), created_plan["plan"]["planSha256"])
            self.assertEqual(replay.exception.code, "MUTATION_PLAN_REPLAYED")

            self.request(request_path, project, "WORKSPACE_SYNC", workspace=workspace)
            sync_plan = service.plan(context_path, request_path)
            synced = service.apply(Path(sync_plan["artifact"]["path"]), sync_plan["plan"]["planSha256"])
            self.assertTrue(synced["journal"]["gtmEvidence"]["syncClean"])

            entities = [
                {"action": "create", "entityKind": "trigger", "template": "custom-event-trigger", "name": "CE - generate_lead", "settings": {"eventName": "generate_lead"}},
                {"action": "create", "entityKind": "tag", "template": "ga4-event-tag", "name": "GA4 - generate_lead", "settings": {"measurementId": "G-TEST123", "eventName": "generate_lead", "eventParameters": {}, "firingTriggers": ["CE - generate_lead"], "blockingTriggers": [], "consentTypes": ["analytics_storage"]}},
            ]
            self.request(request_path, project, "ENTITY_BULK_UPDATE", workspace=workspace, entities=entities)
            entity_plan = service.plan(context_path, request_path)
            entity_result = service.apply(Path(entity_plan["artifact"]["path"]), entity_plan["plan"]["planSha256"])
            self.assertTrue(entity_result["journal"]["gtmEvidence"]["entitiesVerified"])

            self.request(request_path, project, "QUICK_PREVIEW", workspace=workspace)
            preview_plan = service.plan(context_path, request_path)
            preview = service.apply(Path(preview_plan["artifact"]["path"]), preview_plan["plan"]["planSha256"])
            self.assertTrue(preview["journal"]["gtmEvidence"]["compilerPreviewVerified"])
            self.assertFalse(preview["journal"]["gtmEvidence"]["runtimePreviewVerified"])

            self.request(request_path, project, "VERSION_CREATE", workspace=workspace, evidenceJournal=preview["artifact"]["path"], versionName="Advisor v1", versionNotes="Checked compiler preview")
            version_plan = service.plan(context_path, request_path)
            version_result = service.apply(Path(version_plan["artifact"]["path"]), version_plan["plan"]["planSha256"])
            version = version_result["journal"]["gtmEvidence"]["versionPath"]
            fingerprint = version_result["journal"]["gtmEvidence"]["versionFingerprint"]

            runtime = {"confirmed": True, "observedAt": "2026-08-19T12:00:00Z", "container": "accounts/1/containers/2", "checks": ["Tag Assistant showed one generate_lead event with expected parameters."]}
            self.request(request_path, project, "PUBLISH", version=version, versionFingerprint=fingerprint, evidenceJournal=version_result["artifact"]["path"], runtimeEvidence=runtime)
            publish_plan = service.plan(context_path, request_path)
            published = service.apply(Path(publish_plan["artifact"]["path"]), publish_plan["plan"]["planSha256"])
            self.assertEqual(published["status"], "applied")
            self.assertEqual(published["journal"]["gtmEvidence"]["publishedVersion"], version)
            writes = [call for call in transport.calls if call["method"] == "POST"]
            self.assertEqual([1] * len(writes), [call["max_attempts"] for call in writes])
            self.assertEqual(6, len(writes))

    def test_wrong_hash_and_stale_precondition_do_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, transport = Path(temp).resolve(), FakeGtmTransport()
            service, context_path, _ = self.prepared(project, transport)
            request_path = project / "request.json"
            self.request(request_path, project, "WORKSPACE_CREATE", workspaceName="Advisor isolated")
            planned = service.plan(context_path, request_path)
            before = len([call for call in transport.calls if call["method"] == "POST"])
            with self.assertRaises(AdvisorError) as mismatch:
                service.apply(Path(planned["artifact"]["path"]), "0" * 64)
            self.assertEqual(mismatch.exception.code, "MUTATION_CONFIRMATION_MISMATCH")
            transport.mutate_before_apply = True
            with self.assertRaises(AdvisorError) as stale:
                service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(stale.exception.code, "STALE_PRECONDITION")
            self.assertEqual(before, len([call for call in transport.calls if call["method"] == "POST"]))

    def test_conflicts_and_unsupported_templates_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, transport = Path(temp).resolve(), FakeGtmTransport()
            service, context_path, _ = self.prepared(project, transport)
            workspace = "accounts/1/containers/2/workspaces/3"
            transport.workspaces[workspace] = {"path": workspace, "name": "Existing", "fingerprint": "fp"}
            transport.tags[workspace], transport.triggers[workspace], transport.variables[workspace] = [], [], []
            transport.status[workspace] = {"workspaceChange": [], "mergeConflict": [{"entityInWorkspace": {"tag": {"name": "Conflict"}}}]}
            request_path = project / "request.json"
            self.request(request_path, project, "QUICK_PREVIEW", workspace=workspace)
            with self.assertRaises(AdvisorError) as conflict:
                service.plan(context_path, request_path)
            self.assertEqual(conflict.exception.code, "GTM_MERGE_CONFLICT")
            self.request(request_path, project, "ENTITY_BULK_UPDATE", workspace=workspace, entities=[{"action": "create", "entityKind": "tag", "template": "custom-html", "name": "Unsafe", "settings": {}}])
            with self.assertRaises(AdvisorError) as unsafe:
                service.plan(context_path, request_path)
            self.assertEqual(unsafe.exception.code, "ARTIFACT_VALIDATION_FAILED")

    def test_compiler_error_blocks_version_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, transport = Path(temp).resolve(), FakeGtmTransport()
            service, context_path, _ = self.prepared(project, transport)
            workspace = "accounts/1/containers/2/workspaces/3"
            transport.workspaces[workspace] = {"path": workspace, "name": "Existing", "fingerprint": "fp"}
            transport.tags[workspace], transport.triggers[workspace], transport.variables[workspace] = [], [], []
            transport.status[workspace] = {"workspaceChange": [], "mergeConflict": []}
            transport.compiler_error = True
            request_path = project / "request.json"
            self.request(request_path, project, "QUICK_PREVIEW", workspace=workspace)
            planned = service.plan(context_path, request_path)
            result = service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(result["status"], "ambiguous")
            self.request(request_path, project, "VERSION_CREATE", workspace=workspace, evidenceJournal=result["artifact"]["path"], versionName="Bad")
            with self.assertRaises(AdvisorError) as evidence:
                service.plan(context_path, request_path)
            self.assertEqual(evidence.exception.code, "INVALID_GTM_EVIDENCE")

    def test_timeout_is_ambiguous_and_never_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, transport = Path(temp).resolve(), FakeGtmTransport()
            service, context_path, _ = self.prepared(project, transport)
            request_path = project / "request.json"
            self.request(request_path, project, "WORKSPACE_CREATE", workspaceName="Advisor isolated")
            planned = service.plan(context_path, request_path)
            transport.timeout_stage = "WORKSPACE_CREATE"
            result = service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(result["status"], "ambiguous")
            writes = [call for call in transport.calls if call["method"] == "POST"]
            self.assertEqual(1, len(writes))
            reconciled = service.reconcile(Path(result["artifact"]["path"]))
            self.assertEqual(reconciled["status"], "reconciled_read_only")
            self.assertEqual(1, len([call for call in transport.calls if call["method"] == "POST"]))

    def test_pause_preserves_existing_tag_and_publish_requires_runtime_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, transport = Path(temp).resolve(), FakeGtmTransport()
            service, context_path, _ = self.prepared(project, transport)
            workspace = "accounts/1/containers/2/workspaces/3"
            transport.workspaces[workspace] = {"path": workspace, "name": "Existing", "fingerprint": "fp"}
            existing = {"path": f"{workspace}/tags/20", "tagId": "20", "fingerprint": "tag-fp", "name": "GA4 - old", "type": "gaawe", "parameter": [{"key": "eventName", "type": "template", "value": "generate_lead"}], "firingTriggerId": ["10"], "blockingTriggerId": [], "tagFiringOption": "oncePerEvent", "paused": False, "consentSettings": {"consentStatus": "needed"}}
            transport.tags[workspace], transport.triggers[workspace], transport.variables[workspace] = [existing], [], []
            transport.status[workspace] = {"workspaceChange": [], "mergeConflict": []}
            request_path = project / "request.json"
            entities = [{"action": "pause", "entityKind": "tag", "template": "ga4-event-tag", "name": "GA4 - old", "entityPath": existing["path"], "fingerprint": "tag-fp", "settings": {}}]
            self.request(request_path, project, "ENTITY_BULK_UPDATE", workspace=workspace, entities=entities)
            planned = service.plan(context_path, request_path)
            payload_tag = planned["plan"]["operations"][0]["body"]["changes"][0]["tag"]
            self.assertEqual(payload_tag["parameter"], existing["parameter"])
            self.assertTrue(payload_tag["paused"])
            result = service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(result["status"], "applied")

            self.request(request_path, project, "PUBLISH", version="accounts/1/containers/2/versions/5", versionFingerprint="fp", evidenceJournal=result["artifact"]["path"])
            with self.assertRaises(AdvisorError) as runtime:
                service.plan(context_path, request_path)
            self.assertEqual(runtime.exception.code, "ARTIFACT_VALIDATION_FAILED")

    def test_version_creation_rejects_workspace_changed_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, transport = Path(temp).resolve(), FakeGtmTransport()
            service, context_path, _ = self.prepared(project, transport)
            workspace = "accounts/1/containers/2/workspaces/3"
            transport.workspaces[workspace] = {"path": workspace, "name": "Existing", "fingerprint": "fp-1"}
            transport.tags[workspace], transport.triggers[workspace], transport.variables[workspace] = [], [], []
            transport.status[workspace] = {"workspaceChange": [], "mergeConflict": []}
            request_path = project / "request.json"
            self.request(request_path, project, "QUICK_PREVIEW", workspace=workspace)
            preview_plan = service.plan(context_path, request_path)
            preview = service.apply(Path(preview_plan["artifact"]["path"]), preview_plan["plan"]["planSha256"])
            transport.workspaces[workspace]["fingerprint"] = "fp-2"
            self.request(request_path, project, "VERSION_CREATE", workspace=workspace, evidenceJournal=preview["artifact"]["path"], versionName="Stale")
            with self.assertRaises(AdvisorError) as stale:
                service.plan(context_path, request_path)
            self.assertEqual(stale.exception.code, "STALE_GTM_PREVIEW")


if __name__ == "__main__":
    unittest.main()
