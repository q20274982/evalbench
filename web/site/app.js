// EvalBench demo 站(過渡版)。純靜態:只讀 snapshot/ 底下的 JSON,不連資料庫、不呼叫模型。
// 資料由 web/export_demo.py 產生;這裡只負責顯示,不重算分數也不重做任何判斷。
//
// 讀者是沒看過這個專案的人,所以每個畫面只留「一眼要看懂的東西」,
// 其餘(參數、模型、版本、花費)收在可展開的「技術細節」裡。
//
// 版面仿錯誤追蹤工具的習慣:一起事故 = 清單上的一列,點進去看那起事故的每一次診斷。
//
// 三個畫面(hash 路由):
//   #/  或  #/run/<run_id>                 事故清單(可選看哪一次評測的結果)
//   #/issue/<scenario>[/<run_id>/<file>]   一起事故:結論 + 逐步重放 + 歷次評測
//   #/runs                                 評測紀錄:每一次評測答對幾題
//
// 語言:中文優先,可切英文。字串全部在下面的 STR,畫面碼不直接寫字。

const app = document.getElementById("app");
let INDEX = null;
const traceCache = new Map();

// ---------- 語言 ----------

const STR = {
  zh: {
    "brand.sub": "事故診斷 AI 的評測台",
    "nav.issues": "事故",
    "nav.runs": "評測紀錄",
    "is.pick": "看哪一次評測",
    "is.th.issue": "事故",
    "is.th.answer": "AI 的回答",
    "is.th.history": "歷次評測(舊 → 新)",
    "is.truth": "正確答案",
    "is.more": "另有 {n} 則告警",
    "is.open": "點任一列,看 AI 當時看到什麼、怎麼回答、怎麼評分。",
    "is.rounds": "答對 {c} / {n} 輪",
    "f.notok": "沒答對",
    "sq.absent": "這次評測沒有這題",
    "sq.tip": "{d} 第 {r} 輪:{o}",
    "ev.title": "歷次評測",
    "ev.hint": "點一輪,切換左邊顯示的那一次診斷。",
    "ev.round": "第 {r} 輪",
    "about.title": "關於這起事故",
    "about.alerts": "觸發的告警",
    "about.fault": "故障類別",
    "about.kind": "資源種類",
    "s.input.missing": "這一次評測當時只存了回答與分數,沒有存下交給 AI 的資料,所以這裡無法顯示。",
    "s.input.missing.right": "沒有存",
    "s.retrieval.ids": "這一次評測只存了段落的編號,沒有存段落內容。挑出的 {n} 段是:",
    "s.retrieval.ids.right": "{n} 段(只有編號)",
    "rn.title": "評測紀錄",
    "rn.lede": "每一次評測用同一套題目把 AI 重跑一遍。同樣的題目通常跑 3 輪,看結果穩不穩定。",
    "rn.see": "看這次的每一題 →",
    "run.opt": "{d} · {t} · 答對 {s} / {n} 題",
    "kv.label": "設定名稱",
    "kv.runid": "評測編號",
    "kv.unknown": "當時沒有記錄",
    "pill": "這裡顯示的是錄好的結果,不會即時呼叫 AI",
    "pill.title": "每個畫面都是從當時存下來的紀錄檔畫出來的。",
    "foot": "分數由程式比對標準答案算出,不是由 AI 評分。資料來源:IBM ITBench-Lite 的 Kubernetes 事故。",
    "loading": "載入中…",

    "runs.title": "這個 AI 找得到事故的原因嗎?",
    "runs.lede": "把一次系統事故的現場資料交給 AI,請它指出是哪一個元件出問題,再用程式對答案。",
    "how.1.t": "事故現場",
    "how.1.d": "告警、叢集事件、設定檔。來自 IBM 整理的真實 Kubernetes 事故。",
    "how.2.t": "AI 診斷",
    "how.2.d": "AI 讀完資料,回答「出問題的元件叫什麼名字」。",
    "how.3.t": "對答案",
    "how.3.d": "程式把回答跟 IBM 標的正確答案比對。答對 1 分,答錯 0 分。",
    "runs.pick": "下面是每一次評測。點進去可以看每一題,再點一題可以看 AI 當時看到什麼、答了什麼。",
    "run.tag.retrieval": "有檢索:先從大量資料裡挑出相關的段落",
    "run.tag.fixed": "沒有檢索:只給固定的一小段資料",
    "run.tag.retrieval.short": "有檢索",
    "run.tag.fixed.short": "沒有檢索",
    "run.open": "看每一題 →",
    "run.single": "只跑 1 題的測試({n} 次)",
    "score.one": "答對 {c} / {n} 題",
    "score.round": "第 {r} 輪",
    "score.cell": "{c} / {n} 題",
    "spread.one": "只跑了 1 輪,所以看不出重跑一次會差多少。",
    "spread.many": "同樣的題目跑了 {k} 輪:最少答對 {min} 題,最多 {max} 題。",
    "details": "技術細節",
    "kv.incidents": "題數",
    "kv.incidents.v": "{s} 題 × {k} 輪 = {n} 次診斷",
    "kv.model": "模型",
    "kv.code": "程式版本",
    "kv.code.v": "{c} · 評分程式 {s}",
    "kv.cost": "花費",
    "kv.cost.v": "共 {c} · 輸入 {t} tokens",
    "kv.params": "輸入怎麼組的",
    "kv.parse": "輸出格式讀不懂",
    "kv.parse.v": "{f} / {n} 次(算 0 分,仍計入題數)",
    "kv.cite": "回答有憑據",
    "kv.cite.v": "{p} / {n} 次通過",
    "kv.started": "時間",

    "queue.title": "每一題的結果",
    "queue.lede": "一列是一起事故。點結果標籤可以看那一次診斷的完整過程。",
    "queue.score": "答對幾題",
    "f.all": "全部",
    "f.ok": "答對",
    "f.bad": "答錯",
    "f.warn": "沒有答案",
    "search": "搜尋事故或元件名稱…",
    "count": "顯示 {a} / {b} 題",
    "th.incident": "事故",
    "th.expected": "正確答案",
    "th.answer": "AI 的回答",
    "th.started": "時間",
    "th.result": "結果",
    "out.ok": "答對",
    "out.bad": "答錯",
    "out.warn": "沒有答案",
    "out.round": "第 {r} 輪 · {o}",
    "noanswer": "(輸出讀不懂)",

    "tr.said": "AI 回答",
    "tr.truth": "正確答案",
    "tr.nothing": "(沒有可用的回答)",
    "tr.prev": "← 上一題",
    "tr.next": "下一題 →",
    "tr.round": "第 {r} 輪",
    "s.input": "AI 看到什麼",
    "s.input.why": "交給 AI 的事故現場資料。正確答案不在裡面,只有評分程式讀得到。",
    "s.input.right": "共 {c} 個字元",
    "s.instructions": "給 AI 的指示",
    "s.chars": "{n} 個字元",
    "sec.Alerts": "告警",
    "sec.告警": "告警",
    "sec.K8s events": "叢集事件",
    "s.retrieval": "找回來的資料",
    "s.retrieval.why": "現場資料太多,無法全部交給 AI。系統先挑出最相關的 {n} 段放進去,順序是相關程度。綠色框代表 AI 在回答裡逐字引用了那一段。",
    "s.retrieval.right": "{n} 段 · {c} 段被引用",
    "s.retrieval.none": "這一次評測還沒有檢索功能,AI 只看到上面那一小段固定的資料。",
    "s.retrieval.none.right": "沒有",
    "blk.quoted": "被引用",
    "s.answer": "AI 的回答",
    "s.answer.why": "評分只看「出問題的元件」這一欄。",
    "a.entity": "出問題的元件",
    "a.reason": "AI 的說明",
    "a.conf": "AI 自評的把握",
    "a.abstain": "AI 表示不知道",
    "a.unreadable": "AI 的輸出不是合法的 JSON,讀不出任何欄位。",
    "a.raw": "AI 的原始輸出",
    "fmt.ok": "格式正確",
    "fmt.bad": "格式有問題",
    "fmt.call": "呼叫失敗",
    "s.citation": "回答有沒有憑據",
    "s.citation.why": "AI 列的每一條證據,都必須在「找回來的資料」裡逐字找得到;而且它說的元件名稱要出現在證據裡。這是程式檢查,不是 AI 檢查。",
    "s.citation.note": "這一欄是產生本頁資料時重新檢查的結果。",
    "c.passed": "通過",
    "c.check_failed": "沒過",
    "c.parse_failed": "沒檢查(輸出讀不懂)",
    "c.call_failed": "沒檢查(呼叫失敗)",
    "c.abstained": "AI 表示不知道",
    "e.found": "找得到原文",
    "e.in": "在 {id}",
    "e.missing": "在找回來的資料裡找不到這段文字",
    "e.unchecked": "沒檢查",
    "e.none": "AI 沒有列出任何證據。",
    "s.score": "評分",
    "s.score.why": "答對 1 分,答錯 0 分,沒有部分分數。",
    "sc.result": "判定",
    "sc.truth": "正確答案",
    "sc.rule": "比對規則",
    "sc.rule.none": "沒有規則,名稱要完全一樣",
    "sc.also": "也算對的答案",
    "sc.detail": "比對紀錄",
    "st.root_filter": "回答符合正確答案的比對規則",
    "st.same_service_filter": "回答是同一個服務底下的另一個資源,照評分規格算對",
    "st.root_id": "回答跟正確答案的名稱完全一樣",
    "st.same_service_id": "回答是同一個服務底下的另一個資源,照評分規格算對",
    "st.miss": "AI 指了別的元件",
    "st.no_answer": "沒有可用的回答(輸出讀不懂,或回答不是一個元件名稱)",
    "kv.request": "請求參數",
    "kv.usage": "用量",
    "kv.usage.v": "輸入 {i} · 輸出 {o} tokens · {c}",
    "kv.trace": "紀錄檔",
    "repro": "不花錢的檢查:確認存下來的輸入可以重新組出來",

    "cmp.title": "逐題對照",
    "cmp.lede": "一列是一起事故,一欄是一次評測,一個方格是一輪。點方格可以看那一次診斷。",
    "cmp.note": "這幾次評測的題目不完全相同,設定也不只差一項,所以這裡只列每一題的結果,不計算「整體進步多少」。",
    "cmp.common": "只看每次評測都有的題目",
    "cmp.count": "顯示 {a} 題 · 其中 {b} 題每次評測都有",
    "cmp.absent": "這次沒有這題",
    "cmp.tip": "第 {r} 輪:{o},AI 回答 {e}",
    "lg.ok": "答對",
    "lg.bad": "答錯",
    "lg.warn": "沒有答案",

    "nf.title": "找不到這一頁",
    "nf.back": "回到評測結果",
    "err.title": "這一頁載入失敗",
    "nodata.title": "還沒有資料",
    "nodata.body": "找不到 snapshot/index.json。用這個指令產生:",
  },
  en: {
    "brand.sub": "evaluation console for an incident-diagnosis AI",
    "nav.issues": "Incidents",
    "nav.runs": "Evaluations",
    "is.pick": "Evaluation",
    "is.th.issue": "Incident",
    "is.th.answer": "AI's answer",
    "is.th.history": "History (old → new)",
    "is.truth": "Correct answer",
    "is.more": "+{n} more alerts",
    "is.open": "Click any row to see what the AI was shown, what it answered, and how it was scored.",
    "is.rounds": "{c} of {n} rounds correct",
    "f.notok": "Not correct",
    "sq.absent": "not part of this evaluation",
    "sq.tip": "{d} round {r}: {o}",
    "ev.title": "History",
    "ev.hint": "Click a round to switch the diagnosis shown on the left.",
    "ev.round": "Round {r}",
    "about.title": "About this incident",
    "about.alerts": "Alerts fired",
    "about.fault": "Fault category",
    "about.kind": "Resource kind",
    "s.input.missing": "This evaluation only stored the answer and score at the time, not the data handed to the AI, so it cannot be shown here.",
    "s.input.missing.right": "not stored",
    "s.retrieval.ids": "This evaluation stored only the passage ids, not their content. The {n} passages picked were:",
    "s.retrieval.ids.right": "{n} passages (ids only)",
    "rn.title": "Evaluations",
    "rn.lede": "Each evaluation re-runs the AI over the same set of incidents. The same incidents are usually run 3 times to see how stable the result is.",
    "rn.see": "See every incident →",
    "run.opt": "{d} · {t} · {s} of {n} correct",
    "kv.label": "Config name",
    "kv.runid": "Evaluation id",
    "kv.unknown": "not recorded at the time",
    "pill": "Recorded results — nothing here calls an AI live",
    "pill.title": "Every screen is drawn from the trace files saved at the time.",
    "foot": "Scores are computed by code against the ground truth, not graded by an AI. Data: Kubernetes incidents from IBM ITBench-Lite.",
    "loading": "Loading…",

    "runs.title": "Can this AI find the cause of an incident?",
    "runs.lede": "Give an AI the evidence from a system incident, ask it which component is at fault, then check the answer with code.",
    "how.1.t": "The incident",
    "how.1.d": "Alerts, cluster events, config. Real Kubernetes incidents curated by IBM.",
    "how.2.t": "AI diagnosis",
    "how.2.d": "The AI reads the data and names the component at fault.",
    "how.3.t": "Check the answer",
    "how.3.d": "Code compares the answer with IBM's labelled root cause. 1 if right, 0 if wrong.",
    "runs.pick": "Each evaluation is listed below. Open one to see every incident, then open an incident to see what the AI was shown and what it answered.",
    "run.tag.retrieval": "With retrieval: relevant passages are picked out of a large snapshot first",
    "run.tag.fixed": "No retrieval: the AI only gets a small fixed slice of the data",
    "run.tag.retrieval.short": "With retrieval",
    "run.tag.fixed.short": "No retrieval",
    "run.open": "See every incident →",
    "run.single": "Single-incident test runs ({n})",
    "score.one": "{c} of {n} correct",
    "score.round": "Round {r}",
    "score.cell": "{c} / {n}",
    "spread.one": "Run once only, so there is no way to tell how much a re-run would differ.",
    "spread.many": "Same incidents run {k} times: {min} correct at worst, {max} at best.",
    "details": "Technical details",
    "kv.incidents": "Incidents",
    "kv.incidents.v": "{s} incidents × {k} round(s) = {n} diagnoses",
    "kv.model": "Model",
    "kv.code": "Code version",
    "kv.code.v": "{c} · scorer {s}",
    "kv.cost": "Cost",
    "kv.cost.v": "{c} total · {t} input tokens",
    "kv.params": "How the input was built",
    "kv.parse": "Output could not be parsed",
    "kv.parse.v": "{f} of {n} (scored 0, still counted)",
    "kv.cite": "Answer backed by evidence",
    "kv.cite.v": "{p} of {n} passed",
    "kv.started": "Time",

    "queue.title": "Result for each incident",
    "queue.lede": "One row per incident. Click a result to see that diagnosis step by step.",
    "queue.score": "Correct answers",
    "f.all": "All",
    "f.ok": "Correct",
    "f.bad": "Wrong",
    "f.warn": "No answer",
    "search": "Search incident or component…",
    "count": "Showing {a} of {b} incidents",
    "th.incident": "Incident",
    "th.expected": "Correct answer",
    "th.answer": "AI's answer",
    "th.started": "Time",
    "th.result": "Result",
    "out.ok": "Correct",
    "out.bad": "Wrong",
    "out.warn": "No answer",
    "out.round": "Round {r} · {o}",
    "noanswer": "(unreadable output)",

    "tr.said": "AI answered",
    "tr.truth": "Correct answer",
    "tr.nothing": "(no usable answer)",
    "tr.prev": "← Previous",
    "tr.next": "Next →",
    "tr.round": "Round {r}",
    "s.input": "What the AI saw",
    "s.input.why": "The incident data handed to the AI. The correct answer is not in it; only the scorer can read that.",
    "s.input.right": "{c} characters",
    "s.instructions": "Instructions to the AI",
    "s.chars": "{n} characters",
    "sec.Alerts": "Alerts",
    "sec.告警": "Alerts",
    "sec.K8s events": "Cluster events",
    "s.retrieval": "What was retrieved",
    "s.retrieval.why": "There is too much data to hand over in full. The system picks the {n} most relevant passages, in order of relevance. A green border means the AI quoted that passage word for word.",
    "s.retrieval.right": "{n} passages · {c} quoted",
    "s.retrieval.none": "This evaluation predates retrieval. The AI only saw the small fixed slice above.",
    "s.retrieval.none.right": "none",
    "blk.quoted": "quoted",
    "s.answer": "The AI's answer",
    "s.answer.why": "Only the “component at fault” field is scored.",
    "a.entity": "Component at fault",
    "a.reason": "AI's explanation",
    "a.conf": "AI's stated confidence",
    "a.abstain": "AI said it does not know",
    "a.unreadable": "The AI's output was not valid JSON, so no fields could be read.",
    "a.raw": "Raw AI output",
    "fmt.ok": "well-formed",
    "fmt.bad": "malformed",
    "fmt.call": "call failed",
    "s.citation": "Is the answer backed by evidence?",
    "s.citation.why": "Every piece of evidence the AI lists must appear word for word in the retrieved passages, and the component it names must appear in that evidence. Checked by code, not by an AI.",
    "s.citation.note": "This result was re-checked when the data for this page was generated.",
    "c.passed": "Passed",
    "c.check_failed": "Failed",
    "c.parse_failed": "Not checked (unreadable output)",
    "c.call_failed": "Not checked (call failed)",
    "c.abstained": "AI said it does not know",
    "e.found": "found word for word",
    "e.in": "in {id}",
    "e.missing": "this text is not in any retrieved passage",
    "e.unchecked": "not checked",
    "e.none": "The AI listed no evidence.",
    "s.score": "Score",
    "s.score.why": "1 if right, 0 if wrong. No partial credit.",
    "sc.result": "Verdict",
    "sc.truth": "Correct answer",
    "sc.rule": "Matching rule",
    "sc.rule.none": "none — the name must match exactly",
    "sc.also": "Also accepted",
    "sc.detail": "Matcher log",
    "st.root_filter": "The answer matches the rule for the correct component",
    "st.same_service_filter": "The answer is another resource of the same service, which the scoring spec accepts",
    "st.root_id": "The answer is exactly the name of the correct component",
    "st.same_service_id": "The answer is another resource of the same service, which the scoring spec accepts",
    "st.miss": "The AI named a different component",
    "st.no_answer": "No usable answer (unreadable output, or not a component name)",
    "kv.request": "Request parameters",
    "kv.usage": "Usage",
    "kv.usage.v": "{i} in · {o} out tokens · {c}",
    "kv.trace": "Trace file",
    "repro": "Free check that the stored input can be rebuilt:",

    "cmp.title": "Incident by incident",
    "cmp.lede": "One row per incident, one column per evaluation, one square per round. Click a square to open that diagnosis.",
    "cmp.note": "These evaluations do not cover exactly the same incidents and differ in more than one setting, so this page lists each result and does not compute an overall improvement.",
    "cmp.common": "Only incidents present in every evaluation",
    "cmp.count": "Showing {a} incidents · {b} are in every evaluation",
    "cmp.absent": "not in this one",
    "cmp.tip": "Round {r}: {o}, AI answered {e}",
    "lg.ok": "Correct",
    "lg.bad": "Wrong",
    "lg.warn": "No answer",

    "nf.title": "Page not found",
    "nf.back": "Back to results",
    "err.title": "This page failed to load",
    "nodata.title": "No data yet",
    "nodata.body": "snapshot/index.json is missing. Generate it with:",
  },
};

// 順序:網址的 ?lang=en → 上次選的 → 中文
function readLang() {
  const q = new URLSearchParams(location.search).get("lang");
  if (q === "zh" || q === "en") return q;
  try {
    const saved = localStorage.getItem("evalbench.lang");
    if (saved === "zh" || saved === "en") return saved;
  } catch {
    // 讀不到就用預設
  }
  return "zh";
}
let LANG = readLang();

function t(key, vars) {
  let s = STR[LANG][key] ?? STR.zh[key] ?? key;
  for (const [k, v] of Object.entries(vars || {})) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

function setLang(lang) {
  LANG = lang;
  try {
    localStorage.setItem("evalbench.lang", lang);
  } catch {
    // 存不了也照樣切換
  }
  applyStatic();
  route();
}

// index.html 裡寫死的那幾段字
function applyStatic() {
  document.documentElement.lang = LANG === "zh" ? "zh-Hant" : "en";
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of document.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
  for (const b of document.querySelectorAll("[data-lang]")) b.classList.toggle("on", b.dataset.lang === LANG);
}

// ---------- 小工具 ----------

// 建 DOM 節點。文字一律走 textContent,資料裡的任何字元都不會被當成 HTML。
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const fmtCost = (c) => (c === null || c === undefined ? "—" : "$" + c.toFixed(4));
const fmtInt = (n) => (n === null || n === undefined ? "—" : n.toLocaleString("en-US"));
const fmtDate = (iso) => iso.slice(0, 10);
const fmtDateTime = (iso) => iso.slice(0, 16).replace("T", " ") + " UTC";
const shortCommit = (c) => (c ? c.slice(0, 7) : "?");
const scenarioNum = (s) => Number(s.split("-")[1]);
const showEntity = (e) => (e === null || e === undefined ? t("noanswer") : e === "" ? '""' : e);

// 一筆結果的三種樣子:答對 / 答錯 / 沒有可用的答案
function outcomeKind(row) {
  if (row.score === 1) return "ok";
  return row.stage === "no_answer" ? "warn" : "bad";
}

function badge(kind, text) {
  return h("span", { class: "badge " + kind }, text);
}

function rowsOf(runId) {
  return INDEX.rows.filter((r) => r.run_id === runId);
}

function kv(...pairs) {
  return h(
    "dl",
    { class: "kv" },
    pairs.filter(Boolean).map(([k, v, mono]) => [h("dt", {}, k), h("dd", { class: mono ? "mono" : "" }, v)]),
  );
}

function fold(title, ...body) {
  return h("details", { class: "fold" }, h("summary", {}, title), h("div", { class: "fold-body" }, body));
}

function paramText(params) {
  return Object.entries(params || {})
    .map(([k, v]) => `${k}=${v}`)
    .join("  ");
}

// 分數一律按輪次分開列;多輪時補一句最少到最多,單輪時明講看不出重跑的差距
function scoreBlock(run) {
  const single = run.per_round.length === 1;
  return h(
    "div",
    { class: "rounds" },
    run.per_round.map((p) =>
      h(
        "div",
        { class: "round-row" + (single ? " single" : "") },
        single ? null : h("span", { class: "muted" }, t("score.round", { r: p.round })),
        h("div", { class: "bar" }, h("i", { style: `width:${(100 * p.correct) / p.total}%` })),
        h("span", { class: "num" }, single ? t("score.one", { c: p.correct, n: p.total }) : t("score.cell", { c: p.correct, n: p.total })),
      ),
    ),
    h("div", { class: "muted small" }, spreadText(run)),
  );
}

function spreadText(run) {
  if (run.per_round.length === 1) return t("spread.one");
  const c = run.per_round.map((p) => p.correct);
  return t("spread.many", { k: run.per_round.length, min: Math.min(...c), max: Math.max(...c) });
}

// ---------- 讀單筆紀錄與畫檢索段落 ----------

async function loadTrace(runId, file) {
  const key = runId + "/" + file;
  if (!traceCache.has(key)) {
    const res = await fetch(`snapshot/traces/${runId}/${file}`);
    if (!res.ok) throw new Error(`trace not found: ${key}`);
    traceCache.set(key, await res.json());
  }
  return traceCache.get(key);
}

// 引用檢查回報的位置長這樣:`Scenario-102/objects/000562 [1203:1291]`
function parseLocated(at) {
  const m = /^(.*) \[(\d+):(\d+)\]$/.exec(at || "");
  return m ? { id: m[1], start: Number(m[2]), end: Number(m[3]) } : null;
}

// 把一段檢索文字畫出來,被引用的區段加上標記
function blockText(text, ranges) {
  const pre = h("pre", {});
  let pos = 0;
  for (const r of [...ranges].sort((a, b) => a.start - b.start)) {
    if (r.start < pos) continue;
    pre.append(text.slice(pos, r.start), h("mark", {}, text.slice(r.start, r.end)));
    pos = r.end;
  }
  pre.append(text.slice(pos));
  return pre;
}

// 檢索段落開頭幾行是 `kind: ...`、`namespace: ...`、`name: ...`,拿來當摘要
function blockHeadline(text) {
  const head = {};
  for (const line of text.split("\n").slice(0, 6)) {
    const m = /^(kind|namespace|name): (.*)$/.exec(line);
    if (m) head[m[1]] = m[2];
  }
  return [head.kind, head.name].filter(Boolean).join(" · ") || null;
}

function citationBadge(status) {
  const kind = status === "passed" ? "ok" : status === "check_failed" ? "bad" : "warn";
  return badge(kind, t("c." + status));
}

function formatText(parse) {
  if (!parse) return t("fmt.call");
  return parse.verdict === "ok" ? t("fmt.ok") : t("fmt.bad");
}

// ---------- 共用 ----------

// 題數大於 1 的才算一次評測;只跑 1 題的是連線測試,不列進來
const batches = () => INDEX.runs.filter((r) => r.n_traces > 1);
const issueHref = (row) => `#/issue/${row.scenario}/${row.run_id}/${row.file}`;
const runTag = (run, short) => t((run.has_retrieval ? "run.tag.retrieval" : "run.tag.fixed") + (short ? ".short" : ""));
const scoreShort = (run) => run.per_round.map((p) => p.correct).join(" / ");

// 一題在一次評測裡的狀態。多輪時不合併成一個對錯,照實寫「答對幾輪」。
function statusOf(rs) {
  const c = rs.reduce((a, r) => a + r.score, 0);
  const n = rs.length;
  const kind = c === n ? "ok" : c > 0 ? "mixed" : rs.every((r) => r.stage === "no_answer") ? "warn" : "bad";
  const label = n === 1 ? t("out." + kind) : t("is.rounds", { c, n });
  return { kind, label, allCorrect: c === n };
}

function issueTitle(scenario) {
  const alerts = (INDEX.issues[scenario] || {}).alerts || [];
  if (!alerts.length) return { title: scenario, more: 0 };
  return { title: alerts[0].description || alerts[0].id || scenario, more: alerts.length - 1 };
}

// 一題在每一次評測的結果:一次評測一組方格,一輪一格,由舊到新
function historySquares(scenario, cellOf) {
  return h(
    "span",
    { class: "history" },
    batches().map((run) => {
      const rs = cellOf.get(run.run_id + "|" + scenario);
      if (!rs) return h("span", { class: "sqgroup absent", title: `${fmtDate(run.created_at)} ${t("sq.absent")}` }, "·");
      return h(
        "span",
        { class: "sqgroup" },
        rs.map((r) =>
          h("i", {
            class: "dot " + outcomeKind(r),
            title: t("sq.tip", { d: fmtDate(run.created_at), r: r.round, o: t("out." + outcomeKind(r)) }),
          }),
        ),
      );
    }),
  );
}

function cellIndex() {
  const cell = new Map();
  for (const r of INDEX.rows) {
    const key = r.run_id + "|" + r.scenario;
    if (!cell.has(key)) cell.set(key, []);
    cell.get(key).push(r);
  }
  return cell;
}

function runMeta(run) {
  const parseFails = Object.entries(run.verdicts)
    .filter(([k]) => k !== "ok")
    .reduce((a, [, v]) => a + v, 0);
  return fold(
    t("details"),
    kv(
      [t("kv.started"), fmtDateTime(run.created_at)],
      [t("kv.incidents"), t("kv.incidents.v", { s: run.n_scenarios, k: run.rounds.length, n: run.n_traces })],
      [t("kv.parse"), t("kv.parse.v", { f: parseFails, n: run.n_traces })],
      run.citation ? [t("kv.cite"), t("kv.cite.v", { p: run.citation.passed || 0, n: run.n_traces })] : null,
      [t("kv.model"), run.model || t("kv.unknown"), Boolean(run.model)],
      run.label ? [t("kv.label"), run.label, true] : [t("kv.params"), paramText(run.slice_params), true],
      [t("kv.code"), run.commit ? t("kv.code.v", { c: shortCommit(run.commit), s: run.scoring_sha12 }) : t("kv.unknown"), Boolean(run.commit)],
      [t("kv.cost"), t("kv.cost.v", { c: fmtCost(run.cost), t: fmtInt(run.prompt_tokens) })],
      [t("kv.runid"), run.run_id, true],
    ),
  );
}

// ---------- 事故清單 ----------

function viewIssues(runId) {
  const runs = batches();
  const run = runs.find((r) => r.run_id === runId) || runs[runs.length - 1];
  const cellOf = cellIndex();
  const scenarios = [...new Set(rowsOf(run.run_id).map((r) => r.scenario))].sort((a, b) => scenarioNum(a) - scenarioNum(b));
  const info = new Map(
    scenarios.map((s) => {
      const rs = cellOf.get(run.run_id + "|" + s);
      return [s, { rs, status: statusOf(rs), ...issueTitle(s) }];
    }),
  );
  const nOk = scenarios.filter((s) => info.get(s).status.allCorrect).length;

  const state = { filter: "all", q: "" };
  const list = h("div", { class: "issue-list" });
  const count = h("span", { class: "muted small" });

  function draw() {
    const q = state.q.trim().toLowerCase();
    const shown = scenarios.filter((s) => {
      const it = info.get(s);
      if (state.filter === "ok" && !it.status.allCorrect) return false;
      if (state.filter === "notok" && it.status.allCorrect) return false;
      if (!q) return true;
      return [s, it.title, it.rs[0].expected, ...it.rs.map((r) => r.entity || "")].join(" ").toLowerCase().includes(q);
    });
    list.replaceChildren(
      h(
        "div",
        { class: "issue-head" },
        h("span", {}, t("is.th.issue")),
        h("span", {}, t("is.th.answer")),
        h("span", {}, t("is.th.history")),
        h("span", {}),
      ),
      ...shown.map((s) => {
        const it = info.get(s);
        const answers = [...new Set(it.rs.map((r) => showEntity(r.entity)))];
        return h(
          "a",
          { class: "issue-row k-" + it.status.kind, href: issueHref(it.rs[0]) },
          h(
            "div",
            { class: "issue-main" },
            h("div", { class: "issue-title" }, it.title, it.more ? h("span", { class: "muted small" }, "  " + t("is.more", { n: it.more })) : null),
            h("div", { class: "issue-sub" }, s, " · ", t("is.truth"), " ", h("span", { class: "mono" }, it.rs[0].expected)),
          ),
          h("div", { class: "issue-answer" }, badge(it.status.kind, it.status.label), h("div", { class: "mono" }, answers.join(" / "))),
          h("div", {}, historySquares(s, cellOf)),
          h("span", { class: "chev", "aria-hidden": "true" }, "›"),
        );
      }),
    );
    count.textContent = t("count", { a: shown.length, b: scenarios.length });
  }

  const tabs = [
    ["all", `${t("f.all")} ${scenarios.length}`],
    ["notok", `${t("f.notok")} ${scenarios.length - nOk}`],
    ["ok", `${t("f.ok")} ${nOk}`],
  ];
  const seg = h(
    "div",
    { class: "seg" },
    tabs.map(([key, label]) =>
      h(
        "button",
        {
          class: key === state.filter ? "on" : "",
          onclick: (e) => {
            state.filter = key;
            for (const b of seg.children) b.classList.toggle("on", b === e.currentTarget);
            draw();
          },
        },
        label,
      ),
    ),
  );

  const picker = h(
    "select",
    {
      onchange: (e) => {
        location.hash = "#/run/" + e.target.value;
      },
    },
    [...runs].reverse().map((r) =>
      h(
        "option",
        { value: r.run_id, selected: r.run_id === run.run_id },
        t("run.opt", { d: fmtDate(r.created_at), t: runTag(r, true), s: scoreShort(r), n: r.per_round[0].total }),
      ),
    ),
  );

  draw();
  return h(
    "div",
    {},
    h("h1", {}, t("runs.title")),
    h("p", { class: "lede" }, t("runs.lede")),
    h(
      "ol",
      { class: "how" },
      [1, 2, 3].map((i) => h("li", {}, h("strong", {}, t(`how.${i}.t`)), h("span", {}, t(`how.${i}.d`)))),
    ),
    h(
      "div",
      { class: "card runbar" },
      h("label", { class: "pick" }, h("span", { class: "label" }, t("is.pick")), picker),
      h("div", { class: "runbar-score" }, h("div", { class: "muted small" }, runTag(run)), scoreBlock(run)),
      runMeta(run),
    ),
    h(
      "div",
      { class: "toolbar" },
      seg,
      h("input", {
        type: "search",
        placeholder: t("search"),
        oninput: (e) => {
          state.q = e.target.value;
          draw();
        },
      }),
      count,
      h(
        "span",
        { class: "legend" },
        ["ok", "bad", "warn"].map((k) => h("span", {}, h("i", { class: "dot " + k }), t("lg." + k))),
      ),
    ),
    h("p", { class: "muted small", style: "margin:0 0 8px" }, t("is.open")),
    list,
  );
}

// ---------- 一起事故 ----------

async function viewIssue(scenario, runId, file) {
  const mine = INDEX.rows.filter((r) => r.scenario === scenario);
  if (!mine.length) return notFound();
  const order = new Map(INDEX.runs.map((r, i) => [r.run_id, i]));
  mine.sort((a, b) => order.get(a.run_id) - order.get(b.run_id) || a.round - b.round);

  // 沒指定就看最新一次評測的第 1 輪
  const latestRun = mine[mine.length - 1].run_id;
  const me = mine.find((r) => r.run_id === runId && r.file === file) || mine.find((r) => r.run_id === latestRun);
  const tr = await loadTrace(me.run_id, me.file);
  const kind = outcomeKind(me);
  const parsed = tr.parse && tr.parse.parsed && typeof tr.parse.parsed === "object" ? tr.parse.parsed : null;
  const gt = tr.ground_truth;
  const meta = INDEX.issues[scenario] || { alerts: [], fault: {} };
  const { title } = issueTitle(scenario);

  // 哪些檢索段落被 evidence 逐字引用到
  const cited = new Map();
  for (const e of tr.citation ? tr.citation.evidence : []) {
    const loc = parseLocated(e.located_at);
    if (!loc) continue;
    if (!cited.has(loc.id)) cited.set(loc.id, []);
    cited.get(loc.id).push(loc);
  }
  const blockEls = new Map();
  function jumpToBlock(id) {
    const el = blockEls.get(id);
    if (!el) return;
    el.open = true;
    el.scrollIntoView({ block: "center" });
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1500);
  }
  const sectionTitle = (name) => STR[LANG]["sec." + name] ?? name;

  // 1 AI 看到什麼
  const inputPanel = tr.prompt
    ? panel(
        1,
        t("s.input"),
        t("s.input.right", { c: fmtInt(tr.prompt_chars) }),
        t("s.input.why"),
        tr.prompt.sections
          .filter((s) => s.text !== null)
          .map((s) =>
            h(
              "details",
              {},
              h("summary", {}, h("strong", {}, sectionTitle(s.title)), h("span", { class: "muted" }, t("s.chars", { n: fmtInt(s.chars) }))),
              h("pre", {}, s.text),
            ),
          ),
        h("details", {}, h("summary", {}, h("strong", {}, t("s.instructions"))), h("pre", {}, tr.prompt.instructions)),
      )
    : panel(1, t("s.input"), t("s.input.missing.right"), t("s.input.missing"));

  // 2 找回來的資料
  let retrievalPanel;
  if (tr.retrieval) {
    retrievalPanel = panel(
      2,
      t("s.retrieval"),
      t("s.retrieval.right", { n: tr.retrieval.length, c: cited.size }),
      t("s.retrieval.why", { n: tr.retrieval.length }),
      tr.retrieval.map((b, i) => {
        const ranges = cited.get(b.id) || [];
        const el = h(
          "details",
          { class: ranges.length ? "cited" : "" },
          h(
            "summary",
            {},
            h("span", { class: "muted" }, "#" + (i + 1)),
            h("span", {}, blockHeadline(b.text) || b.id),
            ranges.length ? badge("ok", t("blk.quoted")) : null,
            h("span", { class: "muted mono" }, b.id),
          ),
          blockText(b.text, ranges),
        );
        blockEls.set(b.id, el);
        return el;
      }),
    );
  } else if (tr.retrieval_ids && tr.retrieval_ids.length) {
    retrievalPanel = panel(
      2,
      t("s.retrieval"),
      t("s.retrieval.ids.right", { n: tr.retrieval_ids.length }),
      t("s.retrieval.ids", { n: tr.retrieval_ids.length }),
      h("pre", { class: "free" }, tr.retrieval_ids.map((id, i) => `#${i + 1}  ${id}`).join("\n")),
    );
  } else {
    retrievalPanel = panel(2, t("s.retrieval"), t("s.retrieval.none.right"), t("s.retrieval.none"));
  }

  // 3 AI 的回答
  const answerPanel = panel(
    3,
    t("s.answer"),
    formatText(tr.parse),
    t("s.answer.why"),
    parsed
      ? [
          h("div", { class: "label" }, t("a.entity")),
          h("div", { class: "answer-entity" }, showEntity(parsed.root_cause_entity)),
          h("div", { class: "label", style: "margin-top:12px" }, t("a.reason")),
          h("p", {}, String(parsed.root_cause ?? "—")),
          parsed.abstain ? h("p", {}, badge("warn", t("a.abstain"))) : null,
        ]
      : h("p", { class: "muted" }, t("a.unreadable")),
    fold(
      t("details"),
      parsed ? kv([t("a.conf"), String(parsed.confidence ?? "—")]) : null,
      h("div", { class: "label", style: "margin:10px 0 4px" }, t("a.raw")),
      h("pre", { class: "free" }, tr.response.raw ?? String(tr.response.error ?? "—")),
    ),
  );

  // 4 回答有沒有憑據(只有存了檢索段落內容的那幾次評測才有)
  const citationPanel = tr.citation
    ? panel(
        4,
        t("s.citation"),
        citationBadge(tr.citation.status),
        t("s.citation.why"),
        tr.citation.evidence.length
          ? h(
              "ul",
              { class: "evidence" },
              tr.citation.evidence.map((e) => {
                const loc = parseLocated(e.located_at);
                return h(
                  "li",
                  {},
                  h("div", { class: "q" }, e.evidence === "" ? '""' : e.evidence),
                  e.found === null
                    ? badge("plain", t("e.unchecked"))
                    : e.found
                      ? [
                          badge("ok", t("e.found")),
                          " ",
                          h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); jumpToBlock(loc.id); } }, t("e.in", { id: loc.id })),
                        ]
                      : badge("bad", t("e.missing")),
                );
              }),
            )
          : h("p", { class: "muted" }, t("e.none")),
        h("p", { class: "muted small", style: "margin:10px 0 0" }, t("s.citation.note")),
      )
    : null;

  // 5 評分
  const scorePanel = panel(
    tr.citation ? 5 : 4,
    t("s.score"),
    badge(kind, t("out." + kind)),
    t("s.score.why"),
    kv([t("sc.result"), t("st." + tr.score.stage)], [t("tr.said"), showEntity(me.entity), true], [t("sc.truth"), gt.root.id, true]),
    fold(
      t("details"),
      kv(
        [t("sc.rule"), gt.root.filters.length ? gt.root.filters.join("   ") : t("sc.rule.none"), true],
        gt.accepted_members.length ? [t("sc.also"), gt.accepted_members.map((m) => m.id).join(", "), true] : null,
        [t("sc.detail"), tr.score.detail, true],
      ),
    ),
  );

  // 這一次診斷的技術細節;完整紀錄檔才附重放指令
  const tech = fold(
    t("details"),
    kv(
      [t("kv.started"), fmtDateTime(tr.created_at)],
      [t("kv.model"), tr.request.model ? `${tr.request.model}${tr.request.provider ? " · " + tr.request.provider.join(", ") : ""}` : t("kv.unknown"), Boolean(tr.request.model)],
      tr.request.model ? [t("kv.request"), `temperature=${tr.request.temperature}  seed=${tr.request.seed}  max_tokens=${tr.request.max_tokens}`, true] : null,
      [t("kv.params"), paramText(tr.slice_params), true],
      [t("kv.code"), tr.versions.commit ? t("kv.code.v", { c: tr.versions.commit, s: tr.versions.scoring_sha12 }) : t("kv.unknown"), Boolean(tr.versions.commit)],
      [t("kv.usage"), t("kv.usage.v", { i: fmtInt(tr.usage.prompt_tokens), o: fmtInt(tr.usage.completion_tokens), c: fmtCost(tr.usage.cost) })],
      tr.trace_id ? [t("kv.trace"), `traces/${tr.run_id}/${me.file}`, true] : null,
    ),
    tr.trace_id
      ? [
          h("p", { class: "muted small", style: "margin:12px 0 4px" }, t("repro")),
          h("pre", { class: "free" }, `uv run python src/replay.py check traces/${tr.run_id}/${me.file}`),
        ]
      : null,
  );

  // 右欄:歷次評測(新 → 舊),點一輪就換下面顯示的那一次
  const byRun = new Map();
  for (const r of mine) {
    if (!byRun.has(r.run_id)) byRun.set(r.run_id, []);
    byRun.get(r.run_id).push(r);
  }
  const events = h(
    "div",
    { class: "events" },
    [...byRun.entries()].reverse().map(([rid, rs]) => {
      const run = INDEX.runs.find((r) => r.run_id === rid);
      return h(
        "div",
        { class: "event" + (rid === me.run_id ? " cur" : "") },
        h("div", {}, h("strong", {}, fmtDate(run.created_at)), " ", h("span", { class: "muted small" }, runTag(run, true))),
        run.label ? h("div", { class: "muted mono", style: "font-size:11px" }, run.label) : null,
        h(
          "div",
          { class: "event-rounds" },
          rs.map((r) => {
            const k = outcomeKind(r);
            return h(
              "a",
              { href: issueHref(r), class: "badge " + k + (r === me ? " sel" : ""), title: showEntity(r.entity) },
              rs.length > 1 ? t("ev.round", { r: r.round }) + " · " : "",
              t("out." + k),
            );
          }),
        ),
      );
    }),
  );

  const about = h(
    "div",
    { class: "card side" },
    h("h2", {}, t("about.title")),
    kv([t("is.truth"), gt.root.id, true], gt.root.kind ? [t("about.kind"), gt.root.kind] : null, meta.fault.category ? [t("about.fault"), [meta.fault.category, meta.fault.mechanism].filter(Boolean).join(" · ")] : null),
    meta.alerts.length
      ? [
          h("div", { class: "label", style: "margin-top:10px" }, t("about.alerts")),
          h("ul", { class: "alerts" }, meta.alerts.map((a) => h("li", {}, h("span", { class: "mono" }, a.id), a.description ? h("div", { class: "muted small" }, a.description) : null))),
        ]
      : null,
  );

  return h(
    "div",
    {},
    h("div", { class: "crumbs" }, h("a", { href: "#/run/" + me.run_id }, t("nav.issues")), " / ", scenario),
    h("h1", {}, title),
    h("div", { class: "muted small" }, scenario),
    h(
      "div",
      { class: "verdict v-" + kind },
      h("div", { class: "verdict-badge" }, t("out." + kind)),
      h("div", {}, h("div", { class: "label" }, t("tr.said")), h("div", { class: "answer-entity" }, me.entity !== null ? showEntity(me.entity) : t("tr.nothing"))),
      h("div", {}, h("div", { class: "label" }, t("tr.truth")), h("div", { class: "answer-entity" }, gt.root.id)),
    ),
    h(
      "div",
      { class: "issue-layout" },
      h("div", {}, inputPanel, retrievalPanel, answerPanel, citationPanel, scorePanel, tech),
      h("aside", {}, h("div", { class: "card side" }, h("h2", {}, t("ev.title")), h("p", { class: "muted small" }, t("ev.hint")), events), about),
    ),
  );
}

function panel(no, title, right, why, ...body) {
  return h(
    "section",
    { class: "panel" },
    h("header", {}, h("h2", {}, h("span", { class: "stepno" }, no), title), h("span", { class: "muted small" }, right)),
    h("div", { class: "body" }, h("p", { class: "why" }, why), body),
  );
}

// ---------- 評測紀錄 ----------

function viewRuns() {
  return h(
    "div",
    {},
    h("h1", {}, t("rn.title")),
    h("p", { class: "lede" }, t("rn.lede")),
    h(
      "div",
      { class: "grid" },
      [...batches()].reverse().map((run) =>
        h(
          "article",
          { class: "card" },
          h("div", { class: "muted small" }, fmtDate(run.created_at)),
          h("h2", { class: "card-title" }, runTag(run)),
          scoreBlock(run),
          h("p", { class: "card-cta" }, h("a", { class: "btn", href: "#/run/" + run.run_id }, t("rn.see"))),
          runMeta(run),
        ),
      ),
    ),
  );
}

// ---------- 路由 ----------

function notFound() {
  return h("div", { class: "pad" }, h("h1", {}, t("nf.title")), h("p", {}, h("a", { href: "#/" }, t("nav.issues"))));
}

async function route() {
  if (!INDEX) return;
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const nav = parts[0] === "runs" ? "runs" : "issues";
  for (const a of document.querySelectorAll("[data-nav]")) a.classList.toggle("active", a.dataset.nav === nav);

  let view;
  try {
    if (parts.length === 0) view = viewIssues(null);
    else if (parts[0] === "run" && parts[1]) view = viewIssues(parts[1]);
    else if (parts[0] === "issue" && parts[1]) view = await viewIssue(parts[1], parts[2], parts[3]);
    else if (parts[0] === "runs") view = viewRuns();
    else view = notFound();
  } catch (err) {
    view = h("div", { class: "pad" }, h("h1", {}, t("err.title")), h("p", { class: "muted" }, String(err.message || err)));
  }
  app.replaceChildren(view);
}

async function start() {
  applyStatic();
  for (const b of document.querySelectorAll("[data-lang]")) b.addEventListener("click", () => setLang(b.dataset.lang));
  try {
    const res = await fetch("snapshot/index.json");
    if (!res.ok) throw new Error("HTTP " + res.status);
    INDEX = await res.json();
  } catch {
    app.replaceChildren(
      h(
        "div",
        { class: "pad" },
        h("h1", {}, t("nodata.title")),
        h("p", { class: "muted" }, t("nodata.body")),
        h("pre", { class: "free" }, "docker compose run --rm app uv run python web/export_demo.py"),
      ),
    );
    return;
  }
  let lastPage = "";
  window.addEventListener("hashchange", () => {
    const page = location.hash.split("/").slice(0, 3).join("/");
    route().then(() => {
      if (page !== lastPage) window.scrollTo(0, 0);
      lastPage = page;
    });
  });
  route();
}

start();
