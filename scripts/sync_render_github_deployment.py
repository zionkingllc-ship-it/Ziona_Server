#!/usr/bin/env python3
"""Mirror Render web deploy results back into GitHub Deployments."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib import error, parse, request

GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
RENDER_API_URL = os.environ.get("RENDER_API_URL", "https://api.render.com/v1")
REQUIRED_WORKFLOWS = {"Ziona CI/CD", "Ziona Vulnerability Scanner"}
SUCCESS_STATES = {"live", "active", "deployed", "succeeded", "success", "completed"}


def validate_api_url(url: str) -> None:
    """Ensure automation only calls the configured GitHub/Render API origins."""
    parsed = parse.urlparse(url)
    allowed_hosts = {
        parse.urlparse(GITHUB_API_URL).hostname,
        parse.urlparse(RENDER_API_URL).hostname,
    }
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise RuntimeError(f"Refusing to call untrusted API URL: {url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--environment-url", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--poll-interval", type=int, default=15)
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def read_json_response(response: Any) -> Any:
    payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else None


def api_request(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    request_headers = {"Authorization": f"Bearer {token}"}
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    validate_api_url(url)
    req = request.Request(url, data=data, method=method, headers=request_headers)  # noqa: S310
    try:
        with request.urlopen(req, timeout=30) as response:  # noqa: S310  # nosec B310
            return read_json_response(response)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {body}") from exc


def github_request(
    path: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    return api_request(
        f"{GITHUB_API_URL}{path}",
        token=token,
        method=method,
        payload=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def render_request(path: str, *, token: str) -> Any:
    return api_request(
        f"{RENDER_API_URL}{path}",
        token=token,
        headers={"Accept": "application/json"},
    )


def load_event_payload(path: str | None) -> dict[str, Any]:
    if not path:
        raise RuntimeError("GITHUB_EVENT_PATH is not available")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def should_sync_current_run(
    *,
    repository: str,
    sha: str,
    branch: str,
    current_run_id: int,
    github_token: str,
) -> tuple[bool, str]:
    query = parse.urlencode({"head_sha": sha, "event": "push", "per_page": 100})
    payload = github_request(
        f"/repos/{repository}/actions/runs?{query}",
        token=github_token,
    )
    workflow_runs = payload.get("workflow_runs", [])
    relevant_runs = [
        run
        for run in workflow_runs
        if run.get("head_branch") == branch and run.get("name") in REQUIRED_WORKFLOWS
    ]
    run_by_name = {run["name"]: run for run in relevant_runs}

    missing = REQUIRED_WORKFLOWS.difference(run_by_name)
    if missing:
        return False, f"Waiting for workflow runs to appear: {', '.join(sorted(missing))}"

    not_ready = [
        run["name"]
        for run in run_by_name.values()
        if run.get("status") != "completed" or run.get("conclusion") != "success"
    ]
    if not_ready:
        return False, f"Waiting for successful workflow completion: {', '.join(sorted(not_ready))}"

    latest_run = max(
        run_by_name.values(),
        key=lambda run: (parse_timestamp(run.get("updated_at")), run.get("id", 0)),
    )
    if latest_run.get("id") != current_run_id:
        return False, f"Current workflow run is not the final successful gatekeeper for {sha[:7]}"

    return True, "All required workflows succeeded; syncing Render deployment back to GitHub"


def create_deployment(
    *,
    repository: str,
    ref: str,
    sha: str,
    environment: str,
    github_token: str,
) -> int:
    payload = github_request(
        f"/repos/{repository}/deployments",
        token=github_token,
        method="POST",
        payload={
            "ref": ref,
            "environment": environment,
            "description": f"Waiting for Render deploy of {sha[:7]}",
            "auto_merge": False,
            "required_contexts": [],
        },
    )
    deployment_id = payload.get("id")
    if not deployment_id:
        raise RuntimeError("GitHub deployment creation did not return an id")
    return int(deployment_id)


def create_deployment_status(
    *,
    repository: str,
    deployment_id: int,
    state: str,
    environment: str,
    environment_url: str,
    description: str,
    github_token: str,
) -> None:
    github_request(
        f"/repos/{repository}/deployments/{deployment_id}/statuses",
        token=github_token,
        method="POST",
        payload={
            "state": state,
            "environment": environment,
            "environment_url": environment_url,
            "description": description,
            "auto_inactive": False,
        },
    )


def flatten_deploys(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "deploys", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def collect_commit_ids(deploy: dict[str, Any]) -> set[str]:
    values: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in {"id", "sha", "commitid"} and isinstance(value, str):
                    values.add(value)
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    commit = deploy.get("commit")
    if commit:
        visit(commit)
    for key in ("commitId", "sha"):
        value = deploy.get(key)
        if isinstance(value, str):
            values.add(value)
    return {value for value in values if value}


def matches_commit(deploy: dict[str, Any], sha: str) -> bool:
    commit_ids = collect_commit_ids(deploy)
    if not commit_ids:
        return False
    return any(
        sha.startswith(commit_id) or commit_id.startswith(sha[:7]) for commit_id in commit_ids
    )


def find_matching_render_deploy(
    *,
    service_id: str,
    sha: str,
    render_token: str,
) -> dict[str, Any] | None:
    payload = render_request(f"/services/{service_id}/deploys?limit=20", token=render_token)
    deploys = flatten_deploys(payload)
    for deploy in deploys:
        if matches_commit(deploy, sha):
            return deploy
    return None


def normalize_status(deploy: dict[str, Any]) -> str:
    for key in ("status", "deployStatus", "state"):
        value = deploy.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return "unknown"


def is_success_status(status: str) -> bool:
    return status in SUCCESS_STATES


def is_failure_status(status: str) -> bool:
    return any(token in status for token in ("fail", "error", "cancel"))


def wait_for_render_deploy(
    *,
    service_id: str,
    sha: str,
    render_token: str,
    timeout: int,
    poll_interval: int,
) -> tuple[dict[str, Any] | None, str]:
    deadline = time.time() + timeout
    latest_status = "waiting_for_render_deploy"
    while time.time() < deadline:
        deploy = find_matching_render_deploy(
            service_id=service_id,
            sha=sha,
            render_token=render_token,
        )
        if deploy is None:
            time.sleep(poll_interval)
            continue

        latest_status = normalize_status(deploy)
        if is_success_status(latest_status) or is_failure_status(latest_status):
            return deploy, latest_status

        time.sleep(poll_interval)

    return None, latest_status


def main() -> int:
    args = parse_args()
    github_token = require_env("GITHUB_TOKEN")
    render_token = require_env("RENDER_API_KEY")
    repository = require_env("GITHUB_REPOSITORY")

    event_payload = load_event_payload(args.event_path)
    workflow_run = event_payload.get("workflow_run", {})
    current_run_id = int(workflow_run.get("id", 0))
    branch = workflow_run.get("head_branch")

    should_sync, message = should_sync_current_run(
        repository=repository,
        sha=args.sha,
        branch=branch,
        current_run_id=current_run_id,
        github_token=github_token,
    )
    sys.stdout.write(f"{message}\n")
    if not should_sync:
        return 0

    deployment_id = create_deployment(
        repository=repository,
        ref=args.ref,
        sha=args.sha,
        environment=args.environment,
        github_token=github_token,
    )
    create_deployment_status(
        repository=repository,
        deployment_id=deployment_id,
        state="queued",
        environment=args.environment,
        environment_url=args.environment_url,
        description=f"Waiting for Render to deploy {args.sha[:7]}",
        github_token=github_token,
    )

    create_deployment_status(
        repository=repository,
        deployment_id=deployment_id,
        state="in_progress",
        environment=args.environment,
        environment_url=args.environment_url,
        description=f"Tracking Render deploy for {args.sha[:7]}",
        github_token=github_token,
    )

    deploy, status = wait_for_render_deploy(
        service_id=args.service_id,
        sha=args.sha,
        render_token=render_token,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )

    if deploy is None:
        create_deployment_status(
            repository=repository,
            deployment_id=deployment_id,
            state="error",
            environment=args.environment,
            environment_url=args.environment_url,
            description=(
                f"Timed out waiting for Render deploy of {args.sha[:7]} (last status: {status})"
            ),
            github_token=github_token,
        )
        return 1

    if is_success_status(status):
        create_deployment_status(
            repository=repository,
            deployment_id=deployment_id,
            state="success",
            environment=args.environment,
            environment_url=args.environment_url,
            description=f"Render deploy is live for {args.sha[:7]}",
            github_token=github_token,
        )
        return 0

    create_deployment_status(
        repository=repository,
        deployment_id=deployment_id,
        state="failure",
        environment=args.environment,
        environment_url=args.environment_url,
        description=f"Render deploy failed with status: {status}",
        github_token=github_token,
    )
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(1) from exc
