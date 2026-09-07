/**
 * Phase 5 gate — the four numbers, measured rather than asserted.
 *
 *   env -u ELECTRON_RUN_AS_NODE ./node_modules/.bin/electron tools/measure_gate.cjs
 *
 * Boots the real app (app/main/main.cjs — real window, real preload, real bundle) and
 * drives it through the app's own debug handle, so what gets measured is the shipping
 * renderer and not a lookalike harness.
 *
 * ELECTRON_RUN_AS_NODE must be unset. VSCode's integrated terminal exports it, and with
 * it set Electron runs as plain Node: no window, no error, exit 0.
 *
 * Full payload resident is the gate's stress case, deliberately not how the app streams.
 * The app loads a ring around the driver (LOAD_RADIUS in app/renderer/src/main.jsx); the
 * gate asks the different question of whether the stack holds the whole 5km world at once.
 */
const { app, BrowserWindow, screen } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

const PROCESS_START = Date.now();
const ROOT = path.resolve(__dirname, '..');
const FPS_FRAMES = 600; // ~10s at 60fps
const results = {};

require(path.join(ROOT, 'app/main/main.cjs'));

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function directorySize(directory) {
  let total = 0;
  const walk = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) total += fs.statSync(full).size;
    }
  };
  walk(directory);
  return total;
}

const MB = (bytes) => `${(bytes / 1048576).toFixed(1)} MB`;

// Frame deltas sampled from the page's own rAF, so this is real presented-frame cadence
// under whatever the compositor is doing, not a synthetic render-loop timing.
const SAMPLE_FPS = `new Promise((resolve) => {
  const deltas = [];
  let last = performance.now();
  function tick() {
    const now = performance.now();
    deltas.push(now - last);
    last = now;
    if (deltas.length < ${FPS_FRAMES}) requestAnimationFrame(tick);
    else resolve(deltas.slice(1));
  }
  requestAnimationFrame(tick);
})`;

// Time from the choice landing to the panel actually showing the new street, measured on
// the DOM. That is the number the plan's "perceived" threshold is about.
const PANEL_LATENCY = `new Promise((resolve) => {
  const heading = document.querySelector('.panel-heading h2');
  const button = [...document.querySelectorAll('.choice')].find((b) => !b.disabled);
  if (!button) { resolve(null); return; }
  void 0;
  const before = heading.textContent;
  const observer = new MutationObserver(() => {
    if (heading.textContent !== before) {
      observer.disconnect();
      resolve(performance.now() - start);
    }
  });
  observer.observe(document.querySelector('.panel'), { subtree: true, childList: true, characterData: true });
  const start = performance.now();
  button.click();
  setTimeout(() => { observer.disconnect(); resolve(null); }, 2000);
})`;

function stats(deltas) {
  const sorted = [...deltas].sort((a, b) => a - b);
  const at = (q) => sorted[Math.floor(sorted.length * q)];
  return {
    median: 1000 / at(0.5),
    // p95 of frame *time* is the worst-case frame, so the low end of fps.
    low: 1000 / at(0.95),
    worstFrameMs: sorted[sorted.length - 1]
  };
}

app.whenReady().then(async () => {
  await wait(500);
  const win = BrowserWindow.getAllWindows()[0];
  if (!win) { console.error('no window'); app.exit(1); return; }
  win.show();
  win.focus();

  try {
    await win.webContents.executeJavaScript(
      'new Promise(r => (document.readyState === "complete" ? r() : addEventListener("load", r)))'
    );

    // 3 — cold start: process start to the first frame the renderer actually presented.
    await win.webContents.executeJavaScript(
      'new Promise(r => { const t = setInterval(() => { if (window.__mtd?.firstFrameEpoch) { clearInterval(t); r(); } }, 16); })'
    );
    const firstFrame = await win.webContents.executeJavaScript('window.__mtd.firstFrameEpoch');
    results.coldStartS = (firstFrame - PROCESS_START) / 1000;

    // The vsync ceiling: no fps number here can exceed the display's refresh rate,
    // so the >= 60 bar is only meaningful if the panel can actually present 60.
    results.refreshHz = screen.getPrimaryDisplay().displayFrequency || null;

    // Ring baseline, sampled only once chunk loading has actually gone quiet. Sampling
    // through in-flight loads measures the loader, not the renderer.
    await win.webContents.executeJavaScript(
      'new Promise(r => { const t = setInterval(() => { if (__mtd.counts().inflight === 0) { clearInterval(t); r(); } }, 100); })'
    );
    await wait(2000);
    results.ringFps = stats(await win.webContents.executeJavaScript(SAMPLE_FPS));
    results.ringChunks = (await win.webContents.executeJavaScript('window.__mtd.counts()')).loaded;

    // 1 — full 5km payload resident.
    const loadStart = Date.now();
    const requested = await win.webContents.executeJavaScript('window.__mtd.loadAll()');
    results.payloadLoadS = (Date.now() - loadStart) / 1000;
    results.chunksRequested = requested;
    results.chunksResident = (await win.webContents.executeJavaScript('window.__mtd.counts()')).loaded;
    results.heapMB = (await win.webContents.executeJavaScript(
      'performance.memory ? performance.memory.usedJSHeapSize / 1048576 : null'
    ));
    results.drawInfo = await win.webContents.executeJavaScript(
      'JSON.stringify({ geometries: __mtd.renderer.info.memory.geometries, calls: __mtd.renderer.info.render.calls, triangles: __mtd.renderer.info.render.triangles })'
    );

    // Warm up properly: with 992 chunks resident, geometries upload to the GPU lazily as
    // the driving camera brings them into the frustum, and those uploads show up as long
    // frames for a while after loading finishes.
    await wait(6000);
    results.fullFpsRuns = [];
    for (let run = 0; run < 3; run += 1) {
      results.fullFpsRuns.push(stats(await win.webContents.executeJavaScript(SAMPLE_FPS)));
    }
    results.fullFps = results.fullFpsRuns.reduce((best, s) => (s.median > best.median ? s : best));

    // 2 — panel update latency, full payload resident (the pessimistic case).
    const latencies = [];
    for (let attempt = 0; attempt < 10; attempt += 1) {
      // Wait for an actual junction rather than clicking into a disabled panel.
      await win.webContents.executeJavaScript(
        'new Promise(r => { const t = setInterval(() => { if ([...document.querySelectorAll(".choice")].some(b => !b.disabled)) { clearInterval(t); r(); } }, 50); setTimeout(() => { clearInterval(t); r(); }, 25000); })'
      );
      const value = await win.webContents.executeJavaScript(PANEL_LATENCY);
      if (value !== null) latencies.push(value);
    }
    results.panelMs = latencies.length
      ? { samples: latencies.length, median: [...latencies].sort((a, b) => a - b)[Math.floor(latencies.length / 2)], worst: Math.max(...latencies) }
      : null;

    // 4 — size. Recorded, not gated. No packaging config exists yet, so this is the
    // honest floor: what a package would have to contain, not a built artefact.
    results.size = {
      electronRuntime: directorySize(path.join(ROOT, 'node_modules/electron/dist')),
      appBundle: directorySize(path.join(ROOT, 'app/renderer/dist')),
      worldData: directorySize(path.join(ROOT, 'data/build/buildings')) + directorySize(path.join(ROOT, 'data/build/roads')),
      graphAndRules: fs.statSync(path.join(ROOT, 'data/build/graph.json')).size + fs.statSync(path.join(ROOT, 'data/build/rules.json')).size
    };
  } catch (error) {
    console.error('[gate error]', error);
  }

  const fps = (s) => `median ${s.median.toFixed(1)} / p95-low ${s.low.toFixed(1)} fps (worst frame ${s.worstFrameMs.toFixed(1)}ms)`;
  console.log('\n================ PHASE 5 GATE ================');
  console.log(`Cold start -> first interactive frame : ${results.coldStartS?.toFixed(2)}s   (bar: <= 5s)`);
  console.log(`Display refresh ceiling               : ${results.refreshHz || 'unknown'} Hz`);
  console.log(`Ring streaming (${results.ringChunks} chunks)      : ${results.ringFps ? fps(results.ringFps) : 'n/a'}`);
  console.log(`Full payload (${results.chunksResident}/${results.chunksRequested} chunks)   : ${results.fullFps ? fps(results.fullFps) : 'n/a'}   (bar: >= 60fps)`);
  console.log(`  best of ${results.fullFpsRuns?.length || 0} runs: ${results.fullFpsRuns?.map((r) => r.median.toFixed(1)).join(' / ') || 'n/a'} fps median`);
  console.log(`  loaded in ${results.payloadLoadS?.toFixed(1)}s, JS heap ${results.heapMB ? results.heapMB.toFixed(0) + ' MB' : 'n/a'}, ${results.drawInfo}`);
  console.log(`Panel update on entering a leg        : ${results.panelMs ? `median ${results.panelMs.median.toFixed(1)}ms, worst ${results.panelMs.worst.toFixed(1)}ms over ${results.panelMs.samples}` : 'n/a'}   (bar: <= 100ms)`);
  if (results.size) {
    console.log('Size (recorded, not gated):');
    console.log(`  Electron runtime ${MB(results.size.electronRuntime)} · app bundle ${MB(results.size.appBundle)}`);
    console.log(`  world GLB ${MB(results.size.worldData)} · graph+rules ${MB(results.size.graphAndRules)}`);
    console.log(`  => a package would carry at least ${MB(Object.values(results.size).reduce((a, b) => a + b, 0))}`);
  }
  console.log('==============================================\n');

  fs.writeFileSync(path.join(ROOT, 'docs/gate-phase5.json'), `${JSON.stringify(results, null, 2)}\n`);
  console.log('written: docs/gate-phase5.json');
  app.exit(0);
});
