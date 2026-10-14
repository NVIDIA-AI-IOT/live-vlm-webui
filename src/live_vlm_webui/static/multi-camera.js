/* SPDX-License-Identifier: Apache-2.0 */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const apiRoot = "/api/multi-camera/sessions";
  const tiles = new Map();
  const savedCameras = new Map();
  const lastWebcamSources = new Map();
  const segmentationTargetFps = 25;
  const descriptionModes = {
    fast: {
      tokens: 32,
      imageMaxEdge: 512,
      hint: "One short sentence: person, location, action, object.",
      prompt: "Describe the most visible action in one sentence of about 15 words: person, position in the scene, action, and object. Use only visible evidence; do not infer identity or intent. If no action is clear, say what is visible.",
    },
    detailed: {
      tokens: 96,
      imageMaxEdge: 640,
      hint: "Up to two sentences with visible actions and object interactions.",
      prompt: "In up to two concise sentences (40 words), describe visible people, their positions in the scene, their actions, and the objects they handle or interact with. Use only visible evidence; do not infer identity or intent. If an action is unclear, describe the visible pose or scene.",
    },
  };
  const previousDemoPrompt = "Describe the visible shopping activity in one short, factual sentence. Mention only actions and objects visible in the image.";
  let cameraCount = 2;
  let devices = [];
  let usbDevices = [];
  let usbCatalogLoaded = false;
  let usbCatalogPromise = null;
  let loadingUsb = false;
  const usbSelections = new Map();
  let usbRefreshTimer = null;
  let usbRefreshPromise = null;
  let usbRefreshPaused = false;
  let kioskCameraMode = null;
  let kioskCameraTransition = false;
  let kioskLaunchGeneration = 0;
  let activeRun = null;
  let phase = "idle";
  let presentationPreferred = false;
  let enumerating = false;
  let backendSelectedByUser = false;
  let apiBaseEdited = false;
  let serverApiBase = null;
  let modelDiscoveryGeneration = 0;
  let demoCatalog = [];
  let demoCatalogPromise = null;
  let demoMaxFps = 25;
  let demoMaxWidth = 960;
  let demoMaxHeight = 540;
  let loadingDemo = false;
  let preparingDemo = false;
  let storeDemoPreset = false;
  let segmentationProfile = null;
  let configurableSegmentationSettings = null;
  let recommendedConfidence = null;
  let confidenceInitialized = false;
  let confidenceEditedByUser = false;
  let kioskAutomatic = false;

  function notice(message = "") {
    $("notice").textContent = message;
    $("notice").hidden = !message;
  }

  function badge(element, text, style = "") {
    element.textContent = text;
    const state = { live: "connected", pending: "processing", error: "error" }[style] || "disconnected";
    element.className = `status-badge ${state}`;
    if (element !== $("session-status")) element.classList.add("camera-state");
  }

  function setPhase(next) {
    phase = next;
    const busy = next !== "idle";
    $("start").disabled = busy || enumerating || loadingDemo || loadingUsb || preparingDemo;
    $("stop").disabled = (!busy && !kioskCameraMode && !(preparingDemo && kioskAutomatic)) || (next === "stopping" && !kioskCameraMode);
    $("focus-stop").disabled = $("stop").disabled;
    if (busy) toggleApiPresets(false);
    $("two-cameras").disabled = busy || loadingDemo || loadingUsb || preparingDemo;
    $("four-cameras").disabled = busy || loadingDemo || loadingUsb || preparingDemo;
    document.body.classList.toggle("streams-active", busy);
    $("settings-note").hidden = !busy;
    $("enable-cameras").disabled = busy || enumerating || loadingDemo || loadingUsb || preparingDemo;
    $("usb-cameras").disabled = busy || enumerating || loadingDemo || loadingUsb || preparingDemo;
    $("multi-stream-view").querySelectorAll(".inference-settings input, .inference-settings textarea, .inference-settings select, .inference-settings button, .camera-runtime-settings input, .camera-runtime-settings select, .source-controls input, .source-controls select, .camera-prompt textarea").forEach((element) => {
      element.disabled = busy || loadingUsb || preparingDemo;
    });
    const labels = { idle: "Not started", starting: "Connecting cameras", running: "Cameras connected", stopping: "Stopping cameras" };
    badge($("session-status"), labels[next], next === "running" ? "live" : busy ? "pending" : "");
    if (!busy) {
      populateUsbDevices();
      syncToggles();
    }
    syncSegmentationProfileControls();
    syncSourceControls();
    updatePresentation();
  }


  function updatePresentation() {
    const presenting = phase === "running" && presentationPreferred;
    const compactFour = cameraCount === 4 && phase !== "idle";
    document.body.classList.toggle("compact-four-view", compactFour);
    document.body.classList.toggle("presentation-view", presenting);
    const toggle = $("presentation-toggle");
    toggle.hidden = phase !== "running";
    $("focus-stop").hidden = !presenting;
    toggle.textContent = presenting ? "Show settings" : "Focus view";
    toggle.setAttribute("aria-pressed", String(presenting));
    const recorded = [...tiles.values()].filter((tile) => tile.source.value === "demo");
    const attribution = $("presentation-attribution");
    attribution.replaceChildren();
    $("demo-note").hidden = recorded.length === 0;
    const seen = new Set();
    for (const tile of recorded) {
      const clip = demoCatalog.find((entry) => entry.demo_id === tile.demo.value);
      if (!clip) continue;
      const key = `${clip.attribution}|${clip.source_url}|${clip.license_url}`;
      if (seen.has(key)) continue;
      if (seen.size) attribution.append(document.createTextNode(" · "));
      seen.add(key);
      attribution.append(document.createTextNode(`${clip.attribution || "Recorded store footage"} · `));
      attribution.append(safeExternalLink("Source", clip.source_url), document.createTextNode(" · "), safeExternalLink(clip.license || "CC BY-SA 4.0", clip.license_url));
    }
    if (recorded.length) attribution.append(document.createTextNode(" · Looped excerpts; resized and annotated."));
  }

  function syncToggles() {
    for (const [toggleId, groupId] of [["segmentation-enabled", "segmentation-controls"], ["vlm-enabled", "vlm-controls"]]) {
      const enabled = $(toggleId).checked;
      $(groupId).classList.toggle("disabled-group", !enabled);
      $(groupId).querySelectorAll("input, textarea, select, button").forEach((element) => {
        element.disabled = phase !== "idle" || loadingUsb || preparingDemo || !enabled;
      });
      $(toggleId).nextElementSibling.textContent = enabled ? "On" : "Off";
    }
    for (const tile of tiles.values()) {
      tile.promptDetails.hidden = !$("vlm-enabled").checked;
    }
    syncSegmentationProfileControls();
    syncTemporalControls();
    syncSourceControls();
  }

  function temporalConfiguration() {
    return {
      enabled: $("vlm-enabled").checked && $("video-understanding").value === "temporal",
      window_seconds: Number($("temporal-window").value),
      sample_fps: 2,
    };
  }

  function syncTemporalControls() {
    const temporal = temporalConfiguration();
    const disabled = phase !== "idle" || loadingUsb || preparingDemo || !$("vlm-enabled").checked;
    $("video-understanding").disabled = disabled;
    $("process-every").disabled = disabled;
    $("temporal-window-field").hidden = $("video-understanding").value !== "temporal";
    $("temporal-window").disabled = disabled || !temporal.enabled;
    $("temporal-hint").hidden = false;
    $("temporal-hint").textContent = $("video-understanding").value === "temporal"
      ? "The first result waits for the video window to fill."
      : "Each VLM request analyzes one sampled frame.";
  }

  function syncSourceControls() {
    const sources = [...tiles.values()].map((tile) => tile.source.value);
    for (const [id, tile] of tiles) {
      if (["webrtc", "usb"].includes(tile.source.value)) lastWebcamSources.set(id, tile.source.value);
    }
    const webcam = sources.length > 0 && sources.every((source) => ["webrtc", "usb"].includes(source));
    const rtsp = sources.length > 0 && sources.every((source) => source === "rtsp");
    const recorded = sources.length > 0 && sources.every((source) => source === "demo");
    const disabled = phase !== "idle" || enumerating || loadingDemo || loadingUsb || preparingDemo;
    for (const [id, selected] of [["source-webcam", webcam], ["source-rtsp", rtsp]]) {
      $(id).classList.toggle("active", selected);
      $(id).setAttribute("aria-selected", String(selected));
      $(id).tabIndex = selected || (!webcam && !rtsp && id === "source-webcam") ? 0 : -1;
      $(id).disabled = disabled;
    }
    $("webcam-source-actions").hidden = rtsp;
    $("camera-selection-hint").textContent = rtsp
      ? "Enter a stream URL in each camera tile."
      : "Select a source and device in each camera tile.";
    const action = rtsp ? "Connect to Streams" : recorded ? "Play Videos" : "Open Cameras";
    $("start-label").replaceChildren(document.createTextNode(action));
    if ($("vlm-enabled").checked) {
      $("start-label").append(document.createTextNode(" and"), document.createElement("br"), document.createTextNode("Start VLM Analysis"));
    }
    syncUsbGuidance();
  }

  function selectSource(source, browserOnly = false) {
    if (phase !== "idle" || loadingDemo || loadingUsb || preparingDemo) return;
    syncSourceControls();
    storeDemoPreset = false;
    for (const [id, tile] of tiles) {
      tile.source.value = source === "rtsp" ? "rtsp" : browserOnly ? "webrtc" : lastWebcamSources.get(id) || "webrtc";
      tile.updateSource();
    }
    populateUsbDevices();
    rememberCameras();
    syncSourceControls();
  }

  function renderTemporalContext(tile, data = {}, completed = false) {
    if (data.temporal) tile.temporal = data.temporal;
    const metrics = data.metrics || data;
    if (completed) {
      tile.temporalHasResult = true;
      tile.temporalProgress.hidden = true;
      if (metrics.temporal_enabled === true) {
        const count = metrics.temporal_frame_count;
        const span = metrics.temporal_window_seconds;
        const known = Number.isInteger(count) && Number.isFinite(span);
        tile.temporalContext.textContent = known ? `Recent video · ${count} frames across ${span.toFixed(1)} s` : "Recent-video result";
        const transport = metrics.temporal_transport === "video" ? "a video clip" : metrics.temporal_transport === "images" ? "ordered frame images" : "recent frames";
        tile.temporalContext.title = `Context used for this result: ${transport}. The duration is the measured span between the first and last sampled frame, not the configured collection window.`;
      } else {
        tile.temporalContext.textContent = metrics.temporal_enabled === false || !tile.temporal?.enabled ? "Single-frame result" : "Video context not reported";
        tile.temporalContext.title = "Context reported by the model request for this completed response.";
      }
      tile.temporalContext.hidden = false;
      return;
    }
    if (tile.temporalHasResult) return;
    const enabled = tile.temporal?.enabled ?? metrics.temporal_enabled === true;
    tile.temporalContext.hidden = !enabled;
    tile.temporalProgress.hidden = !enabled;
    if (!enabled) return;
    const count = data.temporal_buffer_frames ?? metrics.temporal_buffer_frames;
    const ready = data.temporal_buffer_ready ?? metrics.temporal_buffer_ready;
    const capacity = (tile.temporal?.window_seconds || 4) * (tile.temporal?.sample_fps || 2);
    tile.temporalContext.textContent = ready === true ? "Recent video ready · waiting for result"
      : Number.isInteger(count) ? `Collecting recent frames · ${count}/${capacity}` : "Collecting recent frames…";
    tile.temporalProgress.textContent = ready === true ? "Video ready"
      : Number.isInteger(count) ? `Collecting ${count}/${capacity}` : "Collecting video…";
    tile.temporalContext.title = "Each camera collects its own recent frames before the first temporal request. This is buffer progress, not a completed VLM result.";
  }

  function rememberCameras() {
    for (const [id, tile] of tiles) {
      savedCameras.set(id, {
        source: tile.source.value,
        device: tile.device.value,
        usb: tile.usb.value || usbSelections.get(id) || "",
        rtsp: tile.rtsp.value,
        demo: tile.demo.value,
        prompt: tile.prompt.value,
      });
    }
  }

  function populateDevices() {
    for (const [id, tile] of tiles) {
      const previous = tile.device.value || savedCameras.get(id)?.device;
      tile.device.replaceChildren();
      const placeholder = new Option(devices.length ? "Select a camera" : "Allow browser cameras to select a device", "");
      tile.device.add(placeholder);
      devices.forEach((device, index) => tile.device.add(new Option(device.label || `Camera device ${index + 1}`, device.deviceId)));
      if (previous && devices.some((device) => device.deviceId === previous)) {
        tile.device.value = previous;
      } else if (devices[tile.number - 1]?.deviceId) {
        tile.device.value = devices[tile.number - 1].deviceId;
      }
    }
  }

  function syncUsbGuidance() {
    const usbTiles = [...tiles.entries()].filter(([, tile]) => tile.source.value === "usb");
    const available = new Set(usbDevices.map((device) => device.device_id));
    const selected = new Set();
    let missing = false;
    for (const [id, tile] of tiles) {
      let message = "";
      if (phase === "idle" && tile.source.value === "usb") {
        if (tile.usb.value && available.has(tile.usb.value) && !selected.has(tile.usb.value)) {
          selected.add(tile.usb.value);
        } else {
          missing = true;
          const remembered = usbSelections.get(id);
          message = remembered && !available.has(remembered)
            ? "Camera disconnected. Reconnect it or select another device."
            : usbDevices.length < usbTiles.length
              ? "Waiting for another USB camera. Connect it or change this source."
              : "Select a different USB camera for this stream.";
        }
      }
      tile.usbHint.textContent = message;
      tile.usbHint.hidden = !message;
      tile.usb.setAttribute("aria-invalid", String(Boolean(message)));
    }
    const summary = $("usb-selection-status");
    summary.hidden = !missing;
    summary.textContent = !missing ? "" : usbDevices.length < usbTiles.length
      ? `${usbDevices.length} of ${usbTiles.length} USB cameras detected. Connect more cameras or change a source.`
      : "Select a different USB camera for each USB stream, or reconnect the missing camera.";
    $("start").disabled = phase !== "idle" || enumerating || loadingDemo || loadingUsb || preparingDemo || missing;
  }

  function populateUsbDevices(changedId = null) {
    if (phase !== "idle") return;
    const available = new Set(usbDevices.map((device) => device.device_id));
    const preferences = new Map([...tiles].map(([id, tile]) => [id,
      usbSelections.has(id) ? usbSelections.get(id) : tile.usb.value || savedCameras.get(id)?.usb || null,
    ]));
    const active = [...tiles].filter(([, tile]) => tile.source.value === "usb")
      .sort(([left], [right]) => Number(left === changedId) - Number(right === changedId));
    const assigned = new Map();
    const used = new Set();
    // Reserve existing choices first, so a newly selected USB source cannot take
    // a device from another tile. Missing preferences survive unplug/replug.
    for (const [id] of active) {
      const previous = preferences.get(id);
      if (previous && available.has(previous)) {
        if (!used.has(previous)) { assigned.set(id, previous); used.add(previous); }
        else { preferences.set(id, null); usbSelections.delete(id); }
      }
    }
    for (const [id] of active) {
      if (assigned.has(id)) continue;
      const choice = preferences.get(id) === null ? [...available].find((device) => !used.has(device)) : "";
      assigned.set(id, choice || "");
      if (choice) used.add(choice);
    }
    for (const [id, tile] of tiles) {
      const previous = preferences.get(id);
      if (previous) usbSelections.set(id, previous);
      const choice = assigned.has(id) ? assigned.get(id)
        : previous === null ? usbDevices[tile.number - 1]?.device_id || "" : available.has(previous) ? previous : "";
      if (choice) usbSelections.set(id, choice);
      tile.usb.replaceChildren(new Option(usbDevices.length ? "Select a USB camera" : "No USB cameras on the Jetson", ""));
      for (const device of usbDevices) {
        const option = new Option(device.label || device.device_id, device.device_id);
        option.disabled = tile.source.value === "usb" && used.has(device.device_id) && choice !== device.device_id;
        tile.usb.add(option);
      }
      tile.usb.value = choice;
    }
    syncUsbGuidance();
  }

  async function loadUsbCameras(force = false) {
    if (usbCatalogPromise) return usbCatalogPromise;
    if (usbCatalogLoaded && !force) return usbDevices;
    usbCatalogPromise = (async () => {
      const result = await request("/api/usb-cameras", {}, 15000);
      const seen = new Set();
      usbDevices = (Array.isArray(result.cameras) ? result.cameras : []).filter((device) => {
        if (!device || typeof device.device_id !== "string" || !/^video\d+$/.test(device.device_id) || seen.has(device.device_id)) return false;
        seen.add(device.device_id);
        return true;
      });
      usbCatalogLoaded = true;
      if (phase === "idle") populateUsbDevices();
      return usbDevices;
    })();
    try { return await usbCatalogPromise; }
    finally { usbCatalogPromise = null; }
  }

  function cancelKioskAutomation() {
    kioskAutomatic = false;
    kioskCameraMode = null;
    kioskLaunchGeneration += 1;
    preparingDemo = false;
  }

  function kioskCameraPlan(mode) {
    const count = mode.cameras || (usbDevices.length >= 4 ? 4 : 2);
    if (usbDevices.length >= count) {
      const available = new Set(usbDevices.map((device) => device.device_id));
      const ids = [...new Set([...(mode.selectedIds || []).filter((id) => available.has(id)), ...available])].slice(0, count);
      return { source: "usb", count, ids, key: `usb:${ids.join(",")}` };
    }
    if (mode.source === "auto") return { source: "demo", count: mode.cameras || 4, key: `demo:${mode.cameras || 4}` };
    return { source: "waiting", count, key: `waiting:${count}` };
  }

  async function reconcileKioskCameras() {
    const mode = kioskCameraMode;
    if (!mode || kioskCameraTransition || preparingDemo || !["idle", "running"].includes(phase) || usbRefreshPaused) return;
    const generation = kioskLaunchGeneration;
    const current = () => kioskCameraMode === mode && kioskLaunchGeneration === generation && !usbRefreshPaused;
    const plan = kioskCameraPlan(mode);
    if (mode.appliedKey === plan.key && (phase === "running" || plan.source === "waiting")) return;
    if (mode.attemptedKey === plan.key && Date.now() < mode.retryAt) return;
    kioskCameraTransition = true;
    try {
      if (!mode.ready) {
        preparingDemo = true;
        setPhase(phase);
        const [segmentation, models] = await Promise.all([checkSegmentation(), discoverModels()]);
        if (!current()) return;
        mode.ready = segmentation.available && Array.isArray(models?.models) && models.models.length > 0;
        if (!mode.ready) { notice("Waiting for demo models…"); return; }
      }
      if (activeRun) {
        const previous = activeRun;
        await stopRun();
        if (!current()) return;
        if (previous.cleanupError) {
          cancelKioskAutomation();
          notice(previous.cleanupError);
          setPhase(phase);
          return;
        }
      }
      if (!current()) return;
      preparingDemo = true;
      setPhase(phase);
      renderCameras(plan.count);
      if (plan.source === "demo") {
        if (!await applyStoreDemo(current) || !current()) return;
      } else {
        storeDemoPreset = false;
        populateUsbDevices();
        for (const [id, tile] of tiles) {
          tile.source.value = "usb";
          if (plan.ids) {
            tile.usb.value = plan.ids[tile.number - 1];
            usbSelections.set(id, tile.usb.value);
          }
          tile.updateSource();
        }
        rememberCameras();
        if (plan.ids) mode.selectedIds = plan.ids;
      }
      mode.appliedKey = plan.key;
      if (plan.source === "waiting") {
        notice(`Waiting for ${plan.count} USB cameras on the Jetson…`);
        return;
      }
      presentationPreferred = true;
      preparingDemo = false;
      setPhase(phase);
      mode.attemptedKey = plan.key;
      mode.retryAt = Date.now() + 10000;
      const run = await start();
      if (current() && run?.cleanupError) {
        cancelKioskAutomation();
        notice(run.cleanupError);
      }
    } catch {
      // Discovery or readiness may be temporarily offline. The next refresh retries.
    } finally {
      kioskCameraTransition = false;
      if (kioskCameraMode === mode && kioskLaunchGeneration === generation) preparingDemo = false;
      setPhase(phase);
    }
  }

  async function refreshUsbCatalog(force = false) {
    if (usbRefreshPromise) return usbRefreshPromise;
    if (usbRefreshPaused || (!force && phase !== "idle" && !kioskCameraMode)) return;
    usbRefreshPromise = (async () => {
      try {
        await loadUsbCameras(true);
        if (!usbRefreshPaused) await reconcileKioskCameras();
      } catch { /* Background discovery must not replace an operator's message. */ }
    })();
    try { return await usbRefreshPromise; }
    finally { usbRefreshPromise = null; }
  }

  function scheduleUsbRefresh() {
    window.clearTimeout(usbRefreshTimer);
    if (usbRefreshPaused) return;
    usbRefreshTimer = window.setTimeout(async () => {
      await refreshUsbCatalog();
      scheduleUsbRefresh();
    }, 3000);
  }

  async function applyUsbCameras() {
    if (phase !== "idle" || loadingUsb) return;
    loadingUsb = true;
    storeDemoPreset = false;
    rememberCameras();
    for (let number = 1; number <= 4; number += 1) {
      const id = `camera-${number}`;
      const saved = savedCameras.get(id) || {};
      savedCameras.set(id, { ...saved, source: "usb" });
      // This explicit bulk action can fill an intentionally cleared slot again.
      if (usbSelections.get(id) === "") usbSelections.delete(id);
    }
    for (const tile of tiles.values()) { tile.source.value = "usb"; tile.updateSource(); }
    setPhase(phase);
    notice();
    try {
      // Selection guidance is updated in place whenever the catalog changes.
      await loadUsbCameras(true);
    } catch (error) { notice(error.message); }
    finally { loadingUsb = false; setPhase(phase); }
  }

  function renderCameras(count) {
    rememberCameras();
    cameraCount = count;
    tiles.clear();
    $("camera-grid").replaceChildren();
    $("camera-grid").dataset.count = String(count);
    $("two-cameras").setAttribute("aria-pressed", String(count === 2));
    $("four-cameras").setAttribute("aria-pressed", String(count === 4));
    for (let number = 1; number <= count; number += 1) {
      const id = `camera-${number}`;
      const card = $("camera-template").content.firstElementChild.cloneNode(true);
      const select = (selector) => card.querySelector(selector);
      const tile = {
        number, card,
        source: select(".source"), device: select(".device"), usb: select(".usb-device"), usbHint: select(".usb-device-hint"), rtsp: select(".rtsp-url"), demo: select(".demo"),
        recordedLabel: select(".recorded-label"),
        prompt: select(".prompt"), promptDetails: select(".camera-prompt"), video: select("video"),
        placeholder: select(".video-placeholder"), state: select(".camera-state"),
        masks: select(".segmentation-metrics"), response: select(".camera-response"),
        vlmMetrics: select(".vlm-metrics"), vlmMetricsDetail: select(".vlm-metrics-detail"),
        segmentationDetail: select(".segmentation-detail"), temporalProgress: select(".temporal-progress"), play: select(".play-video"),
        observations: [], observationList: select(".recent-observations"),
        observationBlock: select(".observations-block"), lastObservationCount: null,
        videoFps: select(".video-fps"), videoSample: null,
        vlmFreshness: select(".vlm-freshness"), analyzingBadge: select(".analyzing-badge"),
        observationReceivedAt: null, observationFrameAge: 0,
        temporalContext: select(".temporal-context"), temporal: null, temporalHasResult: false,
      };
      select(".camera-title").textContent = `Camera ${number}`;
      tile.video.setAttribute("aria-label", `Camera ${number} live stream`);
      for (const [field, labelClass] of [["source", ".source-label"], ["device", ".device-label"], ["usb", ".usb-label"], ["rtsp", ".rtsp-label"], ["demo", ".demo-label"], ["prompt", ".prompt-label"]]) {
        tile[field].id = `${id}-${field}`;
        select(labelClass).htmlFor = tile[field].id;
      }
      tile.usbHint.id = `${id}-usb-hint`;
      tile.usb.setAttribute("aria-describedby", tile.usbHint.id);
      const saved = savedCameras.get(id);
      if (saved) {
        tile.source.value = saved.source;
        tile.rtsp.value = saved.rtsp || "";
        tile.prompt.value = saved.prompt || "";
      } else if (storeDemoPreset) {
        tile.source.value = "demo";
      }
      const updateSource = () => {
        select(".device-field").hidden = tile.source.value !== "webrtc";
        select(".usb-field").hidden = tile.source.value !== "usb";
        select(".rtsp-field").hidden = tile.source.value !== "rtsp";
        select(".demo-field").hidden = tile.source.value !== "demo";
        tile.recordedLabel.hidden = tile.source.value !== "demo";
        tile.video.setAttribute("aria-label", `Camera ${number} ${tile.source.value === "demo" ? "recorded demo" : "live stream"}`);
        updateDemoNote();
      };
      tile.updateSource = updateSource;
      tile.demo.addEventListener("change", updateDemoNote);
      tile.usb.addEventListener("change", () => {
        const duplicate = tile.usb.value && [...tiles].some(([otherId, other]) => otherId !== id
          && other.source.value === "usb" && other.usb.value === tile.usb.value);
        if (duplicate) tile.usb.value = usbSelections.get(id) || "";
        else usbSelections.set(id, tile.usb.value);
        savedCameras.set(id, { ...savedCameras.get(id), usb: tile.usb.value });
        populateUsbDevices(id);
        setPhase(phase);
      });
      tile.source.addEventListener("change", () => {
        updateSource();
        populateUsbDevices(id);
        setPhase(phase);
        if (tile.source.value === "usb") loadUsbCameras().catch((error) => notice(error.message));
      });
      updateSource();
      tile.video.addEventListener("playing", () => { tile.placeholder.hidden = true; tile.play.hidden = true; });
      for (const event of ["pause", "ended", "emptied", "waiting"]) tile.video.addEventListener(event, () => clearVideoRate(tile));
      tile.play.addEventListener("click", () => tile.video.play().catch(() => notice("Your browser could not play this camera stream.")));
      tiles.set(id, tile);
      $("camera-grid").append(card);
    }
    populateDevices();
    populateUsbDevices();
    populateDemoClips();
    updateDemoNote();
    syncToggles();
    updateModelRole();
  }

  function applyDescriptionMode(updatePresetSettings = true) {
    const mode = descriptionModes[$("description-mode").value] || descriptionModes.fast;
    const current = $("shared-prompt").value.trim();
    const isBuiltIn = !current || current === previousDemoPrompt || Object.values(descriptionModes).some((entry) => entry.prompt === current);
    if (isBuiltIn) $("shared-prompt").value = mode.prompt;
    if (updatePresetSettings) {
      $("max-tokens").value = String(mode.tokens);
      $("image-max-edge").value = String(mode.imageMaxEdge);
    }
    $("description-mode-hint").textContent = mode.hint;
    syncPromptPreset();
  }

  function syncPromptPreset() {
    const prompt = $("shared-prompt").value;
    $("prompt-preset").value = [...$("prompt-preset").options].some((option) => option.value === prompt) ? prompt : "";
  }

  function updateModelRole() {
    document.querySelectorAll(".vlm-insight-label").forEach((label) => {
      label.title = $("model").value || "Server model";
    });
  }

  function safeExternalLink(label, url) {
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) return document.createTextNode(label);
      const link = document.createElement("a");
      link.textContent = label;
      link.href = parsed.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      return link;
    } catch { return document.createTextNode(label); }
  }

  function updateDemoNote() {
    syncSourceControls();
    const hasDemo = [...tiles.values()].some((tile) => tile.source.value === "demo");
    $("demo-note").hidden = !hasDemo;
    const selected = [...tiles.values()].filter((tile) => tile.source.value === "demo")
      .map((tile) => demoCatalog.find((clip) => clip.demo_id === tile.demo.value)).filter(Boolean);
    const sourceRates = [...new Set(selected.map((clip) => clip.source_fps).filter(Number.isFinite))];
    const rateNote = sourceRates.length === 1 ? `source ${sourceRates[0]} FPS.` : "source frame rates are shown beside each clip.";
    $("demo-playback-note").textContent = `Recorded, looping clips · ${rateNote} Playback up to ${demoMaxWidth}×${demoMaxHeight} at ${demoMaxFps} FPS. Independent, unsynchronized recordings.`;
    updatePresentation();
  }

  function populateDemoClips() {
    const available = demoCatalog.filter((clip) => clip.available);
    const automatic = storeDemoPreset && demoCatalog.some((clip) => clip.preferred_for_realtime)
      ? available.filter((clip) => clip.preferred_for_realtime && clip.source_fps === 25) : available;
    for (const [id, tile] of tiles) {
      const previous = tile.demo.value || savedCameras.get(id)?.demo;
      tile.demo.replaceChildren(new Option(available.length ? "Select a recorded clip" : "No store clips available", ""));
      for (const clip of demoCatalog) {
        const option = new Option(`${clip.title || clip.demo_id}${Number.isFinite(clip.source_fps) ? ` · ${clip.source_fps} FPS` : ""}${clip.available ? "" : " (unavailable)"}`, clip.demo_id);
        option.disabled = !clip.available;
        tile.demo.add(option);
      }
      if (previous && demoCatalog.some((clip) => clip.demo_id === previous && clip.available)) tile.demo.value = previous;
      else if (automatic[tile.number - 1]) tile.demo.value = automatic[tile.number - 1].demo_id;
    }
  }

  async function loadDemoCatalog(force = false) {
    if (demoCatalogPromise) return demoCatalogPromise;
    if (!force && demoCatalog.length) return demoCatalog;
    demoCatalogPromise = (async () => {
      const result = await request("/api/demo/videos", {}, 15000);
      demoCatalog = Array.isArray(result.videos) ? result.videos.filter((clip) => typeof clip.demo_id === "string") : [];
      if (Number.isFinite(result.max_fps) && result.max_fps > 0) demoMaxFps = result.max_fps;
      if (Number.isInteger(result.playback_max_width) && result.playback_max_width >= 320 && result.playback_max_width <= 1920
          && Number.isInteger(result.playback_max_height) && result.playback_max_height >= 180 && result.playback_max_height <= 1080
          && result.playback_max_width % 2 === 0 && result.playback_max_height % 2 === 0) {
        demoMaxWidth = result.playback_max_width;
        demoMaxHeight = result.playback_max_height;
      }
      populateDemoClips();
      updateDemoNote();
      return demoCatalog;
    })();
    try { return await demoCatalogPromise; }
    finally { demoCatalogPromise = null; }
  }

  async function applyStoreDemo(isCurrent = () => true) {
    if (phase !== "idle" || loadingDemo) return;
    loadingDemo = true;
    setPhase(phase);
    notice();
    try {
      const installed = (await loadDemoCatalog(true)).filter((clip) => clip.available);
      if (!isCurrent()) return false;
      const preferred = installed.filter((clip) => clip.preferred_for_realtime && clip.source_fps === 25);
      const available = demoCatalog.some((clip) => clip.preferred_for_realtime) ? preferred : installed;
      if (available.length < cameraCount) {
        throw new Error(`The ${cameraCount}-camera store demo needs ${cameraCount} different clips; ${available.length} are available. Add the missing demo clips on the server${available.length >= 2 ? ", or choose 2 cameras" : ""}, then try again.`);
      }
      storeDemoPreset = true;
      rememberCameras();
      $("segmentation-enabled").checked = true;
      $("classes").value = "person, shopping cart, shopping basket, backpack";
      $("process-every").value = "25";
      if (!confidenceEditedByUser) setConfidence(confidenceRecommendation());
      updateConfidenceHint();
      applyDescriptionMode(false);
      for (let number = 1; number <= 4; number += 1) {
        const id = `camera-${number}`;
        const saved = savedCameras.get(id) || {};
        savedCameras.set(id, { source: "demo", device: saved.device || "", usb: saved.usb || "", rtsp: saved.rtsp || "", demo: available[number - 1]?.demo_id || "", prompt: saved.prompt || "" });
      }
      for (const [id, tile] of tiles) {
        tile.source.value = "demo";
        tile.demo.value = savedCameras.get(id).demo;
        tile.prompt.value = savedCameras.get(id).prompt;
        tile.updateSource();
      }
      syncToggles();
      updateDemoNote();
      notice();
      return true;
    } catch (error) {
      notice(`Store demo unavailable. ${error.message}`);
      return false;
    } finally {
      loadingDemo = false;
      setPhase(phase);
    }
  }

  async function enumerateCameras(requestPermission = false, updateSelections = true) {
    if (!navigator.mediaDevices?.enumerateDevices) {
      throw new Error("Browser cameras require HTTPS or localhost. You can also use RTSP camera sources.");
    }
    let probe;
    try {
      if (requestPermission) probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      const seen = new Set();
      devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => {
        if (device.kind !== "videoinput" || !device.deviceId) return false;
        const identity = device.groupId || device.deviceId;
        if (seen.has(identity)) return false;
        seen.add(identity);
        return true;
      });
      if (updateSelections) populateDevices();
    } finally {
      probe?.getTracks().forEach((track) => track.stop());
    }
  }

  async function request(url, options = {}, timeout = 45000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      const text = await response.text();
      let body;
      try { body = text ? JSON.parse(text) : {}; } catch { body = { error: text }; }
      if (!response.ok) {
        const detail = typeof body.error === "string" ? body.error : `Request failed (${response.status}).`;
        throw new Error(response.status === 503 ? `Service unavailable: ${detail}` : detail);
      }
      return body;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The server took too long to respond. Check its connection and try again.");
      throw error;
    } finally { window.clearTimeout(timer); }
  }

  function confidenceRecommendation() {
    // The generic store preset uses its own recommendation; fixed models report theirs.
    if (storeDemoPreset && !segmentationProfile) return 0.1;
    return recommendedConfidence ?? (storeDemoPreset ? 0.1 : 0.25);
  }

  function setConfidence(value) {
    $("confidence").value = String(value);
    $("confidence-value").textContent = `${Math.round(Number($("confidence").value) * 100)}%`;
  }

  function updateConfidenceHint() {
    const percent = Math.round(confidenceRecommendation() * 100);
    $("confidence-hint").textContent = `Suggested: ${percent}%. Lower values may add false masks.`;
  }

  function syncSegmentationProfileControls() {
    const fixed = segmentationProfile !== null;
    if (fixed) {
      $("classes").value = segmentationProfile.classes.join(", ");
      $("seg-imgsz").value = String(segmentationProfile.imgsz);
    }
    const disabled = phase !== "idle" || preparingDemo || !$("segmentation-enabled").checked || fixed;
    $("classes").disabled = disabled;
    $("seg-imgsz").disabled = disabled;
    $("segmentation-profile").hidden = !fixed;
    $("segmentation-profile").textContent = fixed
      ? `Fixed retail model · ${segmentationProfile.imgsz} px` : "";
    $("classes-hint").textContent = fixed
      ? "Fixed by the model." : "Separate names with commas.";
  }

  function applySegmentationProfile(status) {
    if (status.backend === "tensorrt") {
      const classes = status.fixed_classes;
      const imgsz = status.fixed_imgsz;
      if (!Array.isArray(classes) || !classes.length
          || classes.some((value) => typeof value !== "string" || !value.trim())
          || !Number.isInteger(imgsz) || imgsz < 32 || imgsz > 2048) {
        throw new Error("The retail model did not provide its required object list and image size.");
      }
      if (!segmentationProfile) {
        configurableSegmentationSettings = { classes: $("classes").value, imgsz: $("seg-imgsz").value };
      }
      segmentationProfile = { classes: [...classes], imgsz };
      if (![...$("seg-imgsz").options].some((option) => option.value === String(imgsz))) {
        $("seg-imgsz").add(new Option(`${imgsz} px · fixed retail model`, String(imgsz)));
      }
    } else {
      if (segmentationProfile && configurableSegmentationSettings) {
        $("classes").value = configurableSegmentationSettings.classes;
        $("seg-imgsz").value = configurableSegmentationSettings.imgsz;
      }
      segmentationProfile = null;
      configurableSegmentationSettings = null;
    }
    const recommendation = status.recommended_confidence;
    recommendedConfidence = Number.isFinite(recommendation) && recommendation >= 0 && recommendation <= 1
      ? recommendation : null;
    if (!confidenceInitialized) {
      if (!confidenceEditedByUser) setConfidence(confidenceRecommendation());
      confidenceInitialized = true;
    }
    updateConfidenceHint();
    syncSegmentationProfileControls();
  }

  async function checkSegmentation() {
    try {
      const status = await request("/api/segmentation/status", {}, 15000);
      applySegmentationProfile(status);
      const segmentationAvailable = status.available === true;
      const detail = status.error || "The segmentation model is not ready on the server.";
      $("segmentation-status").textContent = segmentationAvailable ? "Ready" : `Unavailable · ${detail}`;
      $("segmentation-status").className = `runtime-status ${segmentationAvailable ? "available" : "unavailable"}`;
      return status;
    } catch (error) {
      $("segmentation-status").textContent = `Unavailable · ${error.message}`;
      $("segmentation-status").className = "runtime-status unavailable";
      return { available: false, error: error.message };
    }
  }

  function selectModel(name) {
    if (![...$("model").options].some((option) => option.value === name)) $("model").add(new Option(name, name));
    $("model").value = name;
    updateModelRole();
  }

  function toggleApiPresets(open) {
    $("api-presets-menu").hidden = !open;
    $("api-presets").setAttribute("aria-expanded", String(open));
  }

  async function discoverModels() {
    const generation = ++modelDiscoveryGeneration;
    const apiBase = $("api-base").value.trim();
    const useServerDefaults = !apiBase || (!apiBaseEdited && apiBase === serverApiBase);
    $("model-status").hidden = false;
    $("model-status").textContent = "Loading models…";
    try {
      // Keep secrets out of discovery URLs. Authenticated custom endpoints can use a manually added model.
      if (!useServerDefaults && $("api-key").value) {
        $("model-status").textContent = "Use + to enter your model name.";
        return null;
      }
      const suffix = !useServerDefaults ? `?${new URLSearchParams({ api_base: apiBase })}` : "";
      const result = await request(`/models${suffix}`, {}, 15000);
      // A previous request must not overwrite an endpoint the user is now editing.
      if (generation !== modelDiscoveryGeneration || apiBase !== $("api-base").value.trim()) return null;
      if (!backendSelectedByUser && useServerDefaults
          && ["openai", "edgellm"].includes(result.backend)) {
        $("vlm-backend").value = result.backend;
      }
      if (useServerDefaults && !apiBaseEdited && typeof result.api_base === "string") {
        serverApiBase = result.api_base;
        $("api-base").value = result.api_base;
      }
      const selected = $("model").value;
      const models = Array.isArray(result.models) ? result.models.filter((model) => typeof model.id === "string" && model.id) : [];
      $("model").replaceChildren(new Option("Use server model", ""));
      for (const model of models) $("model").add(new Option(model.name || model.id, model.id));
      const preferred = selected || (models.find((model) => model.current) || models[0])?.id;
      if (preferred) selectModel(preferred);
      updateModelRole();
      $("model-status").textContent = models.length ? "" : "No models found. Use + to enter a model name.";
      $("model-status").hidden = models.length > 0;
      return result;
    } catch {
      if (generation !== modelDiscoveryGeneration) return null;
      $("model-status").textContent = "Models unavailable. Use + to enter a model name.";
      $("connection-settings").open = true;
      return null;
    }
  }

  function configuration() {
    syncSegmentationProfileControls();
    const segmentation = {
      enabled: $("segmentation-enabled").checked,
      target_fps: segmentationTargetFps,
      imgsz: Number($("seg-imgsz").value),
      confidence: Number($("confidence").value),
      opacity: Number($("opacity").value),
      classes: [...new Set($("classes").value.split(",").map((value) => value.trim()).filter(Boolean))],
    };
    if (segmentation.enabled && !segmentation.classes.length) throw new Error("Enter at least one object class to segment.");
    const vlmEnabled = $("vlm-enabled").checked;
    if (!vlmEnabled && !segmentation.enabled) throw new Error("Enable segmentation or live descriptions before starting.");
    const vlm = {};
    if (vlmEnabled) {
      vlm.backend = $("vlm-backend").value;
      vlm.temperature = 0;
      vlm.image_max_edge = Number($("image-max-edge").value);
      if (!Number.isInteger(vlm.image_max_edge) || (vlm.image_max_edge !== 0 && (vlm.image_max_edge < 128 || vlm.image_max_edge > 2048))) {
        throw new Error("Choose a valid VLM image size.");
      }
      for (const [field, id] of [["model", "model"], ["api_base", "api-base"], ["api_key", "api-key"], ["prompt", "shared-prompt"]]) {
        const value = $(id).value.trim();
        if (value) vlm[field] = value;
      }
      vlm.max_tokens = Number($("max-tokens").value);
      if (!Number.isInteger(vlm.max_tokens) || vlm.max_tokens < 1 || vlm.max_tokens > 32768) throw new Error("Max tokens must be between 1 and 32768.");
    }
    const processEvery = Number($("process-every").value);
    if (!Number.isInteger(processEvery) || processEvery < 1 || processEvery > 3600) throw new Error("The frame interval must be between 1 and 3600.");
    const cameras = [];
    const selectedDevices = new Set();
    const selectedUsbDevices = new Set();
    for (const [id, tile] of tiles) {
      const camera = { camera_id: id, source: tile.source.value };
      if (camera.source === "rtsp") {
        camera.rtsp_url = tile.rtsp.value.trim();
        if (!/^rtsps?:\/\/[^/\s]+/i.test(camera.rtsp_url)) throw new Error(`Camera ${tile.number}: enter an RTSP stream URL.`);
      } else if (camera.source === "usb") {
        const device = usbDevices.find((entry) => entry.device_id === tile.usb.value);
        if (!device) throw new Error(`Camera ${tile.number}: select an available USB camera on the Jetson.`);
        if (selectedUsbDevices.has(device.device_id)) throw new Error(`Camera ${tile.number}: select a different Jetson USB device. Each stream needs its own camera.`);
        selectedUsbDevices.add(device.device_id);
        camera.usb_device_id = device.device_id;
      } else if (camera.source === "demo") {
        const clip = demoCatalog.find((entry) => entry.demo_id === tile.demo.value);
        if (!clip?.available) throw new Error(`Camera ${tile.number}: choose an available recorded store clip.`);
        camera.demo_id = clip.demo_id;
      } else {
        if (!tile.device.value) throw new Error(`Camera ${tile.number}: select a browser camera, or choose an RTSP stream.`);
        if (selectedDevices.has(tile.device.value)) throw new Error(`Camera ${tile.number}: select a different device. Each stream needs its own camera.`);
        selectedDevices.add(tile.device.value);
      }
      if (vlmEnabled && tile.prompt.value.trim()) camera.vlm = { prompt: tile.prompt.value.trim() };
      cameras.push(camera);
    }
    return { cameras, preview_mode: "segmented", vlm_enabled: vlmEnabled, temporal: temporalConfiguration(), segmentation, process_every: processEvery, ...(vlmEnabled ? { vlm } : {}) };
  }

  function assertActive(run) {
    if (run.cancelled || activeRun !== run) throw new DOMException("Camera start cancelled", "AbortError");
  }

  function showCameraStatus(camera) {
    const tile = tiles.get(camera.camera_id);
    if (!tile) return;
    const labels = { waiting: "Waiting for video", streaming: "Streaming", failed: "Camera error", stopped: "Stopped", connecting: "Connecting" };
    badge(tile.state, labels[camera.state] || camera.state || "Waiting", camera.state === "streaming" ? "live" : camera.state === "failed" ? "error" : "pending");
    if (camera.error) {
      tile.response.textContent = camera.error;
      tile.response.title = camera.error;
      tile.response.classList.add("error-text");
      updateObservationFreshness(tile, {});
    } else if (camera.response && camera.response !== "Initializing...") {
      tile.response.textContent = camera.response;
      tile.response.title = camera.response;
      const failed = String(camera.response).startsWith("Error:");
      tile.response.classList.toggle("error-text", failed);
      updateObservationFreshness(tile, failed ? {} : (camera.metrics || {}), true);
    }
    tile.analyzingBadge.hidden = camera.metrics?.is_processing !== true;
    renderTemporalContext(tile, camera, Boolean(camera.response && camera.response !== "Initializing..." && !camera.error && camera.metrics?.last_request_succeeded !== false && !String(camera.response).startsWith("Error:")));
    if (camera.error || ["failed", "stopped"].includes(camera.state)) {
      tile.temporalContext.hidden = true;
      tile.temporalProgress.hidden = true;
    }
    if (["failed", "stopped"].includes(camera.state)) clearRealtimeIndicators(tile);
    // Reconnect snapshots must recover failed USB capture just like live events.
    if (camera.state === "failed" && tile.source.value === "usb" && kioskCameraMode) kioskCameraMode.appliedKey = null;
  }

  function updateObservationFreshness(tile, metrics, snapshot = false) {
    const age = Number.isFinite(metrics.observation_age_ms)
      ? metrics.observation_age_ms
      : (!snapshot ? metrics.last_e2e_latency_ms : null);
    if (Number.isFinite(age) && metrics.last_request_succeeded !== false) {
      tile.observationFrameAge = Math.max(0, age);
      tile.observationReceivedAt = performance.now();
      tile.vlmFreshness.title = "Approximate age since this frame reached the server; camera capture and camera-to-server transfer are excluded.";
      tile.vlmFreshness.hidden = false;
      tile.vlmFreshness.textContent = `Observed ${(tile.observationFrameAge / 1000).toFixed(1)} s ago`;
    } else {
      tile.observationReceivedAt = null;
      tile.vlmFreshness.hidden = true;
    }
  }

  function clearVideoRate(tile) {
    tile.videoSample = null;
    tile.videoFps.textContent = "Video — FPS";
  }

  function clearRealtimeIndicators(tile) {
    clearVideoRate(tile);
    tile.videoFps.hidden = true;
    tile.observationReceivedAt = null;
    tile.observationFrameAge = 0;
    tile.vlmFreshness.textContent = "";
    tile.vlmFreshness.hidden = true;
    tile.analyzingBadge.hidden = true;
    tile.temporalProgress.hidden = true;
  }

  function updateVideoRate(tile, now) {
    const video = tile.video;
    if (video.paused || video.ended || video.readyState < 2 || !video.srcObject) {
      clearVideoRate(tile);
      return;
    }
    const quality = video.getVideoPlaybackQuality?.();
    const decoded = quality?.totalVideoFrames ?? video.webkitDecodedFrameCount;
    if (!Number.isFinite(decoded)) {
      tile.videoFps.textContent = "Video FPS unavailable";
      tile.videoSample = null;
      return;
    }
    if (!tile.videoSample || decoded < tile.videoSample.decoded) {
      tile.videoSample = { decoded, now };
      tile.videoFps.textContent = "Video — FPS";
      return;
    }
    const elapsed = now - tile.videoSample.now;
    if (elapsed >= 1000) {
      const fps = (decoded - tile.videoSample.decoded) * 1000 / elapsed;
      tile.videoFps.textContent = `Video ${fps.toFixed(1)} FPS`;
      tile.videoFps.title = "Browser decoded-frame rate over the last second, separate from mask inference FPS.";
      tile.videoSample = { decoded, now };
    }
  }

  function updateLiveIndicators(run) {
    if (activeRun !== run || run.cancelled) return;
    const now = performance.now();
    for (const tile of tiles.values()) {
      updateVideoRate(tile, now);
      if (tile.observationReceivedAt !== null) {
        const age = tile.observationFrameAge + now - tile.observationReceivedAt;
        tile.vlmFreshness.textContent = `Observed ${(age / 1000).toFixed(1)} s ago`;
        tile.vlmFreshness.hidden = false;
      }
    }
  }

  function measuredTime(value) {
    return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
  }

  function renderVlmMetrics(tile, metrics) {
    const items = [];
    const fullResult = metrics.last_e2e_latency_ms;
    items.push(Number.isFinite(fullResult) ? `Full result ${measuredTime(fullResult)}` : "Full result not measured");
    if (Number.isFinite(metrics.last_queue_ms)) items.push(`Queue ${measuredTime(metrics.last_queue_ms)}`);
    if (Number.isFinite(metrics.last_latency_ms)) items.push(`Request ${measuredTime(metrics.last_latency_ms)}`);
    tile.vlmMetrics.textContent = Number.isFinite(fullResult) ? `E2E ${measuredTime(fullResult)}` : "";
    tile.vlmMetricsDetail.textContent = items.join(" · ");
    tile.vlmMetrics.title = "Full result: frame received at the server to the complete VLM response, including preparation and queue time. Excludes camera capture, camera-to-server transfer, and browser display. Request: JPEG preparation and model request duration.";
    tile.vlmMetricsDetail.title = tile.vlmMetrics.title;
  }

  function recordObservation(tile, text, metrics) {
    const count = metrics.total_inferences;
    if (Number.isFinite(count) && tile.lastObservationCount === count) return;
    tile.lastObservationCount = Number.isFinite(count) ? count : null;
    tile.observations.unshift({ text: String(text), received: new Date() });
    tile.observations.length = Math.min(tile.observations.length, 4);
    tile.observationList.replaceChildren();
    // The newest observation is already displayed above; retain three earlier ones.
    for (const observation of tile.observations.slice(1)) {
      const item = document.createElement("li");
      const time = document.createElement("time");
      time.dateTime = observation.received.toISOString();
      time.textContent = observation.received.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      time.title = "Description received at this local time";
      const content = document.createElement("span");
      content.textContent = observation.text;
      item.append(time, content);
      tile.observationList.append(item);
    }
    tile.observationBlock.hidden = tile.observations.length < 2;
  }

  function handleEvent(run, event) {
    if (activeRun !== run || run.cancelled || event.session_id !== run.id) return;
    if (event.type === "session_status") {
      (event.cameras || []).forEach(showCameraStatus);
      return;
    }
    const tile = tiles.get(event.camera_id);
    if (!tile) return;
    if (event.type === "camera_status") {
      showCameraStatus(event);
    } else if (event.type === "temporal_status") {
      renderTemporalContext(tile, event);
    } else if (event.type === "vlm_status") {
      tile.analyzingBadge.hidden = event.is_processing !== true;
    } else if (event.type === "vlm_response") {
      tile.response.textContent = event.text || "No description returned.";
      tile.response.title = tile.response.textContent;
      tile.response.classList.toggle("error-text", String(event.text).startsWith("Error:"));
      const metrics = event.metrics || {};
      renderVlmMetrics(tile, metrics);
      tile.analyzingBadge.hidden = true;
      if (event.text && !String(event.text).startsWith("Error:")) {
        renderTemporalContext(tile, metrics, true);
        recordObservation(tile, event.text, metrics);
        updateObservationFreshness(tile, metrics);
      } else {
        tile.temporalContext.hidden = true;
        tile.temporalProgress.hidden = true;
        updateObservationFreshness(tile, {});
      }
    } else if (event.type === "segmentation_result") {
      const metrics = event.metrics || {};
      const error = event.error || metrics.error;
      if (error || ["failed", "error", "unavailable"].includes(event.state)) {
        tile.masks.textContent = error || "Segmentation unavailable";
        tile.masks.classList.add("error-text");
        tile.segmentationDetail.textContent = "";
        return;
      }
      const values = [Number.isFinite(metrics.fps) && metrics.fps > 0 ? `Analyzed ${metrics.fps.toFixed(1)} FPS` : "Analysis FPS pending"];
      values.push(`${metrics.mask_count ?? 0} masks detected`);
      if (Number.isFinite(metrics.inference_ms)) values.push(`Inference ${Math.round(metrics.inference_ms)} ms`);
      tile.masks.textContent = Number.isFinite(metrics.fps) && metrics.fps > 0 ? `YOLO ${metrics.fps.toFixed(1)} FPS` : "YOLO — FPS";
      tile.segmentationDetail.textContent = values.join(" · ");
      tile.masks.title = "Frames analyzed per second, including frames with no objects detected. Mask count is for the latest analyzed frame.";
      tile.segmentationDetail.title = tile.masks.title;
      tile.masks.classList.remove("error-text");
    }
  }

  function connectResults(run, reconnect = false) {
    return new Promise((resolve, reject) => {
      assertActive(run);
      const url = new URL(`${apiRoot}/${encodeURIComponent(run.id)}/ws`, window.location.href);
      url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(url);
      run.ws = ws;
      let opened = false;
      const timer = window.setTimeout(() => { ws.close(); reject(new Error("Could not connect to camera results.")); }, 10000);
      ws.addEventListener("open", () => {
        window.clearTimeout(timer);
        if (run.cancelled) { ws.close(); reject(new DOMException("Cancelled", "AbortError")); return; }
        opened = true;
        if (reconnect) notice();
        resolve();
      });
      ws.addEventListener("message", (message) => {
        try { handleEvent(run, JSON.parse(message.data)); } catch { /* Ignore malformed status events. */ }
      });
      ws.addEventListener("error", () => { if (!opened) { window.clearTimeout(timer); reject(new Error("Could not connect to camera results.")); } });
      ws.addEventListener("close", () => {
        window.clearTimeout(timer);
        if (!opened) reject(new Error("The camera results connection closed."));
        if (run.cancelled || activeRun !== run || !opened || run.ws !== ws) return;
        for (const tile of tiles.values()) tile.analyzingBadge.hidden = true;
        notice("Camera results disconnected. Reconnecting…");
        run.reconnectTimer = window.setTimeout(() => {
          if (run.cancelled || activeRun !== run) return;
          if (kioskAutomatic) reconnectKiosk(run);
          else connectResults(run, true).catch(() => notice("Camera results are unavailable. Stop and start the cameras to reconnect."));
        }, 1500);
      });
    });
  }

  async function reconnectKiosk(run) {
    if (run.cancelled || activeRun !== run) return;
    try {
      const response = await fetch(`${apiRoot}/${encodeURIComponent(run.id)}`, { signal: AbortSignal.timeout(5000) });
      if (run.cancelled || activeRun !== run) return;
      if (response.status === 404 || response.status === 410) {
        // Only recreate after the server confirms the old session is gone.
        // A temporary WebSocket outage must keep the existing session.
        run.cancelled = true;
        stopLocal(run);
        window.location.reload();
        return;
      }
      if (!response.ok) throw new Error("Waiting for the demo server");
      await connectResults(run, true);
      return;
    } catch { /* Keep waiting through a server restart. Stop cancels this retry. */ }
    if (!run.cancelled && activeRun === run) {
      run.reconnectTimer = window.setTimeout(() => reconnectKiosk(run), 2000);
    }
  }

  function waitForIce(peer, run) {
    if (peer.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve, reject) => {
      const done = (error) => {
        window.clearTimeout(timer);
        peer.removeEventListener("icegatheringstatechange", changed);
        error ? reject(error) : resolve();
      };
      const changed = () => {
        if (run.cancelled) done(new DOMException("Cancelled", "AbortError"));
        else if (peer.iceGatheringState === "complete") done();
      };
      const timer = window.setTimeout(() => done(new Error("Camera connection setup timed out.")), 12000);
      peer.addEventListener("icegatheringstatechange", changed);
      changed();
    });
  }

  async function connectCamera(run, camera) {
    assertActive(run);
    const tile = tiles.get(camera.camera_id);
    const peer = new RTCPeerConnection({ iceServers: [] });
    run.peers.set(camera.camera_id, peer);
    badge(tile.state, "Connecting", "pending");
    peer.addEventListener("track", (event) => {
      if (run.cancelled || activeRun !== run) return;
      tile.video.srcObject = event.streams[0] || new MediaStream([event.track]);
      tile.video.play().catch(() => { tile.play.hidden = false; });
    });
    peer.addEventListener("connectionstatechange", () => {
      if (run.cancelled || activeRun !== run) return;
      if (peer.connectionState === "connected") badge(tile.state, "Streaming", "live");
      else if (["failed", "disconnected"].includes(peer.connectionState)) {
        badge(tile.state, peer.connectionState === "failed" ? "Connection failed" : "Reconnecting", peer.connectionState === "failed" ? "error" : "pending");
        if (peer.connectionState === "failed") notice(`Camera ${tile.number} disconnected. Other cameras can continue. Stop and start to reconnect it.`);
      }
    });
    if (camera.source === "webrtc") {
      for (const track of run.streams.get(camera.camera_id).getVideoTracks()) peer.addTrack(track, run.streams.get(camera.camera_id));
    } else peer.addTransceiver("video", { direction: "recvonly" });
    await peer.setLocalDescription(await peer.createOffer());
    await waitForIce(peer, run);
    assertActive(run);
    const answer = await request(`${apiRoot}/${encodeURIComponent(run.id)}/cameras/${camera.camera_id}/offer`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: peer.localDescription.sdp, type: "offer" }),
    });
    assertActive(run);
    await peer.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
    await new Promise((resolve, reject) => {
      const changed = () => {
        if (run.cancelled || ["failed", "closed"].includes(peer.connectionState)) finish(new Error(`Camera ${tile.number} could not connect.`));
        else if (peer.connectionState === "connected") finish();
      };
      const finish = (error) => {
        window.clearTimeout(timer);
        peer.removeEventListener("connectionstatechange", changed);
        error ? reject(error) : resolve();
      };
      const timer = window.setTimeout(() => finish(new Error(`Camera ${tile.number} connection timed out. Check that your browser can reach the Jetson on this network.`)), 15000);
      peer.addEventListener("connectionstatechange", changed);
      changed();
    });
  }

  function stopLocal(run) {
    window.clearTimeout(run.reconnectTimer);
    window.clearInterval(run.indicatorTimer);
    run.ws?.close();
    for (const stream of run.streams.values()) stream.getTracks().forEach((track) => track.stop());
    for (const peer of run.peers.values()) peer.close();
    for (const tile of tiles.values()) {
      tile.video.srcObject = null;
      tile.placeholder.hidden = false;
      tile.play.hidden = true;
      clearRealtimeIndicators(tile);
      tile.temporalContext.hidden = true;
      tile.temporalHasResult = false;
    }
  }

  async function cleanup(run) {
    if (run.cleanupPromise) return run.cleanupPromise;
    run.cancelled = true;
    stopLocal(run);
    run.cleanupPromise = (async () => {
      if (run.creationAttempted) {
        try {
          const response = await fetch(`${apiRoot}/${encodeURIComponent(run.id)}`, { method: "DELETE", signal: AbortSignal.timeout(15000) });
          if (!response.ok && response.status !== 404) throw new Error("Session cleanup failed");
        } catch {
          run.cleanupError = "Local cameras stopped. The server could not confirm session cleanup; reconnect to the server before starting again.";
          notice(run.cleanupError);
        }
      }
      for (const tile of tiles.values()) badge(tile.state, "Stopped");
    })();
    return run.cleanupPromise;
  }

  async function performStart(run) {
    if ($("segmentation-enabled").checked) {
      const status = await checkSegmentation();
      assertActive(run);
      if (!status.available) throw new Error(`Segmentation is unavailable: ${status.error || "the model is not ready"}. Disable segmentation to use live descriptions only, or make the model available on the server.`);
    }
    if ([...tiles.values()].some((tile) => tile.source.value === "usb")) await loadUsbCameras(true);
    assertActive(run);
    const browserTiles = [...tiles.values()].filter((tile) => tile.source.value === "webrtc");
    if (browserTiles.some((tile) => !tile.device.value)) await enumerateCameras(true);
    assertActive(run);
    const config = configuration();
    for (const [id, tile] of tiles) {
      clearRealtimeIndicators(tile);
      tile.videoFps.hidden = false;
      tile.response.classList.remove("error-text");
      tile.response.textContent = config.vlm_enabled ? "Waiting for result…" : "VLM off";
      tile.vlmMetrics.textContent = "";
      tile.vlmMetricsDetail.textContent = "";
      tile.segmentationDetail.textContent = "";
      tile.temporal = config.temporal;
      tile.temporalHasResult = false;
      renderTemporalContext(tile);
      tile.observations = [];
      tile.lastObservationCount = null;
      tile.observationList.replaceChildren();
      tile.observationBlock.hidden = true;
      tile.masks.classList.remove("error-text");
      tile.masks.textContent = config.segmentation.enabled ? "YOLO — FPS" : "Masks off";
      if (tile.source.value === "webrtc") {
        const stream = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { exact: tile.device.value }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } }, audio: false });
        run.streams.set(id, stream);
        if (run.cancelled) stream.getTracks().forEach((track) => track.stop());
        assertActive(run);
      }
    }
    run.creationAttempted = true;
    const session = await request(apiRoot, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...config, session_id: run.id }),
    });
    assertActive(run);
    session.cameras.forEach(showCameraStatus);
    run.indicatorTimer = window.setInterval(() => updateLiveIndicators(run), 250);
    await connectResults(run);
    // Await every attempt before cleanup, so no late negotiation can recreate resources.
    const connections = await Promise.allSettled(config.cameras.map((camera) => connectCamera(run, camera)));
    const failed = connections.find((result) => result.status === "rejected");
    if (failed) throw failed.reason;
    assertActive(run);
  }

  async function start() {
    if (activeRun || phase !== "idle") return;
    notice();
    const sessionId = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const run = { id: `cameras-${sessionId}`, peers: new Map(), streams: new Map(), cancelled: false, creationAttempted: false };
    activeRun = run;
    setPhase("starting");
    run.startTask = performStart(run);
    try {
      await run.startTask;
      if (!run.cancelled) {
        setPhase("running");
      }
    } catch (error) {
      const wasCancelled = run.cancelled;
      await cleanup(run);
      if (!wasCancelled) {
        const permissionMessage = error.name === "NotAllowedError" ? "Camera access was denied. Allow camera access in your browser, or choose RTSP sources." : error.name === "NotReadableError" ? "A camera is busy or cannot be opened. Close other apps using it and try again." : error.message;
        notice(run.cleanupError ? `${permissionMessage} ${run.cleanupError}` : permissionMessage);
      }
      if (activeRun === run) { activeRun = null; setPhase("idle"); }
    }
    return run;
  }

  async function stop() {
    cancelKioskAutomation();
    await stopRun();
    setPhase(phase);
  }

  async function stopRun() {
    const run = activeRun;
    if (!run) return;
    run.cancelled = true;
    setPhase("stopping");
    stopLocal(run);
    await run.startTask.catch(() => {});
    await cleanup(run);
    if (activeRun === run) { activeRun = null; setPhase("idle"); }
  }

  function demoLaunchOptions() {
    const url = new URL(window.location.href);
    const query = url.searchParams;
    if (!query.has("demo")) return null;
    for (const key of ["demo", "cameras", "autostart", "temporal", "kiosk"]) {
      if (query.getAll(key).length > 1) throw new Error("Invalid demo link. Open Multi-stream to start manually.");
    }
    const kiosk = query.get("kiosk") === "1";
    const source = query.get("demo");
    const cameras = query.get("cameras") ?? (kiosk ? "auto" : source === "usb" ? "2" : "4");
    const automatic = query.get("autostart") ?? "0";
    const temporal = query.get("temporal") ?? "1";
    if (!(kiosk ? ["auto", "store", "usb"] : ["store", "usb"]).includes(source)
        || !(kiosk ? ["auto", "2", "4"] : ["2", "4"]).includes(cameras)
        || !["0", "1"].includes(automatic) || !["0", "1"].includes(temporal)
        || ![null, "0", "1"].includes(query.get("kiosk"))) {
      throw new Error("Invalid demo link. Open Multi-stream to start manually.");
    }
    // Ordinary links are one-shot. The upstream kiosk profile explicitly opts
    // into persistent autostart so a server restart can recover the display.
    if (!kiosk) {
      query.delete("autostart");
      window.history.replaceState(window.history.state, "", url);
    }
    return { source, cameras: cameras === "auto" ? null : Number(cameras), automatic: automatic === "1", temporal: temporal === "1", kiosk };
  }

  async function applyDemoLaunch(options, readiness) {
    let automatic = false;
    const generation = ++kioskLaunchGeneration;
    const current = () => generation === kioskLaunchGeneration;
    kioskAutomatic = options.kiosk && options.automatic;
    const watchCameras = kioskAutomatic && options.source !== "store";
    if (watchCameras) kioskCameraMode = { source: options.source, cameras: options.cameras, ready: false };
    preparingDemo = true;
    setPhase(phase);
    notice("Preparing demo…");
    $("vlm-enabled").checked = true;
    $("segmentation-enabled").checked = true;
    $("description-mode").value = "fast";
    applyDescriptionMode();
    $("process-every").value = "25";
    $("video-understanding").value = options.temporal ? "temporal" : "single";
    $("temporal-window").value = "2";
    syncToggles();
    try {
      const [segmentation, models] = await readiness;
      if (!current()) return;
      if (!segmentation.available) throw new Error("Object masks are unavailable. Check the demo launcher and try again.");
      if (!Array.isArray(models?.models) || !models.models.length) throw new Error("The VLM is unavailable. Check the demo launcher and try again.");
      if (watchCameras) kioskCameraMode.ready = true;
      else {
        if (options.kiosk && options.source !== "store") {
          try { await enumerateCameras(true); }
          catch (error) {
            if (options.source !== "auto" || error.name !== "NotFoundError") throw error;
            devices = [];
          }
          if (!current()) return;
          if (options.source === "auto") options.source = devices.length >= 2 ? "usb" : "store";
          if (options.source === "usb") {
            options.cameras ??= devices.length >= 4 ? 4 : 2;
            if (devices.length < options.cameras) throw new Error(`This kiosk needs ${options.cameras} different cameras; found ${devices.length}.`);
          }
        }
        options.cameras ??= options.source === "usb" ? 2 : 4;
        renderCameras(options.cameras);
        if (options.source === "store") {
          if (!await applyStoreDemo(current) || !current()) return;
          presentationPreferred = true;
          automatic = options.automatic;
          notice(automatic ? "" : "Store demo ready. Select Start.");
        } else {
          for (const tile of tiles.values()) {
            tile.source.value = "webrtc";
            tile.updateSource();
          }
          automatic = kioskAutomatic;
          presentationPreferred = automatic;
          notice(automatic ? "" : "USB demo ready. Select Start and allow camera access.");
        }
      }
    } catch (error) {
      if (current()) notice(error.message);
    } finally {
      if (current()) {
        preparingDemo = false;
        setPhase(phase);
      }
    }
    if (!current()) return;
    if (watchCameras) await refreshUsbCatalog(true);
    else if (automatic) await start();
  }

  $("two-cameras").addEventListener("click", () => renderCameras(2));
  $("four-cameras").addEventListener("click", () => renderCameras(4));
  $("start").addEventListener("click", () => {
    cancelKioskAutomation();
    start();
  });
  // An operator changing idle camera inputs takes control from kiosk automation.
  for (const eventName of ["click", "change"]) $("multi-stream-view").addEventListener(eventName, (event) => {
    if (phase === "idle" && event.target.closest("#two-cameras, #four-cameras, #source-webcam, #source-rtsp, #enable-cameras, #usb-cameras, .source-controls")) {
      cancelKioskAutomation();
      // Let the target handler record a new choice (including an explicit clear)
      // before a phase update rebuilds the USB options from remembered choices.
      if (event.type !== "change" || !event.target.matches(".source, .usb-device")) setPhase(phase);
    }
  }, true);
  $("usb-cameras").addEventListener("click", applyUsbCameras);
  $("stop").addEventListener("click", stop);
  $("focus-stop").addEventListener("click", stop);
  $("source-webcam").addEventListener("click", () => selectSource("webcam"));
  $("source-rtsp").addEventListener("click", () => selectSource("rtsp"));
  for (const id of ["source-webcam", "source-rtsp"]) $(id).addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const target = event.key === "Home" ? "source-webcam" : event.key === "End" ? "source-rtsp" : id === "source-webcam" ? "source-rtsp" : "source-webcam";
    $(target).click();
    $(target).focus();
  });
  $("presentation-toggle").addEventListener("click", () => {
    presentationPreferred = !presentationPreferred;
    updatePresentation();
    document.querySelector(".stream-toolbar").scrollIntoView({ block: "start", behavior: "instant" });
  });
  $("segmentation-enabled").addEventListener("change", syncToggles);
  $("vlm-enabled").addEventListener("change", syncToggles);
  $("video-understanding").addEventListener("change", syncTemporalControls);
  $("temporal-window").addEventListener("change", syncTemporalControls);
  $("refresh-models").addEventListener("click", discoverModels);
  $("add-model").addEventListener("click", () => {
    const name = window.prompt("Enter model name")?.trim();
    if (name) selectModel(name);
  });
  $("api-base").addEventListener("input", () => {
    apiBaseEdited = true;
    modelDiscoveryGeneration += 1;
    $("model-status").hidden = true;
  });
  $("api-base").addEventListener("change", () => {
    $("model").value = "";
    updateModelRole();
    discoverModels();
  });
  $("api-presets").addEventListener("click", () => toggleApiPresets($("api-presets-menu").hidden));
  $("api-presets-menu").addEventListener("click", (event) => {
    const item = event.target.closest("[data-api-url]");
    if (!item) return;
    apiBaseEdited = true;
    $("api-base").value = item.dataset.apiUrl;
    $("vlm-backend").value = "openai";
    backendSelectedByUser = true;
    $("model").value = "";
    toggleApiPresets(false);
    $("api-presets").focus();
    discoverModels();
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".api-preset-control")) toggleApiPresets(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("api-presets-menu").hidden) {
      toggleApiPresets(false);
      $("api-presets").focus();
    }
  });
  $("prompt-preset").addEventListener("change", () => {
    if (!$("prompt-preset").value) return;
    $("shared-prompt").value = $("prompt-preset").value;
    $("shared-prompt").classList.add("applied");
    window.setTimeout(() => $("shared-prompt").classList.remove("applied"), 1000);
  });
  $("shared-prompt").addEventListener("input", syncPromptPreset);
  $("vlm-backend").addEventListener("change", () => { backendSelectedByUser = true; });
  $("description-mode").addEventListener("change", () => applyDescriptionMode());
  $("model").addEventListener("input", updateModelRole);
  for (const event of ["input", "change"]) $("confidence").addEventListener(event, () => {
    confidenceEditedByUser = true;
    $("confidence-value").textContent = `${Math.round(Number($("confidence").value) * 100)}%`;
  });
  $("opacity").addEventListener("input", () => { $("opacity-value").textContent = `${Math.round(Number($("opacity").value) * 100)}%`; });
  $("enable-cameras").addEventListener("click", async () => {
    notice();
    enumerating = true;
    setPhase(phase);
    try {
      await enumerateCameras(true);
      selectSource("webcam", true);
      if (devices.length < cameraCount) notice(`Found ${devices.length} browser camera${devices.length === 1 ? "" : "s"}. You can combine browser cameras and RTSP streams.`);
    } catch (error) { notice(error.message); }
    finally { enumerating = false; setPhase(phase); }
  });
  navigator.mediaDevices?.addEventListener("devicechange", () => {
    enumerateCameras(false, false).then(() => {
      if (phase === "idle") populateDevices();
    }).catch(() => {});
    refreshUsbCatalog(true);
  });
  window.addEventListener("pagehide", () => {
    usbRefreshPaused = true;
    window.clearTimeout(usbRefreshTimer);
  });
  window.addEventListener("pageshow", () => {
    usbRefreshPaused = false;
    scheduleUsbRefresh();
  });
  window.addEventListener("beforeunload", () => {
    if (!activeRun) return;
    activeRun.cancelled = true;
    stopLocal(activeRun);
    if (activeRun.creationAttempted) fetch(`${apiRoot}/${encodeURIComponent(activeRun.id)}`, { method: "DELETE", keepalive: true }).catch(() => {});
  });
  const header = document.querySelector(".header");
  const updateHeaderHeight = () => document.body.style.setProperty("--webui-header-height", `${header.getBoundingClientRect().height}px`);
  new ResizeObserver(updateHeaderHeight).observe(header);
  updateHeaderHeight();
  applyDescriptionMode();
  renderCameras(2);
  enumerateCameras().catch(() => {});
  loadUsbCameras().catch(() => {});
  scheduleUsbRefresh();
  const readiness = Promise.all([checkSegmentation(), discoverModels(), loadDemoCatalog().catch(() => [])]);
  try {
    const launch = demoLaunchOptions();
    if (launch) applyDemoLaunch(launch, readiness);
  } catch (error) { notice(error.message); }
})();

// Read the existing system monitor independently of camera sessions.
(() => {
  const $ = (id) => document.getElementById(id);
  const card = $("systemStatsCard");
  if (!card) return;
  const fields = [
    { value: "gpuUtil", bar: "gpuUtilBar", canvas: "gpuUtilSparkline", percent: "gpu_percent", history: "gpu_util", color: "#4CAF50" },
    { value: "vramUsage", bar: "vramBar", canvas: "vramSparkline", percent: "vram_percent", used: "vram_used_gb", total: "vram_total_gb", history: "vram_used", color: "#9C27B0" },
    { value: "cpuUtil", bar: "cpuBar", canvas: "cpuSparkline", percent: "cpu_percent", history: "cpu_util", color: "#2196F3" },
    { value: "ramUsage", bar: "ramBar", canvas: "ramSparkline", percent: "ram_percent", used: "ram_used_gb", total: "ram_total_gb", history: "ram_used", color: "#FF9800" },
  ];
  let socket = null;
  let reconnectTimer = null;
  let watchdog = null;
  let reconnectDelay = 1000;
  let suspended = false;
  let latestStats = null;
  let lastReceived = 0;
  let lastRendered = 0;
  let frame = null;
  let renderTimer = null;
  let intervalMs = 250;
  try {
    const saved = Number(localStorage.getItem("gpuUpdateInterval"));
    if (saved >= 0.1 && saved <= 5) intervalMs = saved * 1000;
  } catch { /* Monitoring still works when browser storage is unavailable. */ }

  function drawSparkline(canvas, values, color) {
    const context = canvas.getContext("2d");
    if (!context) return;
    const width = Math.max(1, canvas.clientWidth);
    const height = Math.max(1, canvas.clientHeight);
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    if (!Array.isArray(values) || !values.length) return;
    const data = values.slice(-120).map((value) => Number.isFinite(value) ? Math.max(0, value) : 0);
    const maximum = Math.max(...data, 1);
    const step = width / (data.length - 1 || 1);
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.beginPath();
    data.forEach((value, index) => {
      const x = index * step;
      const y = height - (value / maximum) * height;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    context.lineTo(width, height);
    context.lineTo(0, height);
    context.closePath();
    context.fillStyle = `${color}20`;
    context.fill();
  }

  function productImage(stats) {
    const product = typeof stats.product_name === "string" ? stats.product_name : "";
    const board = typeof stats.board_name === "string" ? stats.board_name : "";
    const gpu = typeof stats.gpu_name === "string" ? stats.gpu_name : "";
    for (const [name, file] of [["Thor", "jetson-agx-thor-devkit_256px.png"], ["Orin Nano", "jetson-orin-nano-devkit_217px.png"], ["Orin", "jetson-agx-orin-devkit_256px.png"], ["DGX Spark", "dgx-spark_256px.png"]]) {
      if (board.includes(name) || product.includes(name)) return `/images/${file}`;
    }
    if (/nvidia|geforce|rtx|gtx|quadro|tesla/i.test(gpu)) return "/images/m48-gpu-chip-text-256px-grn.png";
    const type = product.includes("MacBook") ? "laptop" : "workstation";
    const theme = document.body.classList.contains("light-theme") ? "blk" : "wht";
    return `/images/m48-${type}-256px-${theme}.png`;
  }

  function renderHeader(stats) {
    const text = (key, fallback = "") => typeof stats[key] === "string" ? stats[key] : fallback;
    const header = $("systemInfoHeader");
    const host = document.createElement("code");
    host.textContent = text("hostname", "System");
    const board = text("board_name") || text("product_name");
    const cpu = text("cpu_model", "Unknown CPU").replace(/\s*@\s*[\d.]+\s*[GM]Hz/gi, "").trim();
    const gpu = text("gpu_name", "Unknown GPU");
    header.replaceChildren();
    if (board) {
      const name = document.createElement("b");
      name.textContent = board;
      header.append(name, document.createTextNode(" ("), host, document.createTextNode(")"));
    } else header.append(host, document.createTextNode(` with ${cpu}`));
    header.append(document.createElement("br"), document.createTextNode(gpu.includes("GPU") ? gpu : `${gpu} GPU`));
    const src = productImage(stats);
    const image = $("systemProductImage");
    if (image.getAttribute("src") !== src) {
      image.src = src;
      image.style.display = "block";
    }
    card.style.setProperty("--system-bg-image", `url('${src}')`);
  }

  function render(stats) {
    renderHeader(stats);
    for (const field of fields) {
      const value = $(field.value);
      const available = field.used
        ? Number.isFinite(stats[field.used]) && Number.isFinite(stats[field.total])
        : Number.isFinite(stats[field.percent]);
      value.replaceChildren();
      if (!available) value.textContent = "N/A";
      else if (field.used) {
        const total = document.createElement("span");
        total.className = "stat-value-denominator";
        total.textContent = `/${stats[field.total].toFixed(1)} GB`;
        value.append(document.createTextNode(stats[field.used].toFixed(1)), total);
      } else value.textContent = `${stats[field.percent].toFixed(1)}%`;
      const percent = available && Number.isFinite(stats[field.percent]) ? stats[field.percent] : 0;
      $(field.bar).style.width = `${Math.max(0, Math.min(100, percent))}%`;
      $(field.bar).style.background = available ? field.color : "#666";
      drawSparkline($(field.canvas), available ? stats.history?.[field.history] : [], field.color);
    }
    lastRendered = performance.now();
  }

  function scheduleRender() {
    window.clearTimeout(renderTimer);
    renderTimer = null;
    if (frame !== null || !latestStats || document.hidden) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      if (latestStats) render(latestStats);
    });
  }

  function unavailable(message) {
    window.clearTimeout(renderTimer);
    renderTimer = null;
    latestStats = null;
    $("systemInfoHeader").textContent = message;
    for (const field of fields) {
      $(field.value).textContent = "N/A";
      $(field.bar).style.width = "0%";
      drawSparkline($(field.canvas), [], field.color);
    }
  }

  function connect() {
    if (suspended || socket) return;
    const url = new URL("/ws?stream_id=multi-stats", window.location.href);
    url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const current = new WebSocket(url);
    socket = current;
    lastReceived = Date.now();
    watchdog = window.setInterval(() => {
      if (Date.now() - lastReceived > 15000) {
        unavailable("System stats unavailable. Reconnecting…");
        current.close();
      }
    }, 5000);
    current.addEventListener("message", (event) => {
      if (socket !== current || suspended) return;
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message?.type !== "gpu_stats" || !message.stats || typeof message.stats !== "object" || Array.isArray(message.stats)) return;
      const firstUpdate = latestStats === null;
      latestStats = message.stats;
      lastReceived = Date.now();
      reconnectDelay = 1000;
      const remaining = intervalMs - (performance.now() - lastRendered);
      if (firstUpdate || remaining <= 0) scheduleRender();
      else if (renderTimer === null) renderTimer = window.setTimeout(scheduleRender, remaining);
    });
    current.addEventListener("close", () => {
      if (socket !== current) return;
      socket = null;
      window.clearInterval(watchdog);
      if (suspended) return;
      unavailable("System stats unavailable. Reconnecting…");
      reconnectTimer = window.setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    });
    current.addEventListener("error", () => current.close());
  }

  $("systemProductImage").addEventListener("error", () => { $("systemProductImage").style.display = "none"; });
  new ResizeObserver(scheduleRender).observe(card);
  new MutationObserver(scheduleRender).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  document.addEventListener("visibilitychange", scheduleRender);
  window.addEventListener("pagehide", () => {
    suspended = true;
    window.clearTimeout(reconnectTimer);
    window.clearTimeout(renderTimer);
    renderTimer = null;
    window.clearInterval(watchdog);
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    const current = socket;
    socket = null;
    current?.close();
  });
  window.addEventListener("pageshow", () => {
    suspended = false;
    connect();
  });
  connect();
})();
