"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {catalog: null, versions: [], current: {}, health: null, adminToken: "", queryController: null, catalogSequence: 0, view: "lookup"};
const icons = {
  info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v5M10 6v1"/>',
  check: '<circle cx="10" cy="10" r="7"/><path d="m6.5 10 2.2 2.2 4.8-5"/>',
  book: '<path d="M3 3h6l2 2h6v13H3zM6 8h8M6 11h8M6 14h5"/>',
  arrow: '<path d="M11 3h6v6M17 3l-9 9M8 3H3v14h14v-5"/>',
  search: '<circle cx="8" cy="8" r="5"/><path d="m12 12 5 5"/>',
  chat: '<path d="M3 3h14v10H8l-5 4zM6 7h8M6 10h5"/>'
};

function el(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  // The SVG fragments are static application constants, never API data.
  svg.innerHTML = icons[name] || icons.info;
  return svg;
}
function valueOf(value) {
  if (value && typeof value === "object" && "value" in value) return value.value;
  return value;
}
function display(value) {
  value = valueOf(value);
  if (value === undefined || value === null || value === "") return "";
  return Array.isArray(value) ? value.map(display).filter(Boolean).join(", ") : String(value);
}
function verifiedDisplay(metadata, key) {
  const field = metadata?.[key];
  return field && field.verification_status === "verified" && list(field.evidence_ids).length ? display(field.value) : "";
}
function list(value) { return Array.isArray(value) ? value : []; }
function errorText(error) { return error && error.message ? error.message : "Không thể hoàn tất yêu cầu."; }
async function api(path, options = {}) {
  const headers = {"Accept": "application/json", ...options.headers};
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  let data;
  try { data = await response.json(); } catch { throw new Error(`Server trả phản hồi không hợp lệ (HTTP ${response.status}).`); }
  if (!response.ok) {
    const detail = data.message || data.error?.message || data.error || data.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(item => `${list(item.loc).join(".")}: ${item.msg}`).join("; ") : "Yêu cầu chưa được xử lý.";
    const err = new Error(`${message} (HTTP ${response.status})${data.request_id ? ` · ${data.request_id}` : ""}`);
    err.response = data;
    throw err;
  }
  return data;
}
function statusBox(title, message, kind = "neutral") {
  const box = el("div", `result-status ${kind}`);
  box.append(icon(kind === "success" ? "check" : "info"));
  const copy = el("div");
  copy.append(el("strong", "", title));
  if (message) copy.append(el("p", "", message));
  box.append(copy);
  return box;
}
function emptyState(title, message) {
  const box = el("div", "empty-state");
  box.append(icon("book"), el("h3", "", title), el("p", "", message));
  return box;
}
function sourceURL(url) {
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.origin === window.location.origin && /^\/sources\/[^/]+$/.test(parsed.pathname)) return parsed.href;
  } catch { /* An invalid registry link is shown without a clickable URL. */ }
  return null;
}
function registeredURL(fileId, bundleId, page) {
  if (!fileId || !bundleId) return null;
  const params = new URLSearchParams({bundle_id: String(bundleId), page: String(page || 1)});
  return `/sources/${encodeURIComponent(fileId)}?${params}#page=${encodeURIComponent(page || 1)}`;
}
function sourceLink(url, label = "Mở tài liệu nguồn") {
  const safe = sourceURL(url);
  if (!safe) return el("span", "muted", "Chưa có liên kết nguồn hợp lệ");
  const a = el("a", "source-link", label);
  a.href = safe;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.append(icon("arrow"));
  return a;
}
function selectValues(select, values, defaultLabel, selected = "") {
  select.replaceChildren(new Option(defaultLabel, ""));
  for (const entry of list(values)) {
    const value = display(typeof entry === "object" ? entry.value ?? entry.id ?? entry.code : entry);
    if (!value) continue;
    const label = typeof entry === "object" ? display(entry.label || entry.name || entry.title || value) : value;
    select.add(new Option(label, value));
  }
  select.value = [...select.options].some(option => option.value === selected) ? selected : "";
}
function setView(view) {
  state.view = view;
  $$(".view").forEach(section => { section.hidden = section.id !== `view-${view}`; });
  $$(".nav-item").forEach(button => {
    const selected = button.dataset.view === view;
    button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  $("#breadcrumb-current").textContent = {lookup: "Tra cứu", documents: "Tài liệu nguồn", admin: "Quản trị dữ liệu"}[view];
  if (view === "documents") renderDocuments();
  document.title = `${$("#breadcrumb-current").textContent} · Cổng thông tin đào tạo UTC`;
}
function updateVersions() {
  const selected = $("#version-select").value;
  const strategy = $("#strategy-select").value;
  const versions = state.versions.filter(version => version.strategy === strategy && version.published !== false);
  selectValues($("#version-select"), versions.map(version => ({value: version.bundle_id, label: `${version.active ? "Hiện hành · " : ""}${version.bundle_id}`})), "Phiên bản hiện hành", selected);
}
async function loadVersions() {
  const data = await api("/versions");
  state.versions = list(data.versions || data);
  state.current = data.current || {};
  updateVersions();
}
async function loadCatalog() {
  const sequence = ++state.catalogSequence;
  const params = new URLSearchParams({strategy: $("#strategy-select").value});
  if ($("#version-select").value) params.set("index_version", $("#version-select").value);
  try {
    const data = await api(`/catalog?${params}`);
    if (sequence !== state.catalogSequence) return;
    state.catalog = data;
    const filters = data.filters || {};
    for (const [id, key, label] of [
      ["filter-major", "major_code", "Tất cả ngành đã xác minh"],
      ["filter-degree", "degree_level", "Tất cả"],
      ["filter-type", "program_type", "Tất cả"],
      ["filter-program", "program_version_id", "Tất cả phiên bản đã xác minh"]
    ]) {
      const values = key === "program_version_id" ? list(filters[key]).map(value => {
        const source = list(data.documents).find(doc => verifiedDisplay(doc.metadata, key) === String(value));
        return {value, label: source?.title ? `${source.title} · ${value}` : value};
      }) : filters[key];
      selectValues($(`#${id}`), values, label, $(`#${id}`).value);
      $(`#${id}`).disabled = !list(filters[key]).length;
    }
    $("#stat-documents").textContent = String(list(data.documents).length);
    $("#coverage-notice").textContent = coverageMessage(data.coverage, data.bundle_id);
    renderDocuments();
  } catch (error) {
    if (sequence !== state.catalogSequence) return;
    state.catalog = null;
    $("#stat-documents").textContent = "—";
    $("#coverage-notice").textContent = `Chưa nạp được phạm vi dữ liệu: ${errorText(error)}`;
    for (const id of ["filter-major", "filter-degree", "filter-type", "filter-program"]) $("#" + id).disabled = true;
    renderDocuments(errorText(error));
  }
}
function coverageMessage(coverage, bundleId) {
  if (!coverage) return bundleId ? `Đang sử dụng gói ${bundleId}. Chỉ tra cứu trong tài liệu đã công bố.` : "Chưa có gói dữ liệu được công bố.";
  const parts = [];
  const excluded = coverage.blocked_files ?? coverage.blocked ?? coverage.excluded_files ?? coverage.excluded;
  const review = coverage.needs_review_files ?? coverage.needs_review;
  if (Number.isFinite(excluded) && excluded > 0) parts.push(`${excluded} đối tượng chưa đủ điều kiện phục vụ`);
  if (Number.isFinite(review) && review > 0) parts.push(`${review} đối tượng cần kiểm tra`);
  if (coverage.limitations) parts.push(...list(coverage.limitations).map(display));
  if (coverage.message) parts.push(display(coverage.message));
  return parts.length ? `Phạm vi dữ liệu: ${parts.join("; ")}.` : `Gói ${bundleId || "đang chọn"} chỉ phục vụ nội dung đủ điều kiện. Không suy ra chương trình hoặc quy định ngoài kho.`;
}
async function loadHealth() {
  const chip = $("#system-health");
  try {
    const health = await api("/health");
    state.health = health;
    const llmReady = Boolean(health.llm?.ready);
    chip.className = `health-chip ${health.ready ? "ready" : "warning"}`;
    chip.lastElementChild.textContent = health.ready ? "Kho dữ liệu sẵn sàng" : "Chưa có kho sẵn sàng";
    $("#stat-llm").textContent = llmReady ? "Hỏi đáp sẵn sàng" : "LLM chưa sẵn sàng";
    const hint = $("#llm-hint");
    hint.hidden = llmReady || $("input[name=mode]:checked").value !== "ask";
    hint.textContent = "Ollama hoặc model local chưa sẵn sàng. Bạn vẫn có thể dùng chế độ Tìm tài liệu.";
  } catch {
    state.health = null;
    chip.className = "health-chip error";
    chip.lastElementChild.textContent = "Mất kết nối server";
    $("#stat-llm").textContent = "Chưa kết nối";
  }
}
function filters() {
  const result = {};
  for (const [id, name] of [["filter-major", "major_code"], ["filter-degree", "degree_level"], ["filter-type", "program_type"], ["filter-program", "program_version_id"]]) {
    if ($(`#${id}`).value) result[name] = $(`#${id}`).value;
  }
  return result;
}
function responseStatus(status) {
  return {
    answered: ["Đã tìm thấy căn cứ", "success"],
    partial: ["Kết quả có giới hạn", "warning"],
    clarification_required: ["Cần làm rõ phạm vi", "neutral"],
    insufficient_evidence: ["Chưa đủ căn cứ trong phạm vi đã tìm", "warning"],
    retrieval_incomplete: ["Truy xuất chưa hoàn tất", "warning"],
    error: ["Không thể hoàn tất xử lý", "error"],
    completed: ["Đã hoàn tất tìm kiếm", "success"]
  }[status] || ["Kết quả tìm kiếm", "neutral"];
}
function answerText(text, validAnchors) {
  const node = el("div", "answer-text");
  for (const token of String(text).split(/(\[E\d+\])/g)) {
    const anchor = token.replace(/[\[\]]/g, "");
    if (validAnchors.has(anchor)) {
      const link = el("a", "", token);
      link.href = `#citation-${anchor}`;
      link.setAttribute("aria-label", `Xem nguồn ${anchor}`);
      node.append(link);
    } else node.append(document.createTextNode(token));
  }
  return node;
}
function renderResult(data, mode) {
  const root = $("#results-content");
  root.replaceChildren();
  const [label, kind] = mode === "search" && data.status === "answered"
    ? ["Đã tìm thấy đoạn liên quan", "success"] : responseStatus(data.status);
  const messages = [...list(data.missing_information), ...list(data.warnings)].map(display).filter(Boolean);
  if (data.message) messages.unshift(display(data.message));
  if (data.error) messages.unshift(display(typeof data.error === "object" ? data.error.message || data.error.code : data.error));
  const banner = statusBox(label, messages.join(" "), kind);
  root.append(banner);
  const citations = list(data.citations);
  const retrieved = list(data.retrieved_chunks);
  $("#results-title").textContent = mode === "search" ? `${retrieved.length} đoạn tài liệu liên quan` : "Trả lời từ tài liệu";

  if (data.clarification && list(data.clarification.options).length) {
    const options = el("div", "clarification-options");
    for (const option of data.clarification.options) {
      const value = display(typeof option === "object" ? option.value || option.id : option);
      const id = {major_code: "filter-major", degree_level: "filter-degree", program_type: "filter-type", program_version_id: "filter-program"}[data.clarification.field];
      const optionLabel = id ? [...$("#" + id).options].find(item => item.value === value)?.textContent : null;
      const button = el("button", "", optionLabel || (typeof option === "object" ? display(option.label || option.name || value) : value));
      button.type = "button";
      button.addEventListener("click", () => {
        if (!id || ![...$("#" + id).options].some(item => item.value === value)) return;
        $("#" + id).value = value;
        $("#query-form").requestSubmit();
      });
      options.append(button);
    }
    banner.lastElementChild.append(options);
  }

  if (data.answer) {
    const answer = el("article", "answer-card");
    const heading = el("h3"); heading.append(icon("chat"), document.createTextNode(mode === "search" ? "Kết quả tìm kiếm" : "Câu trả lời"));
    answer.append(heading, answerText(data.answer, new Set(citations.map(item => String(item.anchor).replace(/[\[\]]/g, "")))));
    const meta = el("div", "answer-meta");
    if (data.strategy) meta.append(el("span", "", `Phân đoạn: ${data.strategy === "fixed" ? "Fixed size" : "Recursive"}`));
    if (data.bundle_id) meta.append(el("span", "", `Gói: ${data.bundle_id}`));
    if (data.scope_resolved && Object.keys(data.scope_resolved).length) {
      const scope = Object.entries(data.scope_resolved).map(([key, value]) => {
        if (key === "program_version_id") {
          const option = [...$("#filter-program").options].find(item => item.value === value);
          return option?.textContent || display(value);
        }
        return display(value);
      }).join(" · ");
      meta.append(el("span", "", `Phạm vi: ${scope}`));
    }
    answer.append(meta); root.append(answer);
  }

  if (citations.length) {
    const heading = el("h3", "sources-heading", mode === "search" ? "Đoạn liên quan trong nguồn" : "Nguồn được sử dụng"); heading.append(el("span", "count-tag", citations.length)); root.append(heading);
    const citationList = el("div", "citation-list");
    for (const citation of citations) {
      const anchor = String(citation.anchor || "").replace(/[\[\]]/g, "");
      const card = el("article", "citation-card");
      if (/^E\d+$/.test(anchor)) card.id = `citation-${anchor}`;
      const top = el("div", "citation-top"); top.append(el("span", "anchor-tag", anchor));
      const info = el("div"); info.append(el("h4", "", citation.title || citation.file_id || "Tài liệu nguồn"));
      const pages = citation.pdf_page || citation.page;
      info.append(el("p", "citation-meta", [pages ? `Trang PDF ${pages}` : "", citation.section].filter(Boolean).join(" · ")));
      top.append(info); card.append(top);
      if (citation.quote) card.append(el("p", "citation-quote", citation.quote));
      card.append(sourceLink(citation.url || registeredURL(citation.file_id, data.bundle_id, pages), "Mở đúng trang nguồn"));
      citationList.append(card);
    }
    root.append(citationList);
  }

  if (retrieved.length) {
    const heading = el("h3", "sources-heading", mode === "ask" ? "Các đoạn đã truy xuất" : "Đoạn liên quan và bằng chứng"); heading.append(el("span", "count-tag", retrieved.length)); root.append(heading);
    const evidenceList = el("div", "evidence-list");
    retrieved.forEach((chunk, index) => {
      const detail = el("details", "evidence-card");
      if (mode === "search" && index === 0) detail.open = true;
      const summary = el("summary"); summary.append(el("span", "evidence-order", String(index + 1).padStart(2, "0")));
      const info = el("div", "evidence-info"); info.append(el("strong", "", chunk.title || chunk.metadata?.title || chunk.chunk_id));
      const spans = list(chunk.source_spans);
      const pages = [...new Set(spans.map(span => span.pdf_page).filter(Boolean))];
      const used = mode === "ask" && citations.some(citation => citation.chunk_id === chunk.chunk_id);
      info.append(el("span", "", [pages.length ? `Trang PDF ${pages.join(", ")}` : "", used ? "Đã dùng trong câu trả lời" : "", verifiedDisplay(chunk.metadata, "major_code")].filter(Boolean).join(" · ")));
      summary.append(info, el("span", "evidence-caret", "⌄")); detail.append(summary);
      const body = el("div", "evidence-body"); body.append(el("p", "", chunk.text || ""));
      const links = el("div", "evidence-links");
      const seen = new Set();
      for (const span of spans) {
        const key = `${span.file_id}:${span.pdf_page}`;
        if (seen.has(key)) continue;
        seen.add(key);
        links.append(sourceLink(registeredURL(span.file_id, data.bundle_id, span.pdf_page), `Mở trang ${span.pdf_page}`));
      }
      body.append(links);
      const ranks = [];
      if (chunk.bm25_rank != null) ranks.push(`BM25 hạng ${chunk.bm25_rank}`);
      if (chunk.faiss_rank != null) ranks.push(`FAISS hạng ${chunk.faiss_rank}`);
      if (Number.isFinite(chunk.rrf_score)) ranks.push(`RRF ${chunk.rrf_score.toFixed(5)}`);
      if (ranks.length) body.append(el("div", "rank-line", `${ranks.join(" · ")} · Điểm xếp hạng, không phải độ tin cậy.`));
      detail.append(body); evidenceList.append(detail);
    });
    root.append(evidenceList);
  } else if (!data.answer && !data.clarification && data.status !== "error") {
    root.append(emptyState("Chưa có đoạn tài liệu để hiển thị", "Thử bổ sung từ khóa, kiểm tra phạm vi đã chọn hoặc xem kho tài liệu hiện được công bố."));
  }
  const footer = [];
  if (data.request_id) footer.push(`Mã tra cứu: ${data.request_id}`);
  const total = data.timings?.total_ms ?? data.timings?.total;
  if (Number.isFinite(total)) footer.push(`Thời gian xử lý: ${(total / 1000).toFixed(2)} giây`);
  if (footer.length) root.append(el("p", "result-footnote", footer.join(" · ")));
}
async function submitQuery(event) {
  event.preventDefault();
  const query = $("#query-input").value.trim();
  if (!query || state.queryController) return;
  const mode = $("input[name=mode]:checked").value;
  const controller = new AbortController();
  state.queryController = controller;
  const region = $("#results-region"); region.hidden = false; region.setAttribute("aria-busy", "true");
  $("#results-title").textContent = mode === "ask" ? "Đang kiểm tra căn cứ và tạo câu trả lời…" : "Đang tìm trong kho tài liệu…";
  const loading = el("div", "result-loading"); loading.append(el("span", "spinner"), el("span", "", mode === "ask" ? "Đang xử lý bằng mô hình local. Vui lòng chờ…" : "Đang truy xuất BM25, FAISS và hợp nhất kết quả…"));
  $("#results-content").replaceChildren(loading);
  $("#submit-query").disabled = true; $("#cancel-query").hidden = false;
  const body = {query, filters: filters(), strategy: $("#strategy-select").value};
  if ($("#version-select").value) body.index_version = $("#version-select").value;
  if (mode === "search") body.top_k = 8;
  const timeout = window.setTimeout(() => controller.abort("timeout"), mode === "ask" ? 300000 : 120000);
  try {
    const data = await api(`/${mode}`, {method: "POST", body: JSON.stringify(body), signal: controller.signal});
    renderResult(data, mode);
  } catch (error) {
    $("#results-title").textContent = "Yêu cầu chưa hoàn tất";
    const aborted = controller.signal.aborted;
    $("#results-content").replaceChildren(statusBox(aborted ? "Đã dừng chờ kết quả" : "Không thể hoàn tất tra cứu", aborted ? "Trình duyệt đã ngừng chờ. Server có thể vẫn đang hoàn tất tác vụ đã nhận." : errorText(error), aborted ? "warning" : "error"));
  } finally {
    window.clearTimeout(timeout); state.queryController = null;
    region.setAttribute("aria-busy", "false"); $("#submit-query").disabled = false; $("#cancel-query").hidden = true;
  }
}
function renderDocuments(error) {
  const root = $("#documents-content"); root.replaceChildren();
  const query = $("#document-search").value.toLocaleLowerCase("vi").trim();
  const documents = list(state.catalog?.documents).filter(doc => `${doc.title || ""} ${doc.original_name || ""}`.toLocaleLowerCase("vi").includes(query));
  $("#document-count").textContent = state.catalog ? `${documents.length} tài liệu` : "";
  if (!documents.length) { root.append(emptyState(error ? "Chưa nạp được kho tài liệu" : query ? "Không tìm thấy tài liệu phù hợp" : "Chưa có tài liệu được công bố", error || (query ? "Thử tìm bằng tên khác hoặc bỏ bớt từ khóa." : "Tài liệu chỉ xuất hiện ở đây sau khi bundle đạt kiểm tra và được phát hành."))); return; }
  for (const doc of documents) {
    const card = el("article", "document-card"); const docIcon = el("span", "document-icon"); docIcon.append(icon("book"));
    card.append(docIcon, el("h2", "", doc.title || doc.original_name || doc.file_id));
    const meta = doc.metadata || {};
    const pageInfo = doc.eligible_page_count < doc.page_count ? `${doc.eligible_page_count}/${doc.page_count} trang có thể tra cứu` : `${doc.page_count} trang PDF`;
    card.append(el("p", "document-info", [doc.page_count ? pageInfo : "", verifiedDisplay(meta, "major_code"), verifiedDisplay(meta, "degree_level"), verifiedDisplay(meta, "program_type")].filter(Boolean).join(" · ")));
    if (doc.original_name && doc.original_name !== doc.title) card.append(el("p", "document-name", doc.original_name));
    card.append(sourceLink(registeredURL(doc.file_id, state.catalog.bundle_id, doc.first_eligible_page || 1))); root.append(card);
  }
}
async function adminApi(path, options = {}) { return api(path, {...options, headers: {"Authorization": `Bearer ${state.adminToken}`, ...options.headers}}); }
function adminMessage(title, message, kind = "neutral") { $("#admin-message").replaceChildren(statusBox(title, message, kind)); }
function operationOutput(data) {
  $("#admin-operation-detail").hidden = false;
  $("#admin-operation-detail").open = true;
  $("#admin-operation-output").textContent = JSON.stringify(data, null, 2);
}
function makeTable(headings, rows) {
  const wrapper = el("div", "table-scroll"); const table = el("table"); const thead = el("thead"); const header = el("tr");
  for (const text of headings) { const th = el("th", "", text); th.scope = "col"; header.append(th); } thead.append(header); table.append(thead);
  const tbody = el("tbody"); for (const cells of rows) { const tr = el("tr"); for (const item of cells) { const td = el("td"); if (item instanceof Node) td.append(item); else td.textContent = display(item); tr.append(td); } tbody.append(tr); } table.append(tbody); wrapper.append(table); return wrapper;
}
function renderJobs(data) {
  const jobs = list(data.jobs || data);
  const root = $("#jobs-list"); root.replaceChildren();
  if (!jobs.length) { root.append(el("p", "muted", "Chưa có tác vụ nạp dữ liệu.")); return; }
  const rows = jobs.map(job => {
    const id = el("div"); id.append(el("strong", "", job.job_id || job.run_id));
    const output = job.result || {};
    const dataset = job.dataset_id || job.dataset_version || output.dataset_id || output.dataset_version;
    const coverage = output.coverage || job.coverage;
    if (job.kind) id.append(el("small", "", job.kind === "build" ? "Xây dựng bundle" : "Nạp tài liệu"));
    if (dataset) id.append(el("small", "", `Dataset: ${dataset}`));
    if (job.bundle_id) id.append(el("small", "", `Bundle: ${job.bundle_id}`));
    if (coverage) id.append(el("small", "", `${coverage.eligible_files ?? "—"} file đủ điều kiện · ${coverage.blocked_files ?? "—"} file bị chặn`));
    if (coverage?.ocr_attempted_pages != null) {
      id.append(el("small", "", `OCR ${coverage.ocr_attempted_pages} trang · ${coverage.ocr_pending_pages} chờ đối chiếu · ${coverage.ocr_approved_pages} đã đối chiếu · ${(coverage.ocr_failed_pages || 0) + (coverage.ocr_empty_pages || 0)} chưa nhận được chữ`));
    }
    if (job.error) id.append(el("small", "", `Lỗi: ${display(job.error)}`));
    const details = el("details", "job-detail"); details.append(el("summary", "", "Xem chi tiết"), el("pre", "", JSON.stringify(job, null, 2))); id.append(details);
    const statusLabel = {pending: "Đang chờ worker", running: "Đang xử lý", completed: "Đã hoàn tất", completed_with_issues: "Hoàn tất, có giới hạn", failed: "Thất bại", blocked: "Bị chặn"}[job.status] || job.status || "—";
    const status = el("span", `badge ${["blocked", "error", "failed"].includes(job.status) ? "blocked" : ""}`, statusLabel);
    const copy = el("button", "button button-quiet", "Dùng dataset"); copy.type = "button";
    copy.disabled = !dataset; copy.addEventListener("click", () => { $("#dataset-id").value = dataset; $("#dataset-id").focus(); });
    return [id, status, job.created_at || job.started_at || "—", copy];
  });
  root.append(makeTable(["TÁC VỤ / DATASET", "TRẠNG THÁI", "THỜI ĐIỂM", "THAO TÁC"], rows));
}
function renderReleases(data) {
  const versions = list(data.bundles || data.versions || data.releases || data);
  const root = $("#releases-list"); root.replaceChildren();
  if (!versions.length) { root.append(el("p", "muted", "Chưa có bundle. Hãy nạp tài liệu và xây dựng chỉ mục trước.")); return; }
  const rows = versions.map(version => {
    const info = el("div"); info.append(el("strong", "", version.bundle_id));
    info.append(el("small", "", [version.dataset_version || version.dataset_id, version.strategy].filter(Boolean).join(" · ")));
    const active = version.active || state.current[version.strategy] === version.bundle_id;
    const status = el("span", `badge ${active ? "active" : ""}`, active ? "Đang phục vụ" : version.status || (version.published ? "Đã phát hành" : "Đã tạo"));
    const counts = el("div", "", `${version.chunk_count ?? "—"} chunk`); if (version.document_count != null) counts.append(el("small", "", `${version.document_count} tài liệu`));
    const actions = el("div", "table-actions");
    if (!active) {
      const publish = el("button", "button button-quiet", "Phát hành"); publish.type = "button"; publish.addEventListener("click", () => changeRelease(version, false, publish)); actions.append(publish);
      if (version.published || ["published", "retired"].includes(version.status)) {
        const rollback = el("button", "button button-quiet", "Quay lại"); rollback.type = "button"; rollback.addEventListener("click", () => changeRelease(version, true, rollback)); actions.append(rollback);
      }
    }
    return [info, status, counts, actions];
  });
  root.append(makeTable(["BUNDLE", "TRẠNG THÁI", "NỘI DUNG", "THAO TÁC"], rows));
  const history = list(data.history || data.releases);
  if (history.length) {
    const details = el("details", "job-detail"); details.append(el("summary", "", "Lịch sử phát hành và quay lại"), el("pre", "", JSON.stringify(history, null, 2))); root.append(details);
  }
}
async function refreshAdmin() {
  if (!state.adminToken) return;
  const results = await Promise.allSettled([adminApi("/admin/jobs"), adminApi("/admin/releases"), loadVersions()]);
  if (results[0].status === "fulfilled") renderJobs(results[0].value);
  if (results[1].status === "fulfilled") renderReleases(results[1].value);
  const error = results.find(result => result.status === "rejected");
  if (error) throw error.reason;
}
async function changeRelease(version, rollback, button) {
  button.disabled = true;
  try {
    const result = await adminApi(rollback ? "/admin/rollback" : "/admin/releases", {method: "POST", body: JSON.stringify({bundle_id: version.bundle_id, expected_current: state.current[version.strategy] || null})});
    operationOutput(result); adminMessage(rollback ? "Đã quay lại phiên bản" : "Đã phát hành bundle", version.bundle_id, "success");
    await refreshAdmin(); await loadCatalog(); await loadHealth();
  } catch (error) { adminMessage("Thao tác phiên bản chưa hoàn tất", errorText(error), "error"); } finally { button.disabled = false; }
}
async function adminFormAction(form, path, body) {
  const button = $("button[type=submit]", form); button.disabled = true;
  adminMessage("Đang xử lý tác vụ", "Tác vụ chạy trên máy chủ. Vui lòng giữ trang để theo dõi kết quả.");
  try {
    const result = await adminApi(path, {method: "POST", body: JSON.stringify(body)});
    operationOutput(result);
    const dataset = result.dataset_id || result.dataset_version || result.result?.dataset_id;
    if (dataset) $("#dataset-id").value = dataset;
    const queued = result.status === "pending" || result.status === "running";
    const title = result.reused ? "Yêu cầu đã có trong hệ thống" : queued ? "Đã đưa tác vụ vào hàng đợi" : "Server đã nhận yêu cầu";
    const message = queued ? `Job: ${result.job_id}. Worker sẽ xử lý riêng; chọn Làm mới để xem tiến độ.` : result.job_id ? `Job: ${result.job_id}. Kiểm tra trạng thái trong danh sách tác vụ.` : result.bundle_id ? `Bundle: ${result.bundle_id}` : "Xem trạng thái và chi tiết bên dưới.";
    adminMessage(title, message, "success");
    await refreshAdmin();
  } catch (error) { adminMessage("Tác vụ chưa hoàn tất", errorText(error), "error"); } finally { button.disabled = false; }
}

$$(".nav-item").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
$$("[data-query]").forEach(button => button.addEventListener("click", () => { $("#query-input").value = button.dataset.query; $("#query-input").dispatchEvent(new Event("input")); $("#query-input").focus(); }));
$("#query-input").addEventListener("input", () => { $("#query-count").textContent = `${$("#query-input").value.length.toLocaleString("vi-VN")} / 2.000 ký tự`; });
$("#query-input").addEventListener("keydown", event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); $("#query-form").requestSubmit(); } });
$("#query-form").addEventListener("submit", submitQuery);
$("#cancel-query").addEventListener("click", () => state.queryController?.abort("user"));
$$("input[name=mode]").forEach(input => input.addEventListener("change", () => { $("#submit-query span").textContent = input.value === "ask" ? "Hỏi đáp" : "Tra cứu"; $("#llm-hint").hidden = input.value !== "ask" || Boolean(state.health?.llm?.ready); if (!$("#llm-hint").hidden) $("#llm-hint").textContent = "Ollama hoặc model local chưa sẵn sàng. Bạn vẫn có thể dùng chế độ Tìm tài liệu."; }));
$("#strategy-select").addEventListener("change", async () => { $("#version-select").value = ""; updateVersions(); await loadCatalog(); });
$("#version-select").addEventListener("change", loadCatalog);
$("#document-search").addEventListener("input", () => renderDocuments());
$("#refresh-catalog").addEventListener("click", async () => { await loadVersions().catch(() => {}); await loadCatalog(); });
$("#admin-auth-form").addEventListener("submit", async event => {
  event.preventDefault(); state.adminToken = $("#admin-token").value.trim();
  const button = $("#admin-auth-form button[type=submit]"); button.disabled = true;
  try {
    await refreshAdmin(); $("#admin-content").hidden = false; $("#admin-disconnect").hidden = false;
    $("#admin-token").value = ""; $("#admin-token").required = false;
    adminMessage("Đã kết nối quản trị", "Quyền thao tác được server kiểm tra cho từng yêu cầu.", "success");
  } catch (error) { state.adminToken = ""; $("#admin-content").hidden = true; adminMessage("Không thể kết nối quản trị", errorText(error), "error"); }
  finally { button.disabled = false; }
});
$("#admin-disconnect").addEventListener("click", () => { state.adminToken = ""; $("#admin-content").hidden = true; $("#admin-disconnect").hidden = true; $("#admin-token").required = true; $("#admin-message").replaceChildren(); });
$("#refresh-admin").addEventListener("click", () => refreshAdmin().catch(error => adminMessage("Không thể làm mới quản trị", errorText(error), "error")));
$("#ingest-form").addEventListener("submit", event => {
  event.preventDefault();
  const body = {source_path: $("#source-path").value.trim(), idempotency_key: crypto.randomUUID(),
    ocr: $("#ingest-ocr").checked, expand_archives: $("#ingest-archives").checked};
  if ($("#metadata-path").value.trim()) body.metadata_path = $("#metadata-path").value.trim();
  adminFormAction(event.currentTarget, "/admin/jobs", body);
});
$("#build-form").addEventListener("submit", event => { event.preventDefault(); adminFormAction(event.currentTarget, "/admin/build", {dataset_id: $("#dataset-id").value.trim(), strategy: $("#build-strategy").value}); });

(async function initialize() {
  await Promise.allSettled([loadHealth(), loadVersions()]);
  await loadCatalog();
  window.setInterval(() => { if (!document.hidden) loadHealth(); }, 60000);
})();
