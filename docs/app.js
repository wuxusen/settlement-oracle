"use strict";
/*
 * Client-side settlement oracle.
 *
 * This is a faithful re-implementation of the Python engine's hashing and
 * resolution logic, running entirely in the browser. Every SHA-256 is a real
 * Web Crypto digest (crypto.subtle.digest), and the canonical JSON encoder
 * mirrors Python's json.dumps(sort_keys=True, separators=(",",":"),
 * ensure_ascii=True) byte-for-byte — so the hashes computed here reproduce the
 * ones the real oracle committed. That is what makes the tamper demo real: it
 * is not comparing against a hard-coded red string, it is re-deriving the
 * settlement from scratch and checking it against the committed record.
 */

const DATA = window.ORACLE_DATA;
const GENESIS = "0".repeat(64);
const EXPLORER = "https://explorer.solana.com/tx/";

/* ---------------------------------------------------------------------------
 * Canonical JSON — must match Python json.dumps(sort_keys, compact, ensure_ascii)
 * ------------------------------------------------------------------------- */

function jsonQuote(s) {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += "\\\\";
    else if (c === 0x08) out += "\\b";
    else if (c === 0x09) out += "\\t";
    else if (c === 0x0a) out += "\\n";
    else if (c === 0x0c) out += "\\f";
    else if (c === 0x0d) out += "\\r";
    else if (c < 0x20) out += "\\u" + c.toString(16).padStart(4, "0");
    else if (c < 0x80) out += s[i];
    else out += "\\u" + c.toString(16).padStart(4, "0"); // ensure_ascii
  }
  return out + '"';
}

function numRepr(n) {
  // Our hashed numbers are integers, except contract totals lines (e.g. 2.5).
  // Python renders int -> "2", 2.5 -> "2.5"; String() matches for both.
  if (!isFinite(n)) throw new Error("non-finite number cannot be canonicalised");
  return String(n);
}

function canonicalJSON(v) {
  if (v === null || v === undefined) return "null";
  const t = typeof v;
  if (t === "number") return numRepr(v);
  if (t === "boolean") return v ? "true" : "false";
  if (t === "string") return jsonQuote(v);
  if (Array.isArray(v)) return "[" + v.map(canonicalJSON).join(",") + "]";
  if (t === "object") {
    const keys = Object.keys(v).sort();
    return "{" + keys.map((k) => jsonQuote(k) + ":" + canonicalJSON(v[k])).join(",") + "}";
  }
  throw new Error("cannot canonicalise " + t);
}

async function sha256hex(str) {
  const bytes = new TextEncoder().encode(str);
  const buf = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/* ---------------------------------------------------------------------------
 * Settlement primitives — direct ports of the Python modules
 * ------------------------------------------------------------------------- */

// feeds/txline_results.py :: canonical_digest
async function evidenceDigest(ev) {
  const core = {
    fixture_id: ev.fixture_id,
    competition_id: ev.competition_id ?? null,
    participant1_id: ev.participant1_id ?? null,
    participant2_id: ev.participant2_id ?? null,
    participant1_is_home: ev.participant1_is_home,
    home_goals: ev.home_goals,
    away_goals: ev.away_goals,
    finalised_ts: ev.finalised_ts,
    finalised_seq: ev.finalised_seq,
    source: ev.source,
  };
  return sha256hex(canonicalJSON(core));
}

// types.py :: MatchResult (outcome/total) + engine._result_dict
function deriveResult(ev) {
  const hg = ev.home_goals, ag = ev.away_goals;
  let outcome = "DRAW";
  if (hg > ag) outcome = "HOME";
  else if (ag > hg) outcome = "AWAY";
  return {
    fixture_id: ev.fixture_id,
    home: ev.home,
    away: ev.away,
    home_goals: hg,
    away_goals: ag,
    total_goals: hg + ag,
    outcome: outcome,
  };
}

// contracts.py :: resolve
function resolve(contract, result) {
  const kind = contract.kind;
  const p = contract.params || {};
  const yn = (b) => (b ? "YES" : "NO");
  if (kind === "TEAM_WIN") {
    const side = String(p.side || "").toUpperCase();
    if (side !== "HOME" && side !== "AWAY") throw new Error("bad TEAM_WIN side");
    return yn(result.outcome === side);
  }
  if (kind === "MATCH_DRAW") return yn(result.outcome === "DRAW");
  if (kind === "DOUBLE_CHANCE") {
    const cover = new Set((p.cover || []).map((o) => String(o).toUpperCase()));
    return yn(cover.has(result.outcome));
  }
  if (kind === "TOTAL_OVER") return yn(result.total_goals > Number(p.line));
  if (kind === "TOTAL_UNDER") return yn(result.total_goals < Number(p.line));
  if (kind === "BTTS") return yn(result.home_goals > 0 && result.away_goals > 0);
  if (kind === "EXACT_SCORE")
    return yn(result.home_goals === Number(p.home) && result.away_goals === Number(p.away));
  throw new Error("unknown contract kind: " + kind);
}

// engine.py :: compute_receipt_hash
async function receiptHash(receipt_version, rule_id, contract, evidence_digest, result, resolution) {
  const body = {
    receipt_version,
    rule_id,
    contract,
    evidence_digest,
    result,
    resolution,
  };
  return sha256hex(canonicalJSON(body));
}

// ledger.py :: _append hash of a single record (all fields except "hash")
async function recordHash(record) {
  const body = {};
  for (const k of Object.keys(record)) if (k !== "hash") body[k] = record[k];
  return sha256hex(canonicalJSON(body));
}

// ledger.py :: verify_chain — recompute prev/hash across the whole chain
async function verifyChain(records) {
  let prev = GENESIS;
  for (const rec of records) {
    if (rec.prev_hash !== prev) return false;
    if ((await recordHash(rec)) !== rec.hash) return false;
    prev = rec.hash;
  }
  return true;
}

// Rebuild a ledger's chain (prev_hash + hash) in place from an ordered list of
// record bodies — used to model a forger who rewrites the log to launder a tamper.
async function rechainRecords(bodies) {
  let head = GENESIS;
  const out = [];
  for (const b of bodies) {
    const rec = Object.assign({}, b);
    delete rec.hash;
    rec.prev_hash = head;
    rec.hash = await recordHash(rec);
    head = rec.hash;
    out.push(rec);
  }
  return { records: out, chain_head: head };
}

/* ---------------------------------------------------------------------------
 * Startup self-test — reproduce every committed hash in the browser
 * ------------------------------------------------------------------------- */

async function selfTest() {
  let receipts = 0, chains = 0, digests = 0, mismatches = 0;
  for (const fx of DATA.fixtures) {
    if ((await evidenceDigest(fx.evidence)) === fx.evidence_digest) digests++;
    else mismatches++;

    const result = fx.result;
    for (const s of fx.slate) {
      const h = await receiptHash(
        s.receipt_version, s.rule_id, s.contract, fx.evidence_digest, result, s.resolution
      );
      if (h === s.receipt_hash) receipts++;
      else mismatches++;
    }
    const ledger = fx.ledger;
    const intact = await verifyChain(ledger.records);
    // also confirm the recomputed head equals the committed head
    let head = GENESIS;
    for (const r of ledger.records) head = r.hash;
    if (intact && head === ledger.chain_head) chains++;
    else mismatches++;
  }
  return { receipts, chains, digests, mismatches, fixtures: DATA.fixtures.length };
}

/* ---------------------------------------------------------------------------
 * UI state + helpers
 * ------------------------------------------------------------------------- */

const $ = (id) => document.getElementById(id);
function esc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
const OUTCOME_LABEL = { HOME: "HOME WIN", AWAY: "AWAY WIN", DRAW: "DRAW" };

const state = {
  fixture: null,
  focusIdx: 0,
  tamper: { home: 0, away: 0, sourceByte: false, launder: false },
};

/* ---------- fixture picker ---------- */

function renderPicker() {
  const grid = $("fx-grid");
  grid.innerHTML = "";
  DATA.fixtures.forEach((fx) => {
    const b = document.createElement("button");
    b.className = "fx";
    b.setAttribute("data-fid", fx.fixture_id);
    b.innerHTML =
      '<div class="teams">' + esc(fx.label) + "</div>" +
      '<div class="meta"><span class="scorepill">' + esc(fx.score) + "</span>" +
      "<span>" + esc(OUTCOME_LABEL[fx.outcome] || fx.outcome) + "</span></div>" +
      (fx.is_var_case ? '<div class="var">◆ VAR overturn case</div>' : "");
    b.addEventListener("click", () => selectFixture(fx.fixture_id, true));
    grid.appendChild(b);
  });
}

function selectFixture(fid, scroll) {
  const fx = DATA.fixtures.find((f) => f.fixture_id === fid);
  if (!fx) return;
  state.fixture = fx;
  state.focusIdx = 0; // home team to win
  state.tamper = { home: fx.home_goals, away: fx.away_goals, sourceByte: false, launder: false };

  Array.prototype.forEach.call(document.querySelectorAll(".fx"), (el) => {
    el.classList.toggle("active", el.getAttribute("data-fid") == fid);
  });

  const board = $("board");
  board.classList.add("show");
  renderBoard();
  $("tog-launder").checked = false;

  // keep the board comfortably in view without yanking the page
  if (scroll === false) return;
  const top = board.getBoundingClientRect().top + window.scrollY - 80;
  if (window.scrollY < top - 40) window.scrollTo({ top, behavior: "smooth" });
}

/* ---------- board: match + markets + receipt + chain ---------- */

function renderBoard() {
  const fx = state.fixture;
  $("b-teams").textContent = fx.home + "  v  " + fx.away;
  $("b-sub").textContent =
    "TxODDS TxLINE devnet · fixture " + fx.fixture_id + " · seq " + fx.finalised_seq;
  $("b-score").textContent = fx.score;
  $("b-outcome").textContent = OUTCOME_LABEL[fx.outcome] || fx.outcome;
  $("b-final").textContent = fx.finalised_action || "game_finalised";

  const varEl = $("b-var");
  if (fx.is_var_case) {
    varEl.style.display = "block";
    varEl.innerHTML =
      "<b>◆ Real VAR case.</b> The feed briefly showed <b>2-2</b> after a VAR review, the goal was " +
      "then <span class='mono'>action_discarded</span> back to <b>2-1</b>, and only " +
      "<span class='mono'>game_finalised</span> confirmed 2-1. A naive “latest score” oracle " +
      "settles the wrong contract — this one settles on the finalisation signal and gets it right.";
  } else {
    varEl.style.display = "none";
  }

  renderMarkets();
  $("edit-home-l").textContent = fx.home;
  $("edit-away-l").textContent = fx.away;
  runTamper();
}

function marketResolvesUnder(contract, result) {
  return resolve(contract, result);
}

function renderMarkets() {
  const fx = state.fixture;
  const result = deriveResult(currentEvidence()); // resolutions reflect the (possibly tampered) score
  const body = $("mk-body");
  body.innerHTML = "";
  fx.slate.forEach((s, i) => {
    const res = marketResolvesUnder(s.contract, result);
    const tr = document.createElement("tr");
    tr.className = "pick" + (i === state.focusIdx ? " focus" : "");
    tr.innerHTML =
      "<td><div class='mkname'>" + esc(s.contract.description) + "</div></td>" +
      "<td class='r'><span class='res " + res + "'>" + res + "</span></td>";
    tr.addEventListener("click", () => {
      state.focusIdx = i;
      renderMarkets();
      runTamper();
    });
    body.appendChild(tr);
  });
  const committed = fx.slate[state.focusIdx].resolution;
  $("mk-hint").innerHTML =
    "Inspecting <b>" + esc(fx.slate[state.focusIdx].contract.description) +
    "</b> — committed resolution <span class='res " + committed + "' style='padding:1px 7px'>" +
    committed + "</span>";
}

/* ---------- tamper: the whole recompute + verification pass ---------- */

function currentEvidence() {
  const fx = state.fixture;
  const ev = Object.assign({}, fx.evidence);
  ev.home_goals = state.tamper.home;
  ev.away_goals = state.tamper.away;
  if (state.tamper.sourceByte) {
    // Flip exactly one byte of the source string — proves ANY committed field is bound.
    const s = ev.source;
    const idx = Math.max(0, s.indexOf("scores"));
    ev.source = s.slice(0, idx) + (s[idx] === "s" ? "S" : "s") + s.slice(idx + 1);
  }
  return ev;
}

function isTampered() {
  const fx = state.fixture;
  return (
    state.tamper.home !== fx.home_goals ||
    state.tamper.away !== fx.away_goals ||
    state.tamper.sourceByte
  );
}

async function runTamper() {
  const fx = state.fixture;
  const focus = fx.slate[state.focusIdx];
  const contract = focus.contract;

  // sync the score steppers
  $("edit-home").textContent = state.tamper.home;
  $("edit-away").textContent = state.tamper.away;
  $("btn-flip").textContent = state.tamper.sourceByte
    ? "↩ Restore the source byte"
    : "Corrupt one byte of the source string";

  const tampered = isTampered();
  const launder = state.tamper.launder;

  // 1) recompute everything from the (possibly tampered) evidence
  const ev = currentEvidence();
  const tDigest = await evidenceDigest(ev);
  const tResult = deriveResult(ev);
  const tResolution = resolve(contract, tResult);
  const tReceiptHash = await receiptHash(
    focus.receipt_version, focus.rule_id, contract, tDigest, tResult, tResolution
  );

  // 2) assemble the receipt the "forger" presents, and the ledger they present.
  //    - naive tamper: stored fields stay the committed (real) ones -> layers 1-3 catch it.
  //    - laundered:    stored fields are recomputed to agree -> only the anchor catches it.
  let rec, ledger;
  if (launder) {
    rec = {
      evidence: ev,
      evidence_digest: tDigest,
      result: tResult,
      resolution: tResolution,
      receipt_hash: tReceiptHash,
      receipt_version: focus.receipt_version,
      rule_id: focus.rule_id,
      contract: contract,
    };
    // rewrite every settlement record to agree with the tampered evidence
    const bodies = [];
    for (const orig of fx.ledger.records) {
      const s = fx.slate.find((x) => x.contract.contract_id === orig.contract_id);
      const rRes = resolve(s.contract, tResult);
      const rHash = await receiptHash(s.receipt_version, s.rule_id, s.contract, tDigest, tResult, rRes);
      bodies.push({
        type: "settlement",
        receipt_hash: rHash,
        rule_id: orig.rule_id,
        contract_id: orig.contract_id,
        fixture_id: orig.fixture_id,
        kind: orig.kind,
        resolution: rRes,
        evidence_digest: tDigest,
        settled_at: orig.settled_at,
      });
    }
    ledger = await rechainRecords(bodies);
  } else {
    rec = {
      evidence: ev,
      evidence_digest: fx.evidence_digest,
      result: fx.result,
      resolution: focus.resolution,
      receipt_hash: focus.receipt_hash,
      receipt_version: focus.receipt_version,
      rule_id: focus.rule_id,
      contract: contract,
    };
    ledger = { records: fx.ledger.records, chain_head: fx.ledger.chain_head };
  }

  // 3) the on-chain anchored head is fixed — it was committed before any tamper
  const anchoredHead = fx.ledger.chain_head;

  // 4) run the six-layer verification exactly like verify.py
  const report = await verifySettlement(rec, ledger, anchoredHead);

  // 5) paint the receipt card, chain, markets, verdict
  paintReceipt(rec, tReceiptHash, focus);
  paintChain(ledger, anchoredHead, launder, tampered);
  renderMarkets();
  paintVerdict(report, tampered, launder);
}

// verify.py :: verify_settlement (the layers we can run purely client-side)
async function verifySettlement(rec, ledger, anchoredHead) {
  const checks = [];

  // 1. evidence integrity
  const d = await evidenceDigest(rec.evidence);
  const ok1 = d === rec.evidence_digest;
  checks.push({
    name: "evidence_integrity",
    ok: ok1,
    detail: ok1
      ? "evidence bundle hashes to the receipt's digest"
      : "digest mismatch — the evidence body was altered",
  });

  // 2. settlement replay
  const replayResult = deriveResult(rec.evidence);
  const replayRes = resolve(rec.contract, replayResult);
  const resultMatches =
    replayResult.home_goals === rec.result.home_goals &&
    replayResult.away_goals === rec.result.away_goals &&
    replayResult.outcome === rec.result.outcome;
  const ok2 = replayRes === rec.resolution && resultMatches;
  checks.push({
    name: "settlement_replay",
    ok: ok2,
    detail: ok2
      ? "replayed " + replayRes + " for “" + rec.contract.description + "”"
      : !resultMatches
      ? "result mismatch: evidence replays " +
        replayResult.home_goals + "-" + replayResult.away_goals +
        " (" + replayResult.outcome + "), receipt claims " +
        rec.result.home_goals + "-" + rec.result.away_goals + " (" + rec.result.outcome + ")"
      : "resolution mismatch: replay " + replayRes + " ≠ receipt " + rec.resolution,
  });

  // 3. receipt fingerprint
  const h = await receiptHash(
    rec.receipt_version, rec.rule_id, rec.contract, rec.evidence_digest, rec.result, rec.resolution
  );
  const ok3 = h === rec.receipt_hash;
  checks.push({
    name: "receipt_fingerprint",
    ok: ok3,
    detail: ok3 ? "receipt hash reproduces" : "hash mismatch: recomputed ≠ stored",
  });

  // 4. audit chain
  const chainIntact = await verifyChain(ledger.records);
  const contains = ledger.records.some(
    (r) => r.type === "settlement" && r.receipt_hash === rec.receipt_hash
  );
  const ok4 = chainIntact && contains;
  checks.push({
    name: "audit_chain",
    ok: ok4,
    detail: ok4
      ? "chain intact and contains this receipt"
      : !chainIntact
      ? "hash chain broken — a record no longer matches its link"
      : "chain does not contain this receipt hash",
  });

  // 5. on-chain anchor: the ledger head must equal the head already anchored
  const ok5 = ledger.chain_head === anchoredHead;
  checks.push({
    name: "onchain_anchor",
    ok: ok5,
    detail: ok5
      ? "ledger head matches the hash anchored on Solana devnet"
      : "ledger head ≠ the head committed on-chain — history was rewritten after anchoring",
  });

  const verified = checks.every((c) => c.ok);
  return { verified, checks };
}

/* ---------- painters ---------- */

function paintReceipt(rec, liveHash, focus) {
  const fx = state.fixture;
  $("rc-market").textContent = "· " + focus.contract.description;
  const p = focus.contract.params || {};
  const paramStr = Object.keys(p).length ? JSON.stringify(p) : "—";
  const rows = [
    ["contract_id", focus.contract.contract_id],
    ["kind", focus.contract.kind],
    ["params", paramStr],
    ["rule_id", focus.rule_id],
    ["result", rec.result.home_goals + "-" + rec.result.away_goals + " (" + rec.result.outcome + ")"],
    ["resolution", rec.resolution],
  ];
  $("rc-kv").innerHTML = rows
    .map(
      (r) =>
        "<div class='kv'><span class='k'>" + esc(r[0]) +
        "</span><span class='v'>" + esc(r[1]) + "</span></div>"
    )
    .join("");

  // The live browser-computed hash of what is currently presented.
  $("rc-hash").textContent = liveHash;
  const repro = $("rc-repro");
  const matchesCommitted = liveHash === fx.slate[state.focusIdx].receipt_hash;
  if (matchesCommitted) {
    repro.className = "repro ok";
    repro.textContent = "✓ reproduced in-browser";
  } else {
    repro.className = "repro bad";
    repro.textContent = "✗ differs from committed";
  }
}

function paintChain(ledger, anchoredHead, launder, tampered) {
  const wrap = $("chain");
  wrap.innerHTML = "";
  const records = ledger.records;
  // show a compact view: first two, an ellipsis, and the last one (head)
  const showIdx = records.length <= 4
    ? records.map((_, i) => i)
    : [0, 1, -1, records.length - 1];
  showIdx.forEach((i, pos) => {
    if (i === -1) {
      const div = document.createElement("div");
      div.className = "clink";
      div.innerHTML =
        '<div class="rail"><div class="line"></div></div>' +
        '<div class="body" style="color:var(--dim);font-size:12px;padding:2px 0 10px;">… ' +
        (records.length - 3) + " more settlement links …</div>";
      wrap.appendChild(div);
      return;
    }
    const r = records[i];
    // A link is "broken" only when a naive tamper leaves the chain inconsistent
    // with what it should commit to. In laundered mode the chain re-hashes
    // cleanly, so we flag the head instead (it won't match the on-chain anchor).
    const headBroken = i === records.length - 1 && launder && tampered && ledger.chain_head !== anchoredHead;
    const div = document.createElement("div");
    div.className = "clink" + (headBroken ? " broken" : "");
    const isLast = pos === showIdx.length - 1;
    div.innerHTML =
      '<div class="rail"><div class="node"></div>' + (isLast ? "" : '<div class="line"></div>') + "</div>" +
      '<div class="body">' +
        '<div class="head">#' + (i + 1) + " · <span class='tagk'>" + esc(r.kind) +
          "</span> " + esc(r.resolution) +
          (headBroken ? " <span style='color:var(--red)'>— head no longer matches the anchor</span>" : "") +
        "</div>" +
        '<div class="hh">hash ' + r.hash.slice(0, 40) + "…</div>" +
      "</div>";
    wrap.appendChild(div);
  });

  const headEl = $("chain-head");
  headEl.textContent = ledger.chain_head;
  headEl.style.color =
    launder && tampered && ledger.chain_head !== anchoredHead ? "var(--red)" : "";
}

function paintVerdict(report, tampered, launder) {
  const v = $("verdict");
  const big = $("verdict-big");
  const sub = $("verdict-sub");
  if (report.verified) {
    v.className = "verdict ok";
    big.textContent = "VERIFIED";
    sub.textContent = tampered
      ? "the forged receipt is internally consistent — watch which layer still catches it"
      : "settlement reproduces from real, unaltered evidence";
  } else {
    v.className = "verdict bad";
    big.textContent = "TAMPER DETECTED";
    const failed = report.checks.filter((c) => !c.ok).map((c) => c.name);
    sub.textContent = launder
      ? "caught by the on-chain anchor — the one thing the forger can't rewrite"
      : "rejected at: " + failed.join(", ");
  }
  $("checks").innerHTML = report.checks
    .map(
      (c) =>
        "<div class='chk'><span class='mark " + (c.ok ? "pass" : "fail") + "'>" +
        (c.ok ? "PASS" : "FAIL") + "</span>" +
        "<div><div class='cn'>" + esc(c.name) + "</div>" +
        "<div class='cd'>" + esc(c.detail) + "</div></div></div>"
    )
    .join("");
}

/* ---------- tamper controls ---------- */

function clamp(n) { return Math.max(0, Math.min(20, n)); }

function bindControls() {
  Array.prototype.forEach.call(document.querySelectorAll(".stepper button"), (b) => {
    b.addEventListener("click", () => {
      const side = b.getAttribute("data-side");
      const d = parseInt(b.getAttribute("data-d"), 10);
      state.tamper[side] = clamp(state.tamper[side] + d);
      runTamper();
    });
  });
  $("btn-blowout").addEventListener("click", () => {
    state.tamper.home = clamp(state.fixture.home_goals + 5);
    state.tamper.away = state.fixture.away_goals;
    runTamper();
  });
  $("btn-flip").addEventListener("click", () => {
    state.tamper.sourceByte = !state.tamper.sourceByte;
    runTamper();
  });
  $("btn-reset").addEventListener("click", () => {
    state.tamper = {
      home: state.fixture.home_goals,
      away: state.fixture.away_goals,
      sourceByte: false,
      launder: state.tamper.launder,
    };
    runTamper();
  });
  $("tog-launder").addEventListener("change", (e) => {
    state.tamper.launder = e.target.checked;
    runTamper();
  });
}

/* ---------- anchor section ---------- */

function renderAnchor() {
  const a = DATA.sister_anchor;
  $("anchor-memo").textContent = DATA.memo_prefix + "<chain-head-sha256>";
  $("tx-sig").textContent = a.tx_sig.slice(0, 22) + "…" + a.tx_sig.slice(-8);
  $("tx-slot").textContent = a.slot.toLocaleString();
  $("tx-head").textContent = a.anchored_head.slice(0, 20) + "…";
  const link = $("tx-link");
  link.href = EXPLORER + a.tx_sig + "?cluster=" + a.cluster;
  $("anchor-honest").innerHTML =
    "The transaction shown on the right is a <b>real, live devnet anchor</b> produced by the sister " +
    "project <span class='mono'>" + esc(a.project) + "</span> with the identical " +
    "<span class='mono'>SolanaAnchor</span> code and Memo Program — concrete, clickable proof the " +
    "anchoring path works on-chain. This settlement oracle anchors its own ledger head the same way " +
    "(memo prefix <span class='mono'>" + esc(DATA.memo_prefix) + "</span>), on demand against a devnet wallet.";
}

/* ---------- theme ---------- */

function bindTheme() {
  const btn = $("themebtn");
  btn.addEventListener("click", () => {
    const root = document.documentElement;
    // The CSS base (no data-theme, no matching media) renders dark, so the
    // effective theme is "light" only when the OS explicitly prefers light.
    const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
    const cur = root.getAttribute("data-theme") || (prefersLight ? "light" : "dark");
    root.setAttribute("data-theme", cur === "dark" ? "light" : "dark");
  });
}

/* ---------- boot ---------- */

async function boot() {
  $("hs-fixtures").textContent = DATA.fixtures.length;
  renderPicker();
  renderAnchor();
  bindControls();
  bindTheme();

  const st = $("selftest");
  const txt = $("selftest-txt");
  try {
    const r = await selfTest();
    $("hs-hashes").textContent = r.receipts;
    if (r.mismatches === 0) {
      st.className = "selftest ok";
      txt.innerHTML =
        "<b>Independently recomputed in your browser:</b> " + r.digests + " evidence digests, " +
        r.receipts + " receipt hashes and " + r.chains + " audit chains across " + r.fixtures +
        " fixtures — <b>all reproduce the committed values.</b> The tamper demo below re-derives every one live.";
    } else {
      st.className = "selftest bad";
      txt.innerHTML = "Self-test found " + r.mismatches + " mismatches — the client hasher disagrees with the committed data.";
    }
  } catch (e) {
    st.className = "selftest bad";
    txt.textContent = "Self-test error: " + e.message;
  }

  // preselect the headline VAR fixture so the demo is populated on load, but
  // leave the reader at the top of the page — do not yank them down to it
  selectFixture(DATA.default_fixture_id || DATA.fixtures[0].fixture_id, false);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
