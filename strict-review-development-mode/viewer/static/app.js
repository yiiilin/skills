const POLL_INTERVAL_MS = 2000;
const KNOWN_STATUSES = [
  "blocked",
  "ready",
  "active",
  "implemented",
  "review-queued",
  "in-review",
  "changes-requested",
  "done",
];
const KNOWN_STATUS_SET = new Set(KNOWN_STATUSES);
const STATUS_META = {
  blocked: { label: "Blocked", className: "status-blocked", queueKey: "blocked" },
  ready: { label: "Ready", className: "status-ready", queueKey: "ready" },
  active: { label: "Active", className: "status-active", queueKey: "active" },
  implemented: { label: "Implemented", className: "status-implemented", queueKey: "implemented" },
  "review-queued": { label: "Review Queue", className: "status-review-queued", queueKey: "review_queued" },
  "in-review": { label: "In Review", className: "status-in-review", queueKey: "in_review" },
  "changes-requested": { label: "Changes Requested", className: "status-changes-requested", queueKey: "changes_requested" },
  done: { label: "Done", className: "status-done", queueKey: "done" },
  unknown: { label: "Unknown Status", className: "status-unknown", queueKey: "unknown" },
};

const state = {
  snapshot: null,
  selectedItemId: null,
  pollHandle: null,
};

const dom = {};

document.addEventListener("DOMContentLoaded", function () {
  bindDom();
  state.selectedItemId = readHashSelectedItemId();

  if (dom.refreshButton) {
    dom.refreshButton.addEventListener("click", function () {
      refreshSnapshot();
    });
  }

  window.addEventListener("hashchange", function () {
    state.selectedItemId = readHashSelectedItemId();
    if (state.snapshot) {
      state.selectedItemId = pickSelectedItemId(state.snapshot, state.selectedItemId);
      renderSnapshot(state.snapshot);
    }
  });

  refreshSnapshot();
  state.pollHandle = setInterval(refreshSnapshot, POLL_INTERVAL_MS);
});

function bindDom() {
  dom.overview = document.getElementById("overview");
  dom.dag = document.getElementById("dag");
  dom.queues = document.getElementById("queues");
  dom.itemDetail = document.getElementById("item-detail");
  dom.mermaidReference = document.getElementById("mermaid-reference");
  dom.warnings = document.getElementById("warnings");
  dom.warningsEmpty = document.getElementById("warnings-empty");
  dom.warningsList = document.getElementById("warnings-list");
  dom.staleBanner = document.getElementById("stale-banner");
  dom.errorState = document.getElementById("error-state");
  dom.errorMessage = document.getElementById("error-message");
  dom.lastRefresh = document.getElementById("last-refresh");
  dom.sourcePath = document.getElementById("source-path");
  dom.refreshButton = document.getElementById("refresh-button");
}

async function refreshSnapshot() {
  try {
    const response = await fetch("/snapshot");
    if (!response.ok) {
      throw new Error("Snapshot request failed with status " + response.status);
    }

    const payload = await response.json();
    applySnapshot(normalizeSnapshot(payload));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const fallback = state.snapshot
      ? Object.assign({}, state.snapshot, { stale: true, error: message })
      : buildEmptyShell(message);
    applySnapshot(fallback);
  }
}

function applySnapshot(snapshot) {
  state.snapshot = snapshot;
  state.selectedItemId = pickSelectedItemId(snapshot, state.selectedItemId);
  renderSnapshot(snapshot);
}

function normalizeSnapshot(rawSnapshot) {
  const snapshot = rawSnapshot && typeof rawSnapshot === "object" ? rawSnapshot : {};
  const counts = Object.assign(buildEmptyCounts(), snapshot.counts || {});
  const queues = Object.assign(buildEmptyQueues(), snapshot.queues || {});
  const meta = Object.assign(
    {
      title: "Strict Review Progress Viewer",
      implementation_concurrency: 0,
      reviewer_concurrency: 0,
      dag_degraded: false,
    },
    snapshot.meta || {}
  );
  const dag = Object.assign({ nodes: [], edges: [], mermaid_reference: "" }, snapshot.dag || {});

  return {
    meta: meta,
    counts: counts,
    queues: queues,
    dag: dag,
    items: Array.isArray(snapshot.items) ? snapshot.items : [],
    warnings: Array.isArray(snapshot.warnings) ? snapshot.warnings : [],
    stale: Boolean(snapshot.stale),
    error: snapshot.error || "",
  };
}

function buildEmptyShell(errorMessage) {
  return {
    meta: {
      title: "Strict Review Progress Viewer",
      implementation_concurrency: 0,
      reviewer_concurrency: 0,
      dag_degraded: false,
    },
    counts: buildEmptyCounts(),
    queues: buildEmptyQueues(),
    dag: {
      nodes: [],
      edges: [],
      mermaid_reference: "",
    },
    items: [],
    warnings: [],
    stale: true,
    error: errorMessage,
  };
}

function buildEmptyCounts() {
  return {
    blocked: 0,
    ready: 0,
    active: 0,
    implemented: 0,
    "review-queued": 0,
    "in-review": 0,
    "changes-requested": 0,
    done: 0,
  };
}

function buildEmptyQueues() {
  return {
    blocked: [],
    ready: [],
    active: [],
    implemented: [],
    review_queued: [],
    in_review: [],
    changes_requested: [],
    done: [],
  };
}

function renderSnapshot(snapshot) {
  renderOverview(snapshot);
  renderWarnings(snapshot);
  renderDag(snapshot);
  renderMermaidReference(snapshot);
  renderQueues(snapshot);
  renderItemDetail(snapshot);
  renderErrorState(snapshot);

  if (dom.lastRefresh) {
    dom.lastRefresh.textContent = "Last refresh: " + new Date().toLocaleTimeString();
  }
  if (dom.sourcePath) {
    dom.sourcePath.textContent = snapshot.meta.title || "Snapshot source: /snapshot";
  }
}

function renderOverview(snapshot) {
  if (!dom.overview) {
    return;
  }

  dom.overview.replaceChildren();
  dom.overview.appendChild(createHeadingBlock("Overview", snapshot.meta.title || "Strict Review Progress Viewer"));

  const summaryGrid = document.createElement("div");
  summaryGrid.className = "summary-grid";
  summaryGrid.appendChild(createSummaryCard("Items", String(ensureItems(snapshot).length)));
  summaryGrid.appendChild(createSummaryCard("Implementation Slots", String(snapshot.meta.implementation_concurrency || 0)));
  summaryGrid.appendChild(createSummaryCard("Reviewer Slots", String(snapshot.meta.reviewer_concurrency || 0)));
  summaryGrid.appendChild(createSummaryCard("DAG State", snapshot.meta.dag_degraded ? "Degraded" : "Healthy"));
  dom.overview.appendChild(summaryGrid);

  const countGrid = document.createElement("div");
  countGrid.className = "count-grid";
  KNOWN_STATUSES.forEach(function (status) {
    const meta = getStatusMeta(status);
    const card = document.createElement("article");
    card.className = "count-card " + meta.className;
    const label = document.createElement("span");
    label.textContent = meta.label;
    const value = document.createElement("strong");
    value.textContent = String(snapshot.counts[status] || 0);
    card.appendChild(label);
    card.appendChild(value);
    countGrid.appendChild(card);
  });

  const unknownCard = document.createElement("article");
  unknownCard.className = "count-card status-unknown";
  const unknownLabel = document.createElement("span");
  unknownLabel.textContent = "Unknown Status";
  const unknownValue = document.createElement("strong");
  unknownValue.textContent = String(getUnknownStatusItems(snapshot).length);
  unknownCard.appendChild(unknownLabel);
  unknownCard.appendChild(unknownValue);
  countGrid.appendChild(unknownCard);

  dom.overview.appendChild(countGrid);
}

function renderWarnings(snapshot) {
  updateWarningsRegion(snapshot);
  updateStaleBanner(snapshot);
}

function updateWarningsRegion(snapshot) {
  if (!dom.warningsList || !dom.warningsEmpty) {
    return;
  }

  const warnings = Array.isArray(snapshot.warnings) ? snapshot.warnings : [];
  if (warnings.length === 0) {
    if (!dom.warningsList.classList.contains("hidden")) {
      dom.warningsList.replaceChildren();
      dom.warningsList.classList.add("hidden");
    }
    dom.warningsEmpty.classList.remove("hidden");
    if (dom.warningsEmpty.textContent !== "No warnings.") {
      dom.warningsEmpty.textContent = "No warnings.";
    }
    return;
  }

  const warningEntries = warnings.map(function (warning) {
    const code = warning && warning.code ? String(warning.code) : "warning";
    const message = warning && warning.message ? String(warning.message) : "Unknown warning";
    return code + ": " + message;
  });
  const nextSignature = warningEntries.join("\n");
  const currentSignature = Array.from(dom.warningsList.children).map(function (item) {
    return item.textContent || "";
  }).join("\n");

  dom.warningsEmpty.classList.add("hidden");
  dom.warningsList.classList.remove("hidden");

  if (currentSignature === nextSignature) {
    return;
  }

  dom.warningsList.replaceChildren();
  warningEntries.forEach(function (entry) {
    const item = document.createElement("li");
    item.textContent = entry;
    dom.warningsList.appendChild(item);
  });
}

function updateStaleBanner(snapshot) {
  if (!dom.staleBanner) {
    return;
  }

  const message = snapshot.stale || snapshot.error
    ? snapshot.error
      ? "Snapshot is stale. Latest refresh failed: " + snapshot.error
      : "Snapshot is stale. Showing the most recent successful data."
    : "";

  if (message) {
    if (dom.staleBanner.textContent !== message) {
      dom.staleBanner.textContent = message;
    }
    dom.staleBanner.classList.remove("hidden");
  } else {
    if (dom.staleBanner.textContent !== "") {
      dom.staleBanner.textContent = "";
    }
    dom.staleBanner.classList.add("hidden");
  }
}

function renderDag(snapshot) {
  if (!dom.dag) {
    return;
  }

  dom.dag.replaceChildren();
  const nodes = Array.isArray(snapshot.dag.nodes) ? snapshot.dag.nodes : [];
  const edges = Array.isArray(snapshot.dag.edges) ? snapshot.dag.edges : [];

  if (nodes.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No DAG nodes available.";
    dom.dag.appendChild(empty);
    return;
  }

  const layout = computeDagLayout(nodes, edges);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 " + layout.width + " " + layout.height);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Strict review dependency graph");

  edges.forEach(function (edge) {
    const source = layout.positions[edge.source];
    const target = layout.positions[edge.target];
    if (!source || !target) {
      return;
    }
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("class", "dag-edge");
    line.setAttribute("x1", String(source.x + layout.nodeWidth));
    line.setAttribute("y1", String(source.y + layout.nodeHeight / 2));
    line.setAttribute("x2", String(target.x));
    line.setAttribute("y2", String(target.y + layout.nodeHeight / 2));
    svg.appendChild(line);
  });

  nodes.forEach(function (node) {
    const position = layout.positions[node.item_id || node.node_id];
    if (!position) {
      return;
    }
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const statusMeta = getStatusMeta(node.dispatch_status);
    group.setAttribute("class", "dag-node " + statusMeta.className + (state.selectedItemId === node.item_id ? " selected" : ""));
    if (node.item_id) {
      group.setAttribute("data-item-id", node.item_id);
    }
    group.setAttribute("tabindex", "0");

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(position.x));
    rect.setAttribute("y", String(position.y));
    rect.setAttribute("width", String(layout.nodeWidth));
    rect.setAttribute("height", String(layout.nodeHeight));
    group.appendChild(rect);

    const itemIdText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    itemIdText.setAttribute("x", String(position.x + 16));
    itemIdText.setAttribute("y", String(position.y + 24));
    itemIdText.textContent = node.item_id || node.node_id || "unknown-item";
    group.appendChild(itemIdText);

    const titleText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    titleText.setAttribute("x", String(position.x + 16));
    titleText.setAttribute("y", String(position.y + 46));
    titleText.textContent = truncateText(node.title || node.heading || "Untitled item", 26);
    group.appendChild(titleText);

    const statusText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    statusText.setAttribute("x", String(position.x + 16));
    statusText.setAttribute("y", String(position.y + 68));
    statusText.textContent = statusMeta.label;
    group.appendChild(statusText);

    group.addEventListener("click", function () {
      if (node.item_id) {
        selectItem(node.item_id);
      }
    });
    group.addEventListener("keydown", function (event) {
      if ((event.key === "Enter" || event.key === " ") && node.item_id) {
        event.preventDefault();
        selectItem(node.item_id);
      }
    });
    svg.appendChild(group);
  });

  dom.dag.appendChild(svg);
}

function renderMermaidReference(snapshot) {
  if (!dom.mermaidReference) {
    return;
  }
  dom.mermaidReference.textContent = snapshot.dag.mermaid_reference || "No Mermaid reference available.";
}

function renderQueues(snapshot) {
  if (!dom.queues) {
    return;
  }

  dom.queues.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = "Queues";
  dom.queues.appendChild(title);

  const grid = document.createElement("div");
  grid.className = "queue-grid";

  const queueDescriptors = [
    { status: "blocked", items: ensureArray(snapshot.queues.blocked) },
    { status: "ready", items: ensureArray(snapshot.queues.ready) },
    { status: "active", items: ensureArray(snapshot.queues.active) },
    { status: "implemented", items: ensureArray(snapshot.queues.implemented) },
    { status: "review-queued", items: ensureArray(snapshot.queues.review_queued) },
    { status: "in-review", items: ensureArray(snapshot.queues.in_review) },
    { status: "changes-requested", items: ensureArray(snapshot.queues.changes_requested) },
    { status: "done", items: ensureArray(snapshot.queues.done) },
    { status: "unknown", items: getUnknownStatusItems(snapshot) },
  ];

  queueDescriptors.forEach(function (descriptor) {
    const column = document.createElement("section");
    const meta = getStatusMeta(descriptor.status);
    column.className = "queue-column " + meta.className;

    const heading = document.createElement("h3");
    heading.textContent = meta.label + " (" + descriptor.items.length + ")";
    column.appendChild(heading);

    if (descriptor.items.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No items.";
      column.appendChild(empty);
    } else {
      const list = document.createElement("ul");
      list.className = "queue-list";
      descriptor.items.forEach(function (item) {
        const listItem = document.createElement("li");
        listItem.appendChild(createQueueButton(item));
        list.appendChild(listItem);
      });
      column.appendChild(list);
    }

    grid.appendChild(column);
  });

  dom.queues.appendChild(grid);
}

function renderItemDetail(snapshot) {
  if (!dom.itemDetail) {
    return;
  }

  dom.itemDetail.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = "Item Detail";
  dom.itemDetail.appendChild(heading);

  const items = ensureItems(snapshot);
  const selectedItem = items.find(function (item) {
    return item.item_id === state.selectedItemId;
  });

  if (!selectedItem) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Select an item to inspect its plan, implementation, verification, and review details.";
    dom.itemDetail.appendChild(empty);
    return;
  }

  const title = document.createElement("p");
  title.className = "meta-line";
  title.textContent = (selectedItem.item_id || "unknown-item") + " — " + (selectedItem.title || selectedItem.heading || "Untitled item");
  dom.itemDetail.appendChild(title);

  const fieldList = document.createElement("dl");
  fieldList.className = "field-list";
  [
    ["dispatch_status", selectedItem.dispatch_status || "unknown"],
    ["blocked_by", formatList(selectedItem.blocked_by)],
    ["blocks", formatList(selectedItem.blocks)],
    ["shared_surfaces", formatList(selectedItem.shared_surfaces)],
    ["parallel_group", selectedItem.parallel_group || "none"],
    ["assigned_subagent", selectedItem.assigned_subagent || "none"],
    ["reviewer_id", getStructuredField(selectedItem, "reviewer_id") || "none"],
    ["reviewer_state", getStructuredField(selectedItem, "reviewer_state") || "unknown"],
    ["next_action", getStructuredField(selectedItem, "next_action") || "none"],
    ["累计等待时长", extractReviewField(selectedItem.review_record, "累计等待时长") || "n/a"],
    ["超时次数", extractReviewField(selectedItem.review_record, "超时次数") || "n/a"],
    ["Replacement Reviewer", extractReviewField(selectedItem.review_record, "Replacement Reviewer") || "n/a"],
  ].forEach(function (entry) {
    const card = document.createElement("div");
    card.className = "field-card";
    const dt = document.createElement("dt");
    dt.textContent = entry[0];
    const dd = document.createElement("dd");
    dd.textContent = entry[1];
    card.appendChild(dt);
    card.appendChild(dd);
    fieldList.appendChild(card);
  });
  dom.itemDetail.appendChild(fieldList);

  const detailSections = document.createElement("div");
  detailSections.className = "detail-sections";
  detailSections.appendChild(createDetailSection("Plan", selectedItem.plan));
  detailSections.appendChild(createDetailSection("Implementation Record", selectedItem.implementation_record));
  detailSections.appendChild(createDetailSection("Verification Record", selectedItem.verification_record));
  detailSections.appendChild(createDetailSection("Review Record", selectedItem.review_record));
  dom.itemDetail.appendChild(detailSections);
}

function renderErrorState(snapshot) {
  if (!dom.errorState || !dom.errorMessage) {
    return;
  }

  const message = isFirstLoadErrorShell(snapshot)
    ? "The first load only returned a stale error shell. Retry once the viewer server can read a checklist snapshot: " + (snapshot.error || "unknown error")
    : "The viewer has a usable snapshot.";

  if (dom.errorMessage.textContent !== message) {
    dom.errorMessage.textContent = message;
  }

  if (isFirstLoadErrorShell(snapshot)) {
    dom.errorState.classList.remove("hidden");
  } else {
    dom.errorState.classList.add("hidden");
  }
}

function isFirstLoadErrorShell(snapshot) {
  const items = ensureItems(snapshot);
  const dagNodes = snapshot && snapshot.dag && Array.isArray(snapshot.dag.nodes) ? snapshot.dag.nodes : [];
  return Boolean(snapshot.stale && snapshot.error && items.length === 0 && dagNodes.length === 0);
}

function pickSelectedItemId(snapshot, currentItemId) {
  const items = ensureItems(snapshot);
  const hashedItemId = readHashSelectedItemId();
  if (hashedItemId) {
    const hashedItem = items.find(function (item) {
      return item.item_id === hashedItemId;
    });
    if (hashedItem) {
      return hashedItem.item_id;
    }
  }

  if (currentItemId) {
    const currentItem = items.find(function (item) {
      return item.item_id === currentItemId;
    });
    if (currentItem) {
      return currentItem.item_id;
    }
  }

  return items.length > 0 ? items[0].item_id || null : null;
}

function readHashSelectedItemId() {
  const hash = window.location && typeof window.location.hash === "string" ? window.location.hash : "";
  if (!hash || hash === "#") {
    return null;
  }

  const rawValue = hash.slice(1);
  if (!rawValue) {
    return null;
  }

  try {
    const decodedValue = decodeURIComponent(rawValue).trim();
    return decodedValue || null;
  } catch (error) {
    return rawValue.trim() || null;
  }
}

function selectItem(itemId) {
  state.selectedItemId = itemId;
  location.hash = encodeURIComponent(itemId);
  if (state.snapshot) {
    renderSnapshot(state.snapshot);
  }
}

function getStatusMeta(status) {
  return STATUS_META[status] || STATUS_META.unknown;
}

function getUnknownStatusItems(snapshot) {
  return ensureItems(snapshot).filter(function (item) {
    return !KNOWN_STATUS_SET.has(item.dispatch_status);
  });
}

function getStructuredField(item, key) {
  if (!item || !item.structured_fields || typeof item.structured_fields !== "object") {
    return "";
  }
  const value = item.structured_fields[key];
  return value == null ? "" : String(value);
}

function extractReviewField(reviewRecord, label) {
  if (!reviewRecord) {
    return "";
  }

  const lines = String(reviewRecord).split(/\r?\n/);
  const normalizedLabel = String(label).trim();
  for (const line of lines) {
    const match = line.match(/^\s*-\s*([^：:]+?)\s*[：:]\s*(.*)$/);
    if (!match) {
      continue;
    }
    if (match[1].trim() === normalizedLabel) {
      return match[2].trim();
    }
  }

  return "";
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function ensureItems(snapshot) {
  return snapshot && Array.isArray(snapshot.items) ? snapshot.items : [];
}

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

function formatList(value) {
  return Array.isArray(value) && value.length > 0 ? value.join(", ") : "none";
}

function createHeadingBlock(titleText, subtitleText) {
  const wrapper = document.createElement("div");
  const title = document.createElement("h2");
  title.textContent = titleText;
  const subtitle = document.createElement("p");
  subtitle.className = "subtle";
  subtitle.textContent = subtitleText;
  wrapper.appendChild(title);
  wrapper.appendChild(subtitle);
  return wrapper;
}

function createSummaryCard(labelText, valueText) {
  const card = document.createElement("article");
  card.className = "summary-card";
  const label = document.createElement("span");
  label.textContent = labelText;
  const value = document.createElement("strong");
  value.textContent = valueText;
  card.appendChild(label);
  card.appendChild(value);
  return card;
}

function createQueueButton(item) {
  const button = document.createElement("button");
  const statusMeta = getStatusMeta(item.dispatch_status);
  button.type = "button";
  button.className = "item-button " + statusMeta.className;
  button.setAttribute("data-item-id", item.item_id || "");
  button.setAttribute("aria-pressed", String(state.selectedItemId === item.item_id));
  button.addEventListener("click", function () {
    if (item.item_id) {
      selectItem(item.item_id);
    }
  });

  const itemId = document.createElement("span");
  itemId.className = "item-id";
  itemId.textContent = item.item_id || "unknown-item";
  button.appendChild(itemId);

  const title = document.createElement("span");
  title.className = "item-title";
  title.textContent = item.title || item.heading || "Untitled item";
  button.appendChild(title);

  const metaLines = [statusMeta.label];
  const reviewerId = getStructuredField(item, "reviewer_id");
  const reviewerState = getStructuredField(item, "reviewer_state");
  const waitDuration = extractReviewField(item.review_record, "累计等待时长");
  const timeoutCount = extractReviewField(item.review_record, "超时次数");
  const replacementReviewer = extractReviewField(item.review_record, "Replacement Reviewer");

  if (reviewerId) {
    metaLines.push("reviewer_id: " + reviewerId);
  }
  if (reviewerState) {
    metaLines.push("reviewer_state: " + reviewerState);
  }
  if (waitDuration) {
    metaLines.push("累计等待时长: " + waitDuration);
  }
  if (timeoutCount) {
    metaLines.push("超时次数: " + timeoutCount);
  }
  if (replacementReviewer) {
    metaLines.push("Replacement Reviewer: " + replacementReviewer);
  }

  const meta = document.createElement("span");
  meta.className = "item-meta";
  meta.textContent = metaLines.join(" • ");
  button.appendChild(meta);
  return button;
}

function createDetailSection(label, value) {
  const section = document.createElement("section");
  section.className = "detail-section";
  const title = document.createElement("h3");
  title.textContent = label;
  const pre = document.createElement("pre");
  pre.textContent = value || "No content recorded.";
  section.appendChild(title);
  section.appendChild(pre);
  return section;
}

function computeDagLayout(nodes, edges) {
  const nodeWidth = 210;
  const nodeHeight = 88;
  const horizontalGap = 60;
  const verticalGap = 26;
  const predecessors = {};
  const layerById = {};

  nodes.forEach(function (node) {
    predecessors[node.item_id || node.node_id] = [];
  });

  edges.forEach(function (edge) {
    if (!predecessors[edge.target]) {
      predecessors[edge.target] = [];
    }
    predecessors[edge.target].push(edge.source);
  });

  const visiting = new Set();
  function layerFor(nodeId) {
    if (layerById[nodeId] != null) {
      return layerById[nodeId];
    }
    if (visiting.has(nodeId)) {
      layerById[nodeId] = 0;
      return 0;
    }
    visiting.add(nodeId);
    const parentIds = predecessors[nodeId] || [];
    let layer = 0;
    parentIds.forEach(function (parentId) {
      layer = Math.max(layer, layerFor(parentId) + 1);
    });
    visiting.delete(nodeId);
    layerById[nodeId] = layer;
    return layer;
  }

  const layers = [];
  nodes.forEach(function (node) {
    const nodeId = node.item_id || node.node_id;
    const layer = layerFor(nodeId);
    if (!layers[layer]) {
      layers[layer] = [];
    }
    layers[layer].push(node);
  });

  const positions = {};
  layers.forEach(function (layerNodes, layerIndex) {
    if (!Array.isArray(layerNodes)) {
      return;
    }
    layerNodes.forEach(function (node, rowIndex) {
      positions[node.item_id || node.node_id] = {
        x: 20 + layerIndex * (nodeWidth + horizontalGap),
        y: 20 + rowIndex * (nodeHeight + verticalGap),
      };
    });
  });

  const maxRows = layers.reduce(function (currentMax, layerNodes) {
    return Math.max(currentMax, Array.isArray(layerNodes) ? layerNodes.length : 0);
  }, 1);

  return {
    positions: positions,
    nodeWidth: nodeWidth,
    nodeHeight: nodeHeight,
    width: 40 + Math.max(1, layers.length) * (nodeWidth + horizontalGap),
    height: 40 + maxRows * (nodeHeight + verticalGap),
  };
}

function truncateText(value, length) {
  if (!value) {
    return "";
  }
  const text = String(value);
  return text.length > length ? text.slice(0, length - 1) + "…" : text;
}
