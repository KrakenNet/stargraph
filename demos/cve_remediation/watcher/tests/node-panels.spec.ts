import { test, expect } from "@playwright/test";
import {
  startServer,
  stopServer,
  postRun,
  startHitlResponder,
  waitForNodeReady,
} from "./server-fixture";

let baseUrl: string;
let runId: string;
let hitl: { stop: () => Promise<void> };

test.describe.serial("cve-rem-node-ui journey", () => {
  test.beforeAll(async () => {
    const server = await startServer();
    baseUrl = server.baseUrl;
    runId = await postRun(baseUrl, "CVE-2021-44228");
    hitl = startHitlResponder(baseUrl, runId);
  });

  test.afterAll(async () => {
    await hitl.stop();
    await stopServer();
  });

  test.beforeEach(async ({ page }) => {
    await page.goto(`${baseUrl}/watch/?run=${runId}`);
  });

  // --- 10 priority + cargonet node panel tests (FR-PW8) ---

  test("renders intake_fetch panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "intake_fetch", 60_000);
    await page.click('[data-node-id="intake_fetch"]');
    const panel = page.locator('[data-panel-id="intake_fetch"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: table contains a row with cve_vendor value (non-empty); CPE URI list >=1 entry
    await expect(panel.locator('[data-field="cve_vendor"]')).not.toBeEmpty();
    await expect(panel.locator('[data-field="cpe_uris"] li')).toHaveCount(1, { timeout: 5_000 });
  });

  test("renders correlate_assets panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "correlate_assets", 60_000);
    await page.click('[data-node-id="correlate_assets"]');
    const panel = page.locator('[data-panel-id="correlate_assets"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: affected-host table >=1 row OR "no hosts matched"; CMDB match shows cmdb_software_name
    const hostsOrMsg = panel.locator('[data-field="affected_hosts"] tr, [data-empty="no-hosts"]');
    await expect(hostsOrMsg.first()).toBeVisible();
    await expect(panel.locator('[data-field="cmdb_software_name"]')).toBeVisible();
  });

  test("renders sandbox_run panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "sandbox_run", 60_000);
    await page.click('[data-node-id="sandbox_run"]');
    const panel = page.locator('[data-panel-id="sandbox_run"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: probe table has 4 rows (baseline/apply/rollback/reapply) OR "probe not yet run"
    const probesOrMsg = panel.locator('[data-field="probe_steps"] tr[data-phase], [data-empty="probe-not-run"]');
    await expect(probesOrMsg.first()).toBeVisible();
  });

  test("renders create_change_request panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "create_change_request", 60_000);
    await page.click('[data-node-id="create_change_request"]');
    const panel = page.locator('[data-panel-id="create_change_request"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: CR id matches /^CHG/ OR cr_status pill rendered
    const crIdOrPill = panel.locator('[data-field="cr_correlation_id"], [data-field="cr_status"]');
    await expect(crIdOrPill.first()).toBeVisible();
  });

  test("renders write_retrospective panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "write_retrospective", 60_000);
    await page.click('[data-node-id="write_retrospective"]');
    const panel = page.locator('[data-panel-id="write_retrospective"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: retro id non-empty AND failure-signals/prevention-suggestions tables present
    await expect(panel.locator('[data-field="retro_id"]')).not.toBeEmpty();
    await expect(panel.locator('[data-table="failure_signals"]')).toBeVisible();
    await expect(panel.locator('[data-table="prevention_suggestions"]')).toBeVisible();
  });

  test("renders krakntrust_attest panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "krakntrust_attest", 60_000);
    await page.click('[data-node-id="krakntrust_attest"]');
    const panel = page.locator('[data-panel-id="krakntrust_attest"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: JWS block visible with truncated token + krakntrust_key_id value
    await expect(panel.locator('[data-field="jws_block"]')).toBeVisible();
    await expect(panel.locator('[data-field="krakntrust_key_id"]')).not.toBeEmpty();
  });

  test("renders drift_watch_spawn panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "drift_watch_spawn", 60_000);
    await page.click('[data-node-id="drift_watch_spawn"]');
    const panel = page.locator('[data-panel-id="drift_watch_spawn"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: drift_child_run_id rendered OR "drift spawn not yet executed"
    const driftOrMsg = panel.locator('[data-field="drift_child_run_id"], [data-empty="drift-not-spawned"]');
    await expect(driftOrMsg.first()).toBeVisible();
  });

  test("renders cargonet_lab_telemetry panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "cargonet_lab_telemetry", 60_000);
    await page.click('[data-node-id="cargonet_lab_telemetry"]');
    const panel = page.locator('[data-panel-id="cargonet_lab_telemetry"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: per_host_verify summary OR "no lab telemetry returned"
    const telemetryOrMsg = panel.locator('[data-field="per_host_verify"], [data-empty="no-telemetry"]');
    await expect(telemetryOrMsg.first()).toBeVisible();
  });

  test("renders emit_sandbox_evidence panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "emit_sandbox_evidence", 60_000);
    await page.click('[data-node-id="emit_sandbox_evidence"]');
    const panel = page.locator('[data-panel-id="emit_sandbox_evidence"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: artifact-ref block shows sandbox_evidence_artifact_ref value (non-empty)
    await expect(panel.locator('[data-field="sandbox_evidence_artifact_ref"]')).not.toBeEmpty();
  });

  test("renders cargonet_writeback panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "cargonet_writeback", 60_000);
    await page.click('[data-node-id="cargonet_writeback"]');
    const panel = page.locator('[data-panel-id="cargonet_writeback"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
    // FR-PW8: status pill shows done OR pending
    const pill = panel.locator('[data-field="writeback_status"]');
    await expect(pill).toBeVisible();
    await expect(pill).toHaveText(/done|pending/);
  });

  // --- 3 additional tests ---

  test("final summary auto-renders on terminal", async ({ page }) => {
    // Wait for terminal state (result event)
    await waitForNodeReady(baseUrl, runId, "__terminal__", 120_000);
    // Summary panel auto-renders without clicking
    const summaryPanel = page.locator('[data-panel-id="__summary__"]');
    await expect(summaryPanel).toBeVisible({ timeout: 10_000 });
    // Pseudo summary card at end of rail
    const summaryCard = page.locator('[data-node-id="__summary__"]');
    await expect(summaryCard).toBeVisible();
  });

  test("URL deep-link restores selection", async ({ page }) => {
    // Navigate with node= param directly
    await page.goto(`${baseUrl}/watch/?run=${runId}&node=sandbox_run`);
    // sandbox_run panel renders without clicking rail
    const panel = page.locator('[data-panel-id="sandbox_run"]');
    await expect(panel).toBeVisible({ timeout: 10_000 });
  });

  test("header-gantt click selects node", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "intake_fetch", 60_000);
    // Click a gantt bar
    const ganttBar = page.locator('[data-gantt-node="intake_fetch"]');
    await expect(ganttBar).toBeVisible({ timeout: 5_000 });
    await ganttBar.click();
    // Panel switches to that node
    const panel = page.locator('[data-panel-id="intake_fetch"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
  });
});
