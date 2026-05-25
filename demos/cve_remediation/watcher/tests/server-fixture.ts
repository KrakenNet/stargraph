import * as net from "net";
import { spawn, ChildProcess } from "child_process";

let serverProcess: ChildProcess | null = null;
let serverPort: number | null = null;
let baseUrl: string | null = null;

/**
 * Acquire a free port via net.createServer listen(0), then spawn the
 * serve_cve_rem server. Polls GET /v1/runs until 200 (60s timeout).
 */
export async function startServer(): Promise<{ port: number; baseUrl: string }> {
  // Free-port acquisition per D10
  const port = await new Promise<number>((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      if (addr && typeof addr === "object") {
        const p = addr.port;
        srv.close(() => resolve(p));
      } else {
        srv.close(() => reject(new Error("Failed to get port")));
      }
    });
    srv.on("error", reject);
  });

  serverPort = port;
  baseUrl = `http://127.0.0.1:${port}`;

  // Spawn the server
  serverProcess = spawn(
    "uv",
    [
      "run",
      "--no-project",
      "python",
      "-m",
      "demos.cve_remediation.serve_cve_rem",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: process.env.HARBOR_ROOT || process.cwd().replace(/\/demos\/.*/, ""),
      stdio: ["ignore", "pipe", "pipe"],
    }
  );

  serverProcess.stderr?.on("data", (chunk: Buffer) => {
    const line = chunk.toString();
    if (process.env.DEBUG) process.stderr.write(`[server] ${line}`);
  });

  // Poll until ready (60s timeout)
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/v1/runs`);
      if (res.ok) break;
    } catch {
      // not ready yet
    }
    await sleep(500);
  }

  if (Date.now() >= deadline - 500) {
    throw new Error(`Server did not become ready within 60s on port ${port}`);
  }

  return { port, baseUrl };
}

/**
 * Stop the server process (SIGTERM, then SIGKILL after 5s).
 */
export async function stopServer(): Promise<void> {
  if (!serverProcess) return;
  const proc = serverProcess;
  serverProcess = null;

  proc.kill("SIGTERM");
  await Promise.race([
    new Promise<void>((resolve) => proc.on("exit", () => resolve())),
    sleep(5000).then(() => {
      proc.kill("SIGKILL");
    }),
  ]);
}

/**
 * POST a new CVE run. Returns the run_id.
 */
export async function postRun(
  url: string,
  cveId: string
): Promise<string> {
  const res = await fetch(`${url}/v1/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ cve_id: cveId }),
  });
  if (!res.ok) {
    throw new Error(`POST /v1/runs failed: ${res.status} ${await res.text()}`);
  }
  const data = await res.json();
  return data.run_id;
}

/**
 * Background HITL auto-approver. Polls /watch/api/run/{id}/events every 500ms,
 * auto-approves any waiting_for_input events. Stops on result event or manual stop().
 * 60s-per-gate watchdog: if no progress for 60s, logs and force-stops.
 */
export function startHitlResponder(
  url: string,
  runId: string
): { stop: () => Promise<void> } {
  let running = true;
  const seenEvents = new Set<string>();
  let lastProgressTs = Date.now();

  const loop = (async () => {
    while (running) {
      try {
        const res = await fetch(`${url}/watch/api/run/${runId}/events`);
        if (!res.ok) {
          await sleep(500);
          continue;
        }
        const events: Array<{ id?: string; type?: string; event_type?: string }> =
          await res.json();

        for (const evt of events) {
          const evtId = evt.id || `${evt.type || evt.event_type}_${JSON.stringify(evt)}`;
          if (seenEvents.has(evtId)) continue;
          seenEvents.add(evtId);
          lastProgressTs = Date.now();

          const evtType = evt.type || evt.event_type || "";

          if (evtType === "waiting_for_input") {
            try {
              await fetch(`${url}/v1/runs/${runId}/respond`, {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  decision: "approve",
                  actor: "playwright",
                  note: "auto",
                }),
              });
            } catch (e) {
              console.error(`[hitl-responder] respond failed:`, e);
            }
          }

          if (evtType === "result") {
            running = false;
            break;
          }
        }
      } catch {
        // fetch error, retry
      }

      // 60s-per-gate watchdog
      if (Date.now() - lastProgressTs > 60_000) {
        console.error(
          `[hitl-responder] watchdog: no progress for 60s, force-stopping`
        );
        running = false;
        break;
      }

      if (running) await sleep(500);
    }
  })();

  return {
    stop: async () => {
      running = false;
      await loop;
    },
  };
}

/**
 * Poll /watch/api/run/{id}/checkpoints until nodeId appears in checkpoint state
 * (state.last_node === nodeId or downstream node present or terminal result).
 * Default 60s timeout per FR-PW7.
 */
export async function waitForNodeReady(
  url: string,
  runId: string,
  nodeId: string,
  timeoutMs: number = 60_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/watch/api/run/${runId}/checkpoints`);
      if (res.ok) {
        const checkpoints: Array<{ state?: Record<string, unknown> }> =
          await res.json();
        if (checkpoints.length > 0) {
          const latest = checkpoints[checkpoints.length - 1];
          const state = latest.state || {};
          if (state.last_node === nodeId) return;
          // Check if a downstream node checkpoint appeared (node already passed)
          const nodeKeys = Object.keys(state);
          if (nodeKeys.some((k) => k === `${nodeId}_done` || k === `${nodeId}_status`)) {
            return;
          }
          // Terminal result means all nodes done
          if (state.run_status === "done" || state.run_status === "failed") {
            return;
          }
        }
      }
    } catch {
      // not ready
    }
    await sleep(1000);
  }
  throw new Error(
    `waitForNodeReady: node "${nodeId}" not ready within ${timeoutMs}ms`
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
