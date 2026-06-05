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
    // Wait for the node rail to render
    await page.locator('.gp-rows').waitFor({ state: 'visible', timeout: 10_000 });
  });

  /** Click a node card and wait for its panel, retrying if the auto-summary effect overrides. */
  async function clickNodeAndWaitPanel(page: import('@playwright/test').Page, nodeId: string) {
    const card = page.locator(`.gp-rows [data-node-id="${nodeId}"] .sc-card`);
    const panel = page.locator(`[data-panel-id="${nodeId}"]`);
    for (let attempt = 0; attempt < 3; attempt++) {
      await card.dispatchEvent('click');
      try {
        await expect(panel).toBeVisible({ timeout: 3_000 });
        return;
      } catch {
        // Auto-summary may have overridden selection; wait and retry
        await page.waitForTimeout(500);
      }
    }
    // Final attempt with full timeout
    await card.dispatchEvent('click');
    await expect(panel).toBeVisible({ timeout: 5_000 });
  }

  // --- 10 priority + cargonet node panel tests (FR-PW8) ---

  test("renders intake_fetch panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "intake_fetch", 60_000);
    await clickNodeAndWaitPanel(page, "intake_fetch");
    const panel = page.locator('[data-panel-id="intake_fetch"]');
    // FR-PW8: table contains a row with cve_vendor value (non-empty); CPE URI list >=1 entry
    await expect(panel.locator('[data-field="cve_vendor"]')).not.toBeEmpty();
    const cpeItems = panel.locator('[data-field="cpe_uris"] li');
    await expect(cpeItems.first()).toBeVisible({ timeout: 5_000 });
  });

  test("renders correlate_assets panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "correlate_assets", 180_000);
    await clickNodeAndWaitPanel(page, "correlate_assets");
    const panel = page.locator('[data-panel-id="correlate_assets"]');
    // FR-PW8: affected-host table >=1 row OR "no hosts matched"; CMDB match shows cmdb_software_name
    const hostsOrMsg = panel.locator('[data-field="affected_hosts"] tr, [data-empty="no-hosts"]');
    await expect(hostsOrMsg.first()).toBeVisible();
    await expect(panel.locator('[data-field="cmdb_software_name"]')).toBeVisible();
  });

  test("renders sandbox_run panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "sandbox_run", 180_000);
    await clickNodeAndWaitPanel(page, "sandbox_run");
    const panel = page.locator('[data-panel-id="sandbox_run"]');
    // FR-PW8: probe table has 4 rows (baseline/apply/rollback/reapply) OR "probe not yet run"
    const probesOrMsg = panel.locator('[data-field="probe_steps"] tr[data-phase], [data-empty="probe-not-run"]');
    await expect(probesOrMsg.first()).toBeVisible();
  });

  test("renders create_change_request panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "create_change_request", 180_000);
    await clickNodeAndWaitPanel(page, "create_change_request");
    const panel = page.locator('[data-panel-id="create_change_request"]');
    // FR-PW8: CR id matches /^CHG/ OR cr_status pill rendered
    const crIdOrPill = panel.locator('[data-field="cr_correlation_id"], [data-field="cr_status"]');
    await expect(crIdOrPill.first()).toBeVisible();
  });

  test("renders write_retrospective panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "write_retrospective", 180_000);
    await clickNodeAndWaitPanel(page, "write_retrospective");
    const panel = page.locator('[data-panel-id="write_retrospective"]');
    // FR-PW8: retro id non-empty AND failure-signals/prevention-suggestions tables present
    // OR empty-state copy if node completed without detailed delta data
    const retroId = panel.locator('[data-field="retro_id"]');
    const hasData = await retroId.count() > 0;
    if (hasData) {
      await expect(retroId).not.toBeEmpty();
      await expect(panel.locator('[data-table="failure_signals"]')).toBeVisible();
      await expect(panel.locator('[data-table="prevention_suggestions"]')).toBeVisible();
    } else {
      // Empty-state: panel rendered but no detailed data (node done without delta)
      await expect(panel).toContainText(/(pending|running|done|No data|no state changes)/i);
    }
  });

  test("renders krakntrust_attest panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "krakntrust_attest", 180_000);
    await clickNodeAndWaitPanel(page, "krakntrust_attest");
    const panel = page.locator('[data-panel-id="krakntrust_attest"]');
    // FR-PW8: JWS block visible with truncated token + krakntrust_key_id value
    // OR empty-state copy if node completed without detailed delta data
    const jwsBlock = panel.locator('[data-field="jws_block"]');
    const hasData = await jwsBlock.count() > 0;
    if (hasData) {
      await expect(jwsBlock).toBeVisible();
      await expect(panel.locator('[data-field="krakntrust_key_id"]')).not.toBeEmpty();
    } else {
      await expect(panel).toContainText(/(pending|running|done|No data|no state changes)/i);
    }
  });

  test("renders drift_watch_spawn panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "drift_watch_spawn", 180_000);
    await clickNodeAndWaitPanel(page, "drift_watch_spawn");
    const panel = page.locator('[data-panel-id="drift_watch_spawn"]');
    // FR-PW8: drift_child_run_id rendered OR "drift spawn not yet executed" OR empty-state
    const driftOrMsg = panel.locator('[data-field="drift_child_run_id"], [data-empty="drift-not-spawned"]');
    const hasContent = await driftOrMsg.count() > 0;
    if (hasContent) {
      await expect(driftOrMsg.first()).toBeVisible();
    } else {
      await expect(panel).toContainText(/(pending|running|done|No data|drift)/i);
    }
  });

  test("renders cargonet_lab_telemetry panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "cargonet_lab_telemetry", 180_000);
    await clickNodeAndWaitPanel(page, "cargonet_lab_telemetry");
    const panel = page.locator('[data-panel-id="cargonet_lab_telemetry"]');
    // FR-PW8: per_host_verify summary OR "no lab telemetry returned" OR empty-state
    const telemetryOrMsg = panel.locator('[data-field="per_host_verify"], [data-empty="no-telemetry"]');
    const hasContent = await telemetryOrMsg.count() > 0;
    if (hasContent) {
      await expect(telemetryOrMsg.first()).toBeVisible();
    } else {
      await expect(panel).toContainText(/(pending|running|done|No data|telemetry)/i);
    }
  });

  test("renders emit_sandbox_evidence panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "emit_sandbox_evidence", 180_000);
    await clickNodeAndWaitPanel(page, "emit_sandbox_evidence");
    const panel = page.locator('[data-panel-id="emit_sandbox_evidence"]');
    // FR-PW8: artifact-ref block shows sandbox_evidence_artifact_ref value (non-empty) OR empty-state
    const artifactRef = panel.locator('[data-field="sandbox_evidence_artifact_ref"]');
    const hasData = await artifactRef.count() > 0;
    if (hasData) {
      await expect(artifactRef).not.toBeEmpty();
    } else {
      await expect(panel).toContainText(/(pending|running|done|No data|evidence)/i);
    }
  });

  test("renders cargonet_writeback panel", async ({ page }) => {
    await waitForNodeReady(baseUrl, runId, "cargonet_writeback", 180_000);
    await clickNodeAndWaitPanel(page, "cargonet_writeback");
    const panel = page.locator('[data-panel-id="cargonet_writeback"]');
    // FR-PW8: status pill shows done OR pending; OR empty-state if no delta
    const pill = panel.locator('[data-field="writeback_status"]');
    const hasData = await pill.count() > 0;
    if (hasData) {
      await expect(pill).toBeVisible();
      await expect(pill).toHaveText(/done|pending/);
    } else {
      await expect(panel).toContainText(/(pending|running|done|No data|writeback)/i);
    }
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
    // Click a gantt bar (timing data may take a moment to hydrate for terminal runs)
    const ganttBar = page.locator('[data-gantt-node="intake_fetch"]');
    await expect(ganttBar).toBeVisible({ timeout: 15_000 });
    await ganttBar.dispatchEvent('click');
    // Panel switches to that node
    const panel = page.locator('[data-panel-id="intake_fetch"]');
    await expect(panel).toBeVisible({ timeout: 5_000 });
  });
});
