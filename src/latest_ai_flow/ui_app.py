from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import strawberry
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info

from latest_ai_flow.main import kickoff_content_crew

RUNS: dict[str, dict[str, Any]] = {}
RUN_LOCK = threading.Lock()


def _append_log(record: dict[str, Any], message: str) -> None:
    logs = record.setdefault("logs", [])
    logs.append(message)
    if len(logs) > 200:
        record["logs"] = logs[-200:]

WORKFLOWS = [
    {"id": "content", "name": "Content Crew", "description": "Research a topic and produce a report"},
    {"id": "summary", "name": "Summary Only", "description": "Run a lightweight summary-style prompt"},
]


@strawberry.type
class Run:
    id: str
    workflow: str
    topic: str
    status: str
    created_at: str
    completed_at: str | None = None
    result: str | None = None
    error: str | None = None
    logs: list[str] | None = None


@strawberry.type
class Query:
    @strawberry.field
    def runs(self, info: Info) -> list[Run]:
        with RUN_LOCK:
            return [self._to_run(item) for item in RUNS.values()]

    @strawberry.field
    def run(self, info: Info, id: str) -> Run | None:
        with RUN_LOCK:
            record = RUNS.get(id)
            if record is None:
                return None
            return self._to_run(record)

    @staticmethod
    def _to_run(record: dict[str, Any]) -> Run:
        return Run(
            id=record["id"],
            workflow=record["workflow"],
            topic=record["topic"],
            status=record["status"],
            created_at=record["created_at"],
            completed_at=record.get("completed_at"),
            result=record.get("result"),
            error=record.get("error"),
            logs=record.get("logs"),
        )


@strawberry.type
class Mutation:
    @strawberry.mutation
    def start_run(self, info: Info, topic: str, workflow: str = "content") -> Run:
        record = _create_run(topic, workflow)
        return Query._to_run(record)


schema = strawberry.Schema(query=Query, mutation=Mutation)
graphql_app = GraphQLRouter(schema, graphql_ide="graphiql")

app = FastAPI(title="CrewAI GraphQL UI", version="0.1.0")
app.include_router(graphql_app, prefix="/graphql")


class RunRequest(BaseModel):
    topic: str = "eBPF"
    workflow: str = "content"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_ui_html())


@app.post("/api/run")
def api_run(payload: RunRequest) -> dict[str, Any]:
    record = _create_run(payload.topic, payload.workflow)
    return {
        "id": record["id"],
        "workflow": record["workflow"],
        "topic": record["topic"],
        "status": record["status"],
        "created_at": record["created_at"],
    }


@app.get("/api/runs")
def api_runs() -> list[dict[str, Any]]:
    with RUN_LOCK:
        return [
            {
                "id": record["id"],
                "workflow": record["workflow"],
                "topic": record["topic"],
                "status": record["status"],
                "created_at": record["created_at"],
                "completed_at": record.get("completed_at"),
                "result": record.get("result"),
                "error": record.get("error"),
                "logs": record.get("logs", []),
            }
            for record in RUNS.values()
        ]


@app.get("/api/flows")
def api_flows() -> list[dict[str, str]]:
    return [{"id": item["id"], "name": item["name"], "description": item["description"]} for item in WORKFLOWS]


@app.get("/api/runs/{run_id}/logs")
def api_run_logs(run_id: str) -> dict[str, Any]:
    with RUN_LOCK:
        record = RUNS.get(run_id)
        if record is None:
            return {"id": run_id, "logs": []}
        return {"id": record["id"], "logs": record.get("logs", [])}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _create_run(topic: str, workflow: str = "content") -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:8]
    created_at = _now_iso()
    record = {
        "id": run_id,
        "workflow": workflow,
        "topic": topic,
        "status": "queued",
        "created_at": created_at,
        "completed_at": None,
        "result": None,
        "error": None,
        "logs": [],
    }
    with RUN_LOCK:
        RUNS[run_id] = record

    thread = threading.Thread(target=_execute_run, args=(record,), daemon=True)
    thread.start()
    return record


def _execute_run(record: dict[str, Any]) -> None:
    record["status"] = "running"
    _append_log(record, f"Starting workflow '{record.get('workflow', 'content')}' for topic '{record['topic']}'")
    try:
        workflow_id = record.get("workflow", "content")
        if workflow_id == "summary":
            _append_log(record, "Using summary workflow")
            record["result"] = f"Summary workflow for topic: {record['topic']}"
            _append_log(record, "Summary workflow completed")
            record["status"] = "succeeded"
        else:
            _append_log(record, "Invoking CrewAI content workflow")
            result = kickoff_content_crew(inputs={"topic": record["topic"]})
            record["result"] = getattr(result, "raw", str(result))
            _append_log(record, "CrewAI workflow completed")
            record["status"] = "succeeded"
    except Exception as exc:  # pragma: no cover - runtime safety path
        _append_log(record, f"Execution failed: {exc}")
        record["error"] = str(exc)
        record["status"] = "failed"
    finally:
        record["completed_at"] = _now_iso()
        _append_log(record, f"Run finished at {record['completed_at']}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ui_html() -> str:
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>CrewAI GraphQL UI</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 2rem; background: #020617; color: #e2e8f0; }
          .card { background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
          .row { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
          .pill { display: inline-block; padding: 0.25rem 0.6rem; border-radius: 999px; background: #1d4ed8; font-size: 0.8rem; margin-right: 0.5rem; }
          input, select, button { padding: 0.7rem; border-radius: 8px; border: 1px solid #64748b; }
          button { cursor: pointer; background: #2563eb; color: white; border: none; }
          .run-list { display: grid; gap: 0.75rem; }
          .run-item { border: 1px solid #334155; border-radius: 10px; padding: 0.75rem; background: #020617; cursor: pointer; }
          .run-item:hover { border-color: #60a5fa; }
          .log-panel { margin-top: 0.75rem; padding: 0.75rem; border-radius: 8px; background: #020617; border: 1px solid #334155; white-space: pre-wrap; font-family: monospace; font-size: 0.9rem; }
          .status { font-weight: bold; }
          .status.queued { color: #fbbf24; }
          .status.running { color: #60a5fa; }
          .status.succeeded { color: #34d399; }
          .status.failed { color: #f87171; }
          .muted { color: #94a3b8; font-size: 0.9rem; }
        </style>
      </head>
      <body>
        <div class="card">
          <h1>CrewAI workflow UI</h1>
          <p>Select a workflow, enter a topic, and watch newly created runs appear below.</p>
          <div class="row">
            <label>Workflow
              <select id="workflow"></select>
            </label>
            <label>Topic
              <input id="topic" value="eBPF" />
            </label>
            <button id="start-run-button">Run flow</button>
          </div>
          <p><a href="/graphql" target="_blank">Open GraphiQL</a></p>
        </div>
        <div class="card">
          <h2>Latest run</h2>
          <div id="result">Waiting for a run…</div>
        </div>
        <div class="card">
          <div class="row" style="justify-content: space-between; align-items: center;">
            <h2 style="margin: 0;">Run history</h2>
            <button id="clear-runs-button">Clear history</button>
          </div>
          <div id="history" class="run-list"></div>
        </div>
        <script>
          async function startRun() {
            const topic = document.getElementById('topic').value || 'eBPF';
            const workflow = document.getElementById('workflow').value || 'content';
            const response = await fetch('/api/run', {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ topic, workflow })
            });
            const data = await response.json();
            document.getElementById('result').innerHTML = `<div class="pill">${data.workflow}</div><strong>${data.id}</strong><div class="muted">${data.topic}</div><div class="status ${data.status}">${data.status}</div>`;
            await loadRuns();
          }

          async function clearRuns() {
            document.getElementById('history').innerHTML = '<div class="muted">Clearing…</div>';
            document.getElementById('result').innerHTML = '<div class="muted">History cleared.</div>';
          }

          async function populateWorkflowSelect(flows) {
            const workflowSelect = document.getElementById('workflow');
            if (!workflowSelect) return;
            const existingValues = Array.from(workflowSelect.options).map(option => option.value);
            const nextValues = flows.map(flow => flow.id);
            if (existingValues.join(',') !== nextValues.join(',')) {
              workflowSelect.innerHTML = '';
              for (const flow of flows) {
                const option = document.createElement('option');
                option.value = flow.id;
                option.textContent = flow.name;
                workflowSelect.appendChild(option);
              }
            }
            if (!workflowSelect.value && flows.length) {
              workflowSelect.value = flows[0].id;
            }
          }

          async function loadRuns() {
            const [runsResponse, flowsResponse] = await Promise.all([
              fetch('/api/runs'),
              fetch('/api/flows')
            ]);
            const runs = await runsResponse.json();
            const flows = await flowsResponse.json();
            populateWorkflowSelect(flows);
            const history = document.getElementById('history');
            if (!runs.length) {
              history.innerHTML = '<div class="muted">No runs yet.</div>';
              return;
            }
            history.innerHTML = runs.map(run => `
              <div class="run-item" data-run-id="${run.id}">
                <div class="row" style="justify-content: space-between; align-items: center;">
                  <div><strong>${run.id}</strong> <span class="pill">${run.workflow}</span></div>
                  <span class="status ${run.status}">${run.status}</span>
                </div>
                <div class="muted">${run.topic}</div>
                <div class="muted">Created: ${run.created_at}</div>
                ${run.result ? `<div style="margin-top: 0.5rem;">${run.result}</div>` : ''}
                ${run.error ? `<div class="status failed" style="margin-top: 0.5rem;">${run.error}</div>` : ''}
                <div id="logs-${run.id}" class="log-panel" style="display:none"></div>
              </div>
            `).join('');
          }

          async function toggleLogs(runId) {
            const panel = document.getElementById(`logs-${runId}`);
            if (!panel) return;
            if (panel.style.display === 'block') {
              panel.style.display = 'none';
              return;
            }
            panel.style.display = 'block';
            panel.textContent = 'Loading logs…';
            try {
              const response = await fetch(`/api/runs/${runId}/logs`);
              const data = await response.json();
              panel.textContent = (data.logs || []).join('\\n');
            } catch (error) {
              panel.textContent = `Unable to load logs: ${error}`;
            }
          }

          document.getElementById('start-run-button').addEventListener('click', startRun);
          document.getElementById('clear-runs-button').addEventListener('click', clearRuns);

          document.addEventListener('click', (event) => {
            const item = event.target.closest('.run-item');
            if (item) {
              const runId = item.getAttribute('data-run-id');
              if (runId) {
                toggleLogs(runId);
              }
            }
          });

          loadRuns();
          setInterval(loadRuns, 4000);
          window.addEventListener('load', () => {
            fetch('/api/flows')
              .then(response => response.json())
              .then(populateWorkflowSelect)
              .catch(() => {});
          });
        </script>
      </body>
    </html>
    """


def main() -> None:
    uvicorn.run("latest_ai_flow.ui_app:app", host="0.0.0.0", port=8000, reload=False)
