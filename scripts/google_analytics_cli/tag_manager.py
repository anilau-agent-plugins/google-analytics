"""Serialized and bounded read-only Google Tag Manager API client."""

from __future__ import annotations

import time
from typing import Any, Callable

from .pagination import collect_page_tokens
from .read_operation import ReadExecutor


class TagManagerClient:
    def __init__(
        self,
        executor: ReadExecutor,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        minimum_interval: float = 4.1,
    ) -> None:
        self.executor = executor
        self.sleep = sleep
        self.monotonic = monotonic
        self.minimum_interval = minimum_interval
        self._last_call: float | None = None

    def _execute(self, operation: str, **kwargs: Any) -> Any:
        now = self.monotonic()
        if self._last_call is not None:
            wait = self.minimum_interval - (now - self._last_call)
            if wait > 0:
                self.sleep(wait)
        response = self.executor.execute(operation, **kwargs)
        self._last_call = self.monotonic()
        return response

    def _list(self, operation: str, key: str, resource: str | None = None, **extra: Any) -> dict[str, Any]:
        def fetch(token: str | None) -> dict[str, Any]:
            query = {"pageToken": token, **extra}
            return self._execute(operation, resource=resource, query=query).data or {}
        return collect_page_tokens(fetch, key, max_pages=50, max_items=10_000)

    def accounts(self) -> dict[str, Any]:
        return self._list("gtm.accounts.list", "account", includeGoogleTags="true")

    def containers(self, account_path: str) -> dict[str, Any]:
        return self._list("gtm.containers.list", "container", account_path)

    def container_baseline(self, container_path: str) -> dict[str, Any]:
        workspaces = self._list("gtm.workspaces.list", "workspace", container_path)
        selected = workspaces["items"][:5]
        details: list[dict[str, Any]] = []
        for workspace in selected:
            path = workspace.get("path")
            if not isinstance(path, str):
                continue
            details.append({
                "workspace": workspace,
                "status": self._execute("gtm.workspace.status", resource=path).data or {},
                "tags": self._list("gtm.tags.list", "tag", path),
                "triggers": self._list("gtm.triggers.list", "trigger", path),
                "variables": self._list("gtm.variables.list", "variable", path),
                "builtInVariables": self._list("gtm.built_in_variables.list", "builtInVariable", path),
                "googleTagConfigs": self._list("gtm.gtag_config.list", "gtagConfig", path),
            })
        versions = self._list(
            "gtm.version_headers.list", "containerVersionHeader", container_path,
            includeDeleted="false", pageSize=100,
        )
        if len(versions["items"]) > 100:
            versions["items"] = versions["items"][:100]
            versions["truncated"] = True
        return {
            "workspaces": workspaces,
            "workspaceDetails": details,
            "workspaceLimitApplied": len(workspaces["items"]) > 5,
            "liveVersion": self._execute("gtm.live_version.get", resource=container_path).data or {},
            "versionHeaders": versions,
        }
