let state = {
  students: [],
  fee_tracker: [],
  dashboard: {},
  rates: [],
  months: [],
  formula_manifest: {},
  settings: {},
  users: [],
  subscriptions: [],
  discount_codes: [],
  backups: [],
  reconciliation: [],
  payer_aliases: [],
  status_changes: [],
  can_access_staff: false,
  staff: { members: [], schedules: [], punches: [], weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"], summary: {} },
  current_user: {},
  role_options: ["Admin", "Office Manager", "Office Assistant"],
  reconciliationPreview: [],
  reconciliationFileName: "",
  feeImportRows: [],
  feeImportPreview: [],
  batchImportRows: [],
  batchImportPreview: [],
  batchImportFileName: "",
};

let appConfig = { auth_required: false };
let supabaseClient = null;
let authSession = null;
let supabasePersistenceMode = null;
let feeMonthOffset = 0;
let reportSelectedMonths = [];
let activeAdminArea = "student";
let activeStaffView = "staff-dashboard";
const FEE_PAST_MONTHS = 1;
const FEE_FUTURE_MONTHS = 2;
const FEE_MONTH_WINDOW = FEE_PAST_MONTHS + 1 + FEE_FUTURE_MONTHS;

const money = (value) => new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 }).format(value || 0);
const number = (value) => new Intl.NumberFormat("en-CA", { maximumFractionDigits: 2 }).format(value || 0);
const qs = (selector, root = document) => root.querySelector(selector);

function toast(message) {
  const node = qs("#toast");
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 2200);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authSession?.access_token) headers.Authorization = `Bearer ${authSession.access_token}`;
  const res = await fetch(path, {
    headers,
    ...options,
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || "Request failed");
  return data;
}

async function initAuth() {
  const res = await fetch("/api/config", { headers: { "Content-Type": "application/json" } });
  appConfig = await res.json();
  if (!appConfig.auth_required) {
    renderAuthState();
    return true;
  }
  if (!window.supabase || !appConfig.supabase_url || !appConfig.supabase_anon_key) {
    qs("#authMessage").textContent = "Authentication is not ready. Check Render environment variables.";
    renderAuthState();
    return false;
  }
  if (qs("#keepSignedIn")) qs("#keepSignedIn").checked = window.localStorage.getItem("smp_keep_signed_in") === "1";
  supabaseClient = createSupabaseClient(window.localStorage.getItem("smp_keep_signed_in") === "1");
  const { data } = await supabaseClient.auth.getSession();
  authSession = data.session;
  renderAuthState();
  return Boolean(authSession);
}

function createSupabaseClient(keepSignedIn) {
  supabasePersistenceMode = keepSignedIn ? "local" : "session";
  return window.supabase.createClient(appConfig.supabase_url, appConfig.supabase_anon_key, {
    auth: {
      persistSession: keepSignedIn,
      storage: keepSignedIn ? window.localStorage : window.sessionStorage,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });
}

function currentUserEmail() {
  return authSession?.user?.email || authSession?.user?.user_metadata?.email || "Signed in";
}

function currentUserName() {
  return authSession?.user?.user_metadata?.full_name || authSession?.user?.user_metadata?.name || currentUserEmail();
}

function currentUserRole() {
  if (state.current_user?.role) return state.current_user.role;
  const email = currentUserEmail().toLowerCase();
  const user = (state.users || []).find((item) => String(item.email || "").toLowerCase() === email);
  if (user?.role) return user.role;
  return authSession ? "Pending access" : "";
}

function renderAuthState() {
  const authRequired = Boolean(appConfig.auth_required);
  const signedIn = Boolean(authSession);
  const authPanel = qs("#authPanel");
  const accountStatus = qs("#accountStatus");
  const accountEmail = qs("#accountEmail");
  const accountRole = qs("#accountRole");
  const signOutButton = qs("#signOut");
  const switchButton = qs("#switchAccount");

  authPanel?.classList.toggle("collapsed", !authRequired || signedIn);
  accountStatus?.classList.toggle("collapsed", !signedIn);
  if (accountEmail) accountEmail.textContent = signedIn ? currentUserName() : "Not signed in";
  if (accountRole) accountRole.textContent = signedIn ? currentUserRole() : "";
  if (signOutButton) signOutButton.style.display = signedIn ? "inline-flex" : "none";
  if (switchButton) switchButton.style.display = signedIn ? "inline-flex" : "none";
}

async function sendMagicLink(event) {
  event.preventDefault();
  const email = qs("#loginEmail").value.trim();
  if (!email || !supabaseClient) return;
  updateAuthPersistenceChoice();
  const { error } = await supabaseClient.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: window.location.origin },
  });
  qs("#authMessage").textContent = error ? error.message : "Check your email for the login link.";
}

function setAuthMode(mode) {
  const isSignup = mode === "signup";
  qs("#loginPanel")?.classList.toggle("collapsed", isSignup);
  qs("#signupPanel")?.classList.toggle("collapsed", !isSignup);
  qs("#showLogin")?.classList.toggle("active", !isSignup);
  qs("#showSignup")?.classList.toggle("active", isSignup);
  qs("#authMessage").textContent = isSignup
    ? "Create your login with Google or enter your email to receive a verification link."
    : "Login with Google or request a secure email link.";
}

function updateAuthPersistenceChoice() {
  const keepSignedIn = Boolean(qs("#keepSignedIn")?.checked);
  window.localStorage.setItem("smp_keep_signed_in", keepSignedIn ? "1" : "0");
  const desiredMode = keepSignedIn ? "local" : "session";
  if (!authSession && supabasePersistenceMode !== desiredMode && window.supabase && appConfig.supabase_url && appConfig.supabase_anon_key) {
    supabaseClient = createSupabaseClient(keepSignedIn);
  }
}

async function signInWithGoogle() {
  if (!supabaseClient) return;
  updateAuthPersistenceChoice();
  await supabaseClient.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo: window.location.origin },
  });
}

async function signOut() {
  if (supabaseClient) await supabaseClient.auth.signOut();
  window.sessionStorage.clear();
  authSession = null;
  renderAuthState();
}

async function switchAccount() {
  if (!supabaseClient) return;
  await supabaseClient.auth.signOut();
  authSession = null;
  renderAuthState();
  await signInWithGoogle();
}

async function load() {
  state = await api("/api/bootstrap");
  hydrateMonthSelectors();
  renderAll();
}

function renderAll() {
  renderBrand();
  renderAuthState();
  qs("#recordCount").textContent = state.students.length;
  renderAdminAreas();
  renderSubjectChoices();
  renderDashboard();
  renderFeeTracker();
  renderRoster();
  renderReporting();
  renderSettings();
  renderBatch();
  renderReconciliation();
  renderStaffAdministration();
}

function renderAdminAreas() {
  const staffButton = qs("#staffAdminArea");
  if (staffButton) {
    staffButton.disabled = !state.can_access_staff;
    staffButton.title = state.can_access_staff ? "Open Staff Administration" : "Staff Administration is limited to Admin and Office Manager";
  }
  if (!state.can_access_staff && activeAdminArea === "staff") activeAdminArea = "student";
  document.body.dataset.adminArea = activeAdminArea;
  qs("#studentTabs")?.classList.toggle("collapsed", activeAdminArea !== "student");
  qs("#staffAdministration")?.classList.toggle("active", activeAdminArea === "staff");
  qs("#staffAdministration")?.classList.toggle("collapsed", activeAdminArea !== "staff");
  document.querySelectorAll("main > section.panel:not(#staffAdministration)").forEach((panel) => {
    panel.classList.toggle("area-hidden", activeAdminArea !== "student");
  });
  qs("#studentAdminArea")?.classList.toggle("active", activeAdminArea === "student");
  qs("#staffAdminArea")?.classList.toggle("active", activeAdminArea === "staff");
}

function switchAdminArea(area) {
  if (area === "staff" && !state.can_access_staff) {
    toast("Staff Administration is limited to Admin and Office Manager");
    return;
  }
  activeAdminArea = area;
  if (area === "student" && !document.querySelector("main > section.panel.active:not(#staffAdministration)")) {
    qs("#dashboard")?.classList.add("active");
    document.querySelector('[data-tab="dashboard"]')?.classList.add("active");
  }
  renderAdminAreas();
}

function renderBrand() {
  const settings = state.settings || {};
  qs("#institutionHeading").textContent = settings.institution_name || "SMP - After School Management Program";
  qs("#institutionDetails").textContent = settings.institution_details || "";
  qs("#institutionPhone").textContent = settings.institution_phone ? `Phone: ${settings.institution_phone}` : "";
  qs("#institutionPhone").style.display = settings.institution_phone ? "block" : "none";
  document.title = `SMP - ${settings.institution_name || "After School Management Program"}`;
}

function hydrateMonthSelectors() {
  const options = state.months.map((m) => `<option value="${m}">${m}</option>`).join("");
  for (const selector of ["#dashboardMonth", "#settingsMonth"]) {
    const node = qs(selector);
    if (node && node.options.length !== state.months.length) node.innerHTML = options;
    if (node) node.value = state.settings.current_month || "May-26";
  }
  const reportMonths = qs("#reportMonths");
  if (reportMonths && reportMonths.options.length !== state.months.length) {
    reportMonths.innerHTML = options;
  }
  if (!reportSelectedMonths.length) reportSelectedMonths = [state.settings.current_month || "May-26"];
  if (reportMonths) {
    [...reportMonths.options].forEach((option) => {
      option.selected = reportSelectedMonths.includes(option.value);
    });
  }
}

function configuredSubjects() {
  const raw = state.settings.subjects_offered || "Math\nEnglish";
  const subjects = raw.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
  return [...new Set(subjects.map((s) => s[0]?.toUpperCase() + s.slice(1)).filter(Boolean))];
}

function subjectList(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  const raw = String(value || "").trim();
  if (raw.toLowerCase() === "both") return ["Math", "English"];
  return raw.split(/[,;/|]+/).map((s) => s.trim()).filter(Boolean);
}

function subjectText(value) {
  return subjectList(value).join(", ");
}

function renderSubjectChoices(selected = "") {
  if (!qs("#studentSubjectChoices")) return;
  const chosen = subjectList(selected).map((s) => s.toLowerCase());
  qs("#studentSubjectChoices").innerHTML = configuredSubjects().map((subject) => `
    <label class="check-pill">
      <input type="checkbox" name="subjects_choice" value="${escapeAttr(subject)}" ${chosen.includes(subject.toLowerCase()) ? "checked" : ""}>
      <span>${subject}</span>
    </label>
  `).join("");
}

function metric(label, value, tone = "") {
  return `<div class="metric ${tone}"><span>${label}</span><strong>${value}</strong></div>`;
}

function activeRows() {
  return state.fee_tracker.filter((row) => row.status.toUpperCase() === "C" && row.subjects);
}

function normalizedStudentName(name) {
  return String(name || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function normalizedDuplicateKey(row) {
  return `${normalizedStudentName(row.student_name)}|${normalizedStudentName(row.parent_guardian)}`;
}

function activeDuplicateNameMap() {
  const counts = {};
  for (const row of state.students || []) {
    if (String(row.status || "").toUpperCase() !== "C") continue;
    const key = normalizedDuplicateKey(row);
    if (!normalizedStudentName(row.student_name)) continue;
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function isActiveDuplicateName(row) {
  if (String(row.status || "").toUpperCase() !== "C") return false;
  return (activeDuplicateNameMap()[normalizedDuplicateKey(row)] || 0) > 1;
}

function renderDashboard() {
  const d = state.dashboard;
  const rows = activeRows();
  const currentMonth = state.settings.current_month || "May-26";
  const currentRevenue = rows.reduce((sum, row) => sum + (row.months[currentMonth] || 0), 0);
  const currentUnpaid = rows.filter((row) => isCurrentMonthOverdue(row));
  const currentUnpaidTotal = currentUnpaid.reduce((sum, row) => sum + (row.std_monthly_fee || 0), 0);
  const duplicateActiveCount = (state.students || []).filter((row) => isActiveDuplicateName(row)).length;
  const subjectMetrics = Object.entries(d.subject_breakdown || {}).slice(0, 4).map(([subject, count]) => metric(`${subject} Students`, count));
  qs("#metrics").innerHTML = [
    metric("Active Students", d.active_students, "accent"),
    metric("Discontinued Students", d.discontinued_students || 0, "warning"),
    metric(`Revenue - ${currentMonth}`, money(currentRevenue), "success"),
    metric("Annual Projected Revenue", money(d.annual_projected_revenue)),
    metric("Total Enrolment Units", d.total_enrolment),
    ...subjectMetrics,
    metric(`Unpaid - ${currentMonth}`, currentUnpaid.length, currentUnpaid.length ? "warning" : "success"),
    metric("Current Month Outstanding", money(currentUnpaidTotal), currentUnpaid.length ? "warning" : "success"),
    metric("Active Student/Parent Duplicates", duplicateActiveCount, duplicateActiveCount ? "warning" : "success"),
  ].join("");

  const enrolmentMax = Math.max(...d.enrolment_totals.map((m) => m.count), 1);
  qs("#enrolmentChart").innerHTML = d.enrolment_totals.map((m) => {
    const h = Math.max(8, Math.round((m.count / enrolmentMax) * 170));
    return `<div class="bar enrolment-bar" style="height:${h}px" title="${m.month}: ${m.count} enrolments"><strong>${m.count}</strong><span>${m.month}</span></div>`;
  }).join("");

  const max = Math.max(...d.monthly_totals.map((m) => m.total), 1);
  qs("#barChart").innerHTML = d.monthly_totals.map((m) => {
    const h = Math.max(8, Math.round((m.total / max) * 170));
    return `<div class="bar" style="height:${h}px" title="${m.month}: ${money(m.total)}"><strong>${money(m.total)}</strong><span>${m.month}</span></div>`;
  }).join("");

  const byMethod = {};
  for (const row of rows) {
    const method = row.payment_method || "Unspecified";
    byMethod[method] = (byMethod[method] || 0) + (row.months[currentMonth] || 0);
  }
  const entries = Object.entries(byMethod).sort((a, b) => b[1] - a[1]);
  const methodMax = Math.max(...entries.map(([, v]) => v), 1);
  qs("#paymentMix").innerHTML = entries.map(([method, value]) => `
    <div class="mix-item">
      <strong>${method}</strong>
      <div class="mix-track"><div class="mix-fill" style="width:${Math.round((value / methodMax) * 100)}%"></div></div>
      <span>${money(value)}</span>
    </div>
  `).join("");

  qs("#unpaidList").innerHTML = currentUnpaid.length
    ? currentUnpaid
        .map((row) => `
          <div class="unpaid-item">
            <strong>${row.student_name}</strong>
            <span>${row.parent_guardian || "No guardian"} - ${subjectText(row.subjects)} - Expected ${money(row.std_monthly_fee)}</span>
          </div>
        `)
        .join("")
    : `<div class="empty-state">No current-month unpaid students after the 5th.</div>`;
}

function monthLabelFromDate(value) {
  if (!value) return "";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleString("en-US", { month: "short" }) + "-" + String(parsed.getFullYear()).slice(-2);
}

function selectedReportMonths() {
  const selected = [...(qs("#reportMonths")?.selectedOptions || [])].map((option) => option.value);
  return selected.length ? selected : [state.settings.current_month || "May-26"];
}

function reportEnrolmentRows() {
  const selected = new Set(selectedReportMonths());
  return (state.fee_tracker || [])
    .filter((row) => selected.has(monthLabelFromDate(row.enrol_date)))
    .sort((a, b) => String(a.enrol_date || "").localeCompare(String(b.enrol_date || "")) || String(a.student_name || "").localeCompare(String(b.student_name || "")));
}

function duplicateGroups() {
  const groups = {};
  for (const row of state.students || []) {
    if (String(row.status || "").toUpperCase() !== "C") continue;
    const key = normalizedDuplicateKey(row);
    if (!normalizedStudentName(row.student_name)) continue;
    groups[key] = groups[key] || [];
    groups[key].push(row);
  }
  return Object.values(groups)
    .filter((items) => items.length > 1)
    .sort((a, b) => a[0].student_name.localeCompare(b[0].student_name));
}

function reportStatusChangeRows() {
  const selected = new Set(selectedReportMonths());
  return (state.status_changes || [])
    .filter((row) => selected.has(row.changed_month))
    .filter((row) => String(row.previous_status || "").toUpperCase() === "C" && String(row.new_status || "").toUpperCase() === "D")
    .sort((a, b) => String(b.changed_at || "").localeCompare(String(a.changed_at || "")));
}

function renderReporting() {
  if (!qs("#reporting")) return;
  const rows = reportEnrolmentRows();
  const statusRows = reportStatusChangeRows();
  const selected = selectedReportMonths();
  const active = rows.filter((row) => String(row.status || "").toUpperCase() === "C");
  const units = rows.reduce((sum, row) => sum + (row.subject_units || 0), 0);
  const monthlyFee = rows.reduce((sum, row) => sum + (row.std_monthly_fee || 0), 0);
  qs("#enrolmentReportSummary").innerHTML = [
    `<div class="info-tile"><span>Selected Months</span><strong>${selected.length}</strong></div>`,
    `<div class="info-tile"><span>Enrolments</span><strong>${rows.length}</strong></div>`,
    `<div class="info-tile"><span>Current Students</span><strong>${active.length}</strong></div>`,
    `<div class="info-tile"><span>C to D Changes</span><strong>${statusRows.length}</strong></div>`,
    `<div class="info-tile"><span>Subject Units</span><strong>${number(units)}</strong></div>`,
    `<div class="info-tile"><span>Monthly Fee Added</span><strong>${money(monthlyFee)}</strong></div>`,
  ].join("");

  renderTable(
    qs("#enrolmentReportTable"),
    ["Month", "#", "Student Name", "Parent / Guardian", "Status", "Enrol Date", "Subjects", "STD Fee", "Pay Method", "Total Paid"],
    rows.map((row) => [
      monthLabelFromDate(row.enrol_date),
      row.number,
      `<span class="${isActiveDuplicateName(row) ? "duplicate-name" : ""}">${escapeHtml(row.student_name)}</span>`,
      escapeHtml(row.parent_guardian || ""),
      `<span class="status-badge ${row.status.toUpperCase() === "C" ? "current" : "inactive"}">${row.status}</span>`,
      row.enrol_date || "",
      row.subjects_display || subjectText(row.subjects),
      money(row.std_monthly_fee),
      row.payment_method || "",
      money(row.total_paid),
    ])
  );

  renderTable(
    qs("#statusChangeReportTable"),
    ["Month", "Changed Date", "#", "Student Name", "Parent / Guardian", "Status Change", "Subjects", "STD Fee", "Notes"],
    statusRows.map((row) => [
      row.changed_month,
      String(row.changed_at || "").slice(0, 10),
      row.number || "",
      escapeHtml(row.student_name || ""),
      escapeHtml(row.parent_guardian || ""),
      `<span class="status-badge inactive">${row.previous_status} to ${row.new_status}</span>`,
      subjectText(row.subjects),
      money(row.std_monthly_fee),
      escapeHtml(row.notes || ""),
    ])
  );

  const duplicates = duplicateGroups();
  qs("#duplicateSummary").innerHTML = duplicates.length
    ? duplicates.map((group) => `<div class="alert-item danger-alert"><strong>${escapeHtml(group[0].student_name)}</strong><span>${escapeHtml(group[0].parent_guardian || "No parent listed")} - ${group.length} active records. Correct in Student Roster.</span></div>`).join("")
    : `<div class="empty-state">No active duplicate student and parent matches found.</div>`;
  renderTable(
    qs("#duplicateReportTable"),
    ["Student Name", "Records", "Roster Numbers", "Parents", "Subjects"],
    duplicates.map((group) => [
      `<span class="duplicate-name">${escapeHtml(group[0].student_name)}</span>`,
      group.length,
      group.map((row) => row.number).join(", "),
      group.map((row) => escapeHtml(row.parent_guardian || "-")).join("<br>"),
      group.map((row) => escapeHtml(subjectText(row.subjects) || "-")).join("<br>"),
    ])
  );

  const currentMonth = state.settings.current_month || "May-26";
  const unpaidCount = activeRows().filter((row) => isCurrentMonthOverdue(row)).length;
  const currentRevenue = activeRows().reduce((sum, row) => sum + (row.months[currentMonth] || 0), 0);
  qs("#managerReportIdeas").innerHTML = [
    ["Outstanding Fees", `${unpaidCount} current-month follow-up${unpaidCount === 1 ? "" : "s"}`, "Run this after the 5th to review students still missing payment."],
    ["Payment Method Summary", "PAD, E-Transfer, Cash, Credit Card", "Compare monthly collections with bank and credit-card deposits."],
    ["Active Duplicate Records", `${duplicates.reduce((sum, group) => sum + group.length, 0)} record${duplicates.length === 1 ? "" : "s"} flagged`, "Clean duplicated student and parent matches before billing or reporting."],
    ["Discontinued Students", `${state.dashboard.discontinued_students || 0} inactive records`, "Review lost enrolments and possible reactivation opportunities."],
    ["Subject Mix", `${configuredSubjects().join(", ")}`, "See which subjects are growing and where staffing demand is increasing."],
    ["Current Month Revenue", `${currentMonth}: ${money(currentRevenue)}`, "Quick check for monthly collection trend and expected deposits."],
  ].map(([title, value, detail]) => `
    <div class="idea-card">
      <span>${title}</span>
      <strong>${value}</strong>
      <p>${detail}</p>
    </div>
  `).join("");
}

function filteredFeeRows() {
  const filter = qs("#feeStatusFilter").value;
  const term = (qs("#feeSearch")?.value || "").toLowerCase();
  return state.fee_tracker
    .filter((row) => filter === "all" || row.status.toUpperCase() === "C")
    .filter((row) => !term || JSON.stringify(row).toLowerCase().includes(term));
}

function currentMonthIndex() {
  const currentMonth = state.settings.current_month || "May-26";
  const index = state.months.indexOf(currentMonth);
  return index >= 0 ? index : Math.max(0, state.months.length - FEE_FUTURE_MONTHS - 1);
}

function feeVisibleMonths() {
  if (!state.months.length) return [];
  const target = currentMonthIndex() + feeMonthOffset;
  const maxStart = Math.max(0, state.months.length - FEE_MONTH_WINDOW);
  const start = Math.max(0, Math.min(maxStart, target - FEE_PAST_MONTHS));
  return state.months.slice(start, start + FEE_MONTH_WINDOW);
}

function renderFeeMonthControls(months) {
  const label = qs("#feeMonthWindowLabel");
  const historyButton = qs("#feeHistoryMonths");
  const futureButton = qs("#feeFutureMonths");
  const currentButton = qs("#feeCurrentMonths");
  if (!label || !historyButton || !futureButton || !currentButton) return;
  const current = state.settings.current_month || "May-26";
  const currentIndex = currentMonthIndex();
  const firstVisibleIndex = state.months.indexOf(months[0]);
  const lastVisibleIndex = state.months.indexOf(months[months.length - 1]);
  label.textContent = months.length ? `Showing ${months[0]} to ${months[months.length - 1]} - Current: ${current}` : "No fee months available";
  historyButton.disabled = firstVisibleIndex <= 0;
  futureButton.disabled = lastVisibleIndex >= state.months.length - 1;
  currentButton.disabled = feeMonthOffset === 0 || (firstVisibleIndex <= currentIndex && lastVisibleIndex >= currentIndex + FEE_FUTURE_MONTHS);
}

function renderFeeTracker() {
  const rows = filteredFeeRows();
  const visibleMonths = feeVisibleMonths();
  renderFeeMonthControls(visibleMonths);
  const headers = ["#", "Student Name", "Parent / Guardian", "Status", "Enrol Date", "Subjects", "Type", "STD Fee", "Pay Method", "Units", ...visibleMonths, "Total Paid", "Balance"];
  const body = rows.map((row) => [
    row.number,
    `<span class="${[isCurrentMonthOverdue(row) ? "overdue-name" : "", isActiveDuplicateName(row) ? "duplicate-name" : ""].filter(Boolean).join(" ")}">${row.student_name}</span>`,
    row.parent_guardian,
    `<span class="status-badge ${row.status.toUpperCase() === "C" ? "current" : "inactive"}">${row.status}</span>`,
    row.enrol_date,
    row.subjects_display || subjectText(row.subjects),
    row.rate_type,
    money(row.std_monthly_fee),
    row.payment_method,
    row.subject_units,
    ...visibleMonths.map((m) => `<input class="payment-input" data-student="${row.id}" data-month="${m}" value="${row.months[m] || ""}" inputmode="decimal">`),
    money(row.total_paid),
    money(row.balance),
  ]);
  const totals = feeTotals(rows);
  body.push([
    "", "TOTAL", "", "", "", "", "", money(totals.stdFee), "", number(totals.units),
    ...visibleMonths.map((m) => money(totals.months[m])),
    money(totals.totalPaid),
    money(totals.balance),
  ]);
  renderTable(qs("#feeTable"), headers, body, { footerLast: true });
  document.querySelectorAll(".payment-input").forEach((input) => {
    input.addEventListener("change", () => savePayment(input));
  });
  renderFeeImportPreview();
}

function renderFeeImportPreview() {
  const panel = qs("#feeImportPanel");
  if (!panel) return;
  const rows = state.feeImportPreview || [];
  if (!rows.length) {
    panel.classList.add("collapsed");
    return;
  }
  panel.classList.remove("collapsed");
  const body = rows.map((row) => [
    row.row_number,
    escapeHtml(row.student_name),
    escapeHtml(row.parent_guardian),
    row.valid ? `<span class="confidence high">ready</span>` : `<span class="confidence low">review</span>`,
    escapeHtml(row.matched_student || "-"),
    escapeHtml(row.matched_parent || "-"),
    escapeHtml(row.matched_enrol_date || "-"),
    row.month_count,
    row.expected_month_count || "-",
    row.missing_month_count || 0,
    money(row.total_amount),
    escapeHtml([...(row.errors || []), ...(row.warnings || [])].join("; ") || "Validated"),
  ]);
  renderTable(qs("#feeImportTable"), ["Row", "CSV Student", "CSV Parent", "Status", "Matched Student", "Matched Parent", "Enrol Date", "CSV Months", "Expected Months", "Blank Months", "Total Amount", "Validation"], body);
}

function monthDate(monthLabel) {
  const [mon, yy] = String(monthLabel || "").split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const idx = months.indexOf(mon);
  if (idx < 0) return null;
  return new Date(2000 + Number(yy), idx, 1);
}

function isCurrentMonthOverdue(row) {
  const now = new Date();
  const currentMonth = state.settings.current_month || "May-26";
  const month = monthDate(currentMonth);
  if (!month || row.status.toUpperCase() !== "C") return false;
  const sameMonth = now.getFullYear() === month.getFullYear() && now.getMonth() === month.getMonth();
  const afterFifth = now.getDate() > 5;
  return sameMonth && afterFifth && !Number(row.months[currentMonth] || 0);
}

function feeTotals(rows) {
  const totals = { stdFee: 0, units: 0, totalPaid: 0, balance: 0, months: {} };
  for (const month of state.months) totals.months[month] = 0;
  for (const row of rows) {
    totals.stdFee += row.std_monthly_fee || 0;
    totals.units += row.subject_units || 0;
    totals.totalPaid += row.total_paid || 0;
    totals.balance += row.balance || 0;
    for (const month of state.months) totals.months[month] += row.months[month] || 0;
  }
  return totals;
}

async function savePayment(input) {
  input.classList.add("saving");
  try {
    await api(`/api/payments/${input.dataset.student}/${input.dataset.month}`, {
      method: "PUT",
      body: JSON.stringify({ amount: input.value }),
    });
    toast("Payment saved");
    await load();
  } catch (error) {
    toast(error.message);
  } finally {
    input.classList.remove("saving");
  }
}

function renderRoster() {
  const term = qs("#search").value.toLowerCase();
  const filter = qs("#rosterStatusFilter").value;
  const rows = state.students
    .filter((s) => filter === "all" || s.status.toUpperCase() === "C")
    .filter((s) => JSON.stringify(s).toLowerCase().includes(term))
    .map((s) => [
      s.number,
      `<button class="link-button ${[isStudentOverdue(s.id) ? "overdue-name" : "", isActiveDuplicateName(s) ? "duplicate-name" : ""].filter(Boolean).join(" ")}" data-profile="${s.id}">${s.student_name}</button>`,
      s.parent_guardian,
      `<span class="status-badge ${s.status.toUpperCase() === "C" ? "current" : "inactive"}">${s.status}</span>`,
      s.enrol_date,
      subjectText(s.subjects),
      s.last_modification || "",
      s.rate_type,
      money(s.std_monthly_fee),
      s.payment_method,
      s.phone || "",
      s.email || "",
      `<div class="row-actions"><button class="small" data-profile="${s.id}">Profile</button><button class="small danger" data-delete="${s.id}">Delete</button></div>`,
    ]);
  renderTable(qs("#rosterTable"), ["#", "Student Name", "Parent / Guardian", "Status", "Enrol Date", "Subjects", "Last Modification", "Rate Type", "STD Fee", "Pay Method", "Phone", "Email", "Actions"], rows);
  document.querySelectorAll("[data-profile]").forEach((button) => button.addEventListener("click", () => showStudentProfile(button.dataset.profile)));
  document.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", () => deleteStudent(button.dataset.delete)));
}

function isStudentOverdue(studentId) {
  const row = state.fee_tracker.find((item) => String(item.id) === String(studentId));
  return row ? isCurrentMonthOverdue(row) : false;
}

function showStudentProfile(id) {
  const student = state.students.find((s) => String(s.id) === String(id));
  const fee = state.fee_tracker.find((s) => String(s.id) === String(id));
  if (!student || !fee) return;
  const enrolDate = student.enrol_date ? new Date(`${student.enrol_date}T00:00:00`) : null;
  const timelineMonths = state.months.filter((m) => {
    const d = monthDate(m);
    return !enrolDate || !d || d >= new Date(enrolDate.getFullYear(), enrolDate.getMonth(), 1);
  });
  qs("#studentProfile").innerHTML = `
    <div class="section-title">
      <div>
        <h2>${student.student_name}</h2>
        <span>${student.parent_guardian || "No guardian listed"}</span>
      </div>
      <button type="button" class="ghost" id="closeProfile">Close</button>
    </div>
    <div class="profile-summary">
      <div><span>Status</span><strong>${student.status}</strong></div>
      <div><span>Subjects</span><strong>${subjectText(student.subjects)}</strong></div>
      <div><span>Rate Type</span><strong>${student.rate_type}</strong></div>
      <div><span>Monthly Fee</span><strong>${money(student.std_monthly_fee)}</strong></div>
      <div><span>Payment</span><strong>${student.payment_method || "-"}</strong></div>
      <div><span>Enrol Date</span><strong>${student.enrol_date || "-"}</strong></div>
    </div>
    <div class="profile-block">
      <h3>Contact</h3>
      <p>${student.phone || "No phone"}<br>${student.email || "No email"}</p>
    </div>
    <div class="profile-block">
      <h3>Notes</h3>
      <p>${student.notes || "No notes recorded."}</p>
    </div>
    <div class="profile-block">
      <h3>Payment Timeline</h3>
      <div class="timeline">
        ${timelineMonths.map((m) => `<span class="${fee.months[m] > 0 ? "paid" : ""}" title="${m}: ${money(fee.months[m])}">${m}</span>`).join("")}
      </div>
      <p>Total paid: <strong>${money(fee.total_paid)}</strong> - Balance: <strong>${money(fee.balance)}</strong></p>
    </div>
    <div class="actions">
      <button type="button" id="profileEdit">Edit Student</button>
    </div>
  `;
  qs("#studentProfile").classList.remove("collapsed");
  qs("#closeProfile").addEventListener("click", closeProfile);
  qs("#profileEdit").addEventListener("click", () => {
    closeProfile();
    editStudent(id);
  });
}

function closeProfile() {
  qs("#studentProfile").classList.add("collapsed");
}

function renderSettings() {
  const form = qs("#settingsForm");
  for (const [key, value] of Object.entries(state.settings)) {
    if (form.elements[key]) form.elements[key].value = value;
  }
  qs("#ratesTable").innerHTML = `
    <thead><tr><th>Subject</th><th>Rate Type</th><th>Monthly Fee</th><th>Description</th><th>Actions</th></tr></thead>
    <tbody>
      ${state.rates.map((r) => `
        <tr data-rate="${r.id}">
          <td><input name="subject" value="${escapeAttr(r.subject)}"></td>
          <td><input name="rate_type" value="${escapeAttr(r.rate_type)}"></td>
          <td><input name="monthly_fee" type="number" step="0.01" value="${r.monthly_fee}"></td>
          <td><input name="description" value="${escapeAttr(r.description || "")}"></td>
          <td><div class="row-actions"><button class="small" data-save-rate="${r.id}">Save</button><button class="small danger" data-delete-rate="${r.id}">Delete</button></div></td>
        </tr>
      `).join("")}
    </tbody>
  `;
  qs("#formulaManifest").textContent = JSON.stringify(state.formula_manifest, null, 2);
  renderBillingAndAccess();
  document.querySelectorAll("[data-save-rate]").forEach((button) => button.addEventListener("click", () => saveRate(button.dataset.saveRate)));
  document.querySelectorAll("[data-delete-rate]").forEach((button) => button.addEventListener("click", () => deleteRate(button.dataset.deleteRate)));
}

function selectedSubjectValues() {
  return [...document.querySelectorAll('[name="subjects_choice"]:checked')].map((input) => input.value);
}

function setSelectedSubjects(value) {
  renderSubjectChoices(value);
}

function renderBillingAndAccess() {
  const subscription = state.subscriptions[0] || {};
  const roleOptions = state.role_options?.length ? state.role_options : ["Admin", "Office Manager", "Office Assistant"];
  const roleSelectOptions = (selected) => roleOptions
    .map((role) => `<option value="${escapeAttr(role)}" ${role === selected ? "selected" : ""}>${role}</option>`)
    .join("");
  qs("#subscriptionSummary").innerHTML = [
    `<div class="info-tile"><span>Status</span><strong>${subscription.status || "trialing"}</strong></div>`,
    `<div class="info-tile"><span>Trial Ends</span><strong>${subscription.trial_end || "Not set"}</strong></div>`,
    `<div class="info-tile"><span>Monthly Price</span><strong>$15.99 CAD</strong></div>`,
    `<div class="info-tile"><span>Payments</span><strong>Stripe / Google Pay</strong></div>`,
  ].join("");

  const userRoleSelect = qs('#userForm [name="role"]');
  if (userRoleSelect) userRoleSelect.innerHTML = roleSelectOptions("Office Assistant");

  qs("#usersTable").innerHTML = `
    <thead><tr><th>Email</th><th>Display Name</th><th>Role</th><th>Status</th><th>Provider</th><th>Actions</th></tr></thead>
    <tbody>
      ${(state.users || []).map((u) => `
        <tr data-user="${u.id}">
          <td><input name="email" type="email" value="${escapeAttr(u.email)}"></td>
          <td><input name="display_name" value="${escapeAttr(u.display_name || "")}"></td>
          <td><select name="role">${roleSelectOptions(u.role)}</select></td>
          <td><select name="active"><option value="1" ${u.active ? "selected" : ""}>Active</option><option value="0" ${!u.active ? "selected" : ""}>Disabled</option></select></td>
          <td>${escapeHtml(u.auth_provider || "Email")}</td>
          <td><div class="row-actions"><button class="small" data-save-user="${u.id}">Save</button><button class="small danger" data-delete-user="${u.id}">Delete</button></div></td>
        </tr>
      `).join("") || `<tr><td colspan="6" class="empty-state">No users have been added yet.</td></tr>`}
    </tbody>
  `;

  qs("#discountTable").innerHTML = `
    <thead><tr><th>Code</th><th>Description</th><th>% Off</th><th>$ Off</th><th>Active</th><th>Actions</th></tr></thead>
    <tbody>
      ${state.discount_codes.map((d) => `
        <tr data-discount="${d.id}">
          <td><input name="code" value="${escapeAttr(d.code)}"></td>
          <td><input name="description" value="${escapeAttr(d.description || "")}"></td>
          <td><input name="percent_off" type="number" step="0.01" value="${d.percent_off || 0}"></td>
          <td><input name="amount_off" type="number" step="0.01" value="${d.amount_off || 0}"></td>
          <td><select name="active"><option value="1" ${d.active ? "selected" : ""}>Yes</option><option value="0" ${!d.active ? "selected" : ""}>No</option></select></td>
          <td><div class="row-actions"><button class="small" data-save-discount="${d.id}">Save</button><button class="small danger" data-delete-discount="${d.id}">Delete</button></div></td>
        </tr>
      `).join("")}
    </tbody>
  `;

  qs("#backupSelect").innerHTML = state.backups.length
    ? state.backups.map((b) => `<option value="${escapeAttr(b.name)}">${b.name}</option>`).join("")
    : `<option value="">No backups found</option>`;
  renderTable(
    qs("#backupTable"),
    ["Backup File", "Modified", "Size"],
    state.backups.map((b) => [b.name, b.modified, `${Math.round((b.size || 0) / 1024)} KB`])
  );

  document.querySelectorAll("[data-save-discount]").forEach((button) => button.addEventListener("click", () => saveDiscount(button.dataset.saveDiscount)));
  document.querySelectorAll("[data-delete-discount]").forEach((button) => button.addEventListener("click", () => deleteDiscount(button.dataset.deleteDiscount)));
  document.querySelectorAll("[data-save-user]").forEach((button) => button.addEventListener("click", () => saveUser(button.dataset.saveUser)));
  document.querySelectorAll("[data-delete-user]").forEach((button) => button.addEventListener("click", () => deleteUser(button.dataset.deleteUser)));
}

function applyInstitutionDefaults() {
  const form = qs("#settingsForm");
  const name = form.elements.institution_name.value.toLowerCase();
  if (name.includes("kumon")) {
    form.elements.subjects_offered.value = "Math\nEnglish";
    toast("Kumon default subjects selected");
  }
}

function staffMembers() {
  return state.staff?.members || [];
}

function staffSchedules() {
  return state.staff?.schedules || [];
}

function staffPunches() {
  return state.staff?.punches || [];
}

function staffWeekdays() {
  return state.staff?.weekdays?.length ? state.staff.weekdays : ["Mon", "Tue", "Wed", "Thu", "Fri"];
}

const STAFFBASE_SAMPLE_USERS = [
  { id: "sample-1", staff_name: "Dr. Sarah Mitchell", email: "s.mitchell@greenfield.edu", role_title: "Principal", subject: "Administration", pin: "0000", phone: "416-555-0001", hourly_rate: 42, active: true },
  { id: "sample-2", staff_name: "James Harrington", email: "j.harrington@greenfield.edu", role_title: "Vice Principal", subject: "Administration", pin: "1111", phone: "416-555-0002", hourly_rate: 38, active: true },
  { id: "sample-3", staff_name: "Rachel Torres", email: "r.torres@greenfield.edu", role_title: "Office Manager", subject: "Administration", pin: "2222", phone: "416-555-0003", hourly_rate: 32, active: true },
  { id: "sample-4", staff_name: "Emily Chen", email: "e.chen@greenfield.edu", role_title: "Math Teacher", subject: "Mathematics", pin: "3333", phone: "416-555-0011", hourly_rate: 28, active: true },
  { id: "sample-5", staff_name: "Marcus Williams", email: "m.williams@greenfield.edu", role_title: "Science Teacher", subject: "Science", pin: "4444", phone: "416-555-0012", hourly_rate: 28, active: true },
  { id: "sample-6", staff_name: "Priya Sharma", email: "p.sharma@greenfield.edu", role_title: "English Teacher", subject: "English", pin: "5555", phone: "416-555-0013", hourly_rate: 28, active: true },
  { id: "sample-7", staff_name: "David O'Brien", email: "d.obrien@greenfield.edu", role_title: "History Teacher", subject: "History", pin: "6666", phone: "416-555-0014", hourly_rate: 27, active: true },
  { id: "sample-8", staff_name: "Aisha Patel", email: "a.patel@greenfield.edu", role_title: "Art Teacher", subject: "Arts", pin: "7777", phone: "416-555-0015", hourly_rate: 27, active: true },
  { id: "sample-9", staff_name: "Tom Nakamura", email: "t.nakamura@greenfield.edu", role_title: "PE Teacher", subject: "PE", pin: "8888", phone: "416-555-0016", hourly_rate: 27, active: true },
  { id: "sample-10", staff_name: "Lisa Kowalski", email: "l.kowalski@greenfield.edu", role_title: "Counselor", subject: "Support", pin: "9999", phone: "416-555-0017", hourly_rate: 30, active: true },
];

const STAFFBASE_SHIFT_TYPES = {
  Teaching: ["Teaching", "#1a65d4", "#e6effe"],
  Planning: ["Planning", "#5b3fc4", "#eeeafe"],
  Supervision: ["Supervision", "#c97d10", "#fef3dc"],
  Meeting: ["Meeting", "#0a8c6a", "#dcf5ee"],
  "Prof Dev": ["Prof. Dev.", "#c42b1c", "#fde8e6"],
  Off: ["Day Off", "#8fa5bc", "#f0f4f8"],
};

function staffbaseMembers() {
  return staffMembers().length ? staffMembers() : STAFFBASE_SAMPLE_USERS;
}

function staffbaseIsSample() {
  return !staffMembers().length;
}

function staffbaseInitials(name) {
  return String(name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "?";
}

function staffbaseAvatar(member, size = 34) {
  const colors = ["#0c1e35", "#0a8c6a", "#1a65d4", "#5b3fc4", "#c97d10", "#c42b1c", "#0a7c9e"];
  const name = member.staff_name || "";
  const index = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % colors.length;
  return `<span class="staffbase-avatar" style="width:${size}px;height:${size}px;background:${colors[index]}">${staffbaseInitials(name)}</span>`;
}

function staffbaseShiftTag(type) {
  const [label, color, bg] = STAFFBASE_SHIFT_TYPES[type] || STAFFBASE_SHIFT_TYPES.Off;
  return `<span class="staffbase-shift-tag" style="background:${bg};color:${color};border-color:${color}22">${label}</span>`;
}

function staffbaseDefaultSchedules() {
  const patterns = {
    "sample-3": ["Meeting", "Teaching", "Planning", "Teaching", "Supervision"],
    "sample-4": ["Teaching", "Planning", "Teaching", "Teaching", "Teaching"],
    "sample-5": ["Teaching", "Teaching", "Supervision", "Teaching", "Meeting"],
    "sample-6": ["Planning", "Teaching", "Teaching", "Teaching", "Teaching"],
    "sample-7": ["Teaching", "Teaching", "Meeting", "Teaching", "Supervision"],
    "sample-8": ["Teaching", "Supervision", "Teaching", "Teaching", "Teaching"],
    "sample-9": ["Prof Dev", "Teaching", "Teaching", "Teaching", "Planning"],
    "sample-10": ["Teaching", "Teaching", "Planning", "Meeting", "Teaching"],
  };
  return Object.entries(patterns).flatMap(([staffId, shifts]) =>
    staffWeekdays().map((weekday, index) => {
      const shiftType = shifts[index] || "Off";
      return {
        id: `${staffId}-${weekday}`,
        staff_id: staffId,
        weekday,
        shift_type: shiftType,
        start_time: shiftType === "Off" ? "" : "15:30",
        end_time: shiftType === "Off" ? "" : "18:30",
        location: shiftType === "Teaching" ? `Room ${100 + Number(staffId.replace("sample-", ""))}` : shiftType === "Meeting" ? "Staff Room" : shiftType === "Supervision" ? "Main Hallway" : "Office",
        published: true,
      };
    })
  );
}

function staffbaseSchedules() {
  return staffSchedules().length ? staffSchedules() : staffbaseDefaultSchedules();
}

function staffbasePunches() {
  if (staffPunches().length) return staffPunches();
  return [
    { id: "sample-p1", staff_id: "sample-4", staff_name: "Emily Chen", role_title: "Math Teacher", punch_date: "2026-05-31", clock_in: "03:25 PM", clock_out: "", duration_hours: 0, source: "GPS verified", notes: "On site" },
    { id: "sample-p2", staff_id: "sample-5", staff_name: "Marcus Williams", role_title: "Science Teacher", punch_date: "2026-05-31", clock_in: "03:31 PM", clock_out: "", duration_hours: 0, source: "GPS verified", notes: "On site" },
    { id: "sample-p3", staff_id: "sample-6", staff_name: "Priya Sharma", role_title: "English Teacher", punch_date: "2026-05-30", clock_in: "03:30 PM", clock_out: "06:32 PM", duration_hours: 3.03, source: "Manual", notes: "" },
  ];
}

function staffbaseHours(row) {
  if (row.duration_hours) return Number(row.duration_hours) || 0;
  if (!row.start_time || !row.end_time || row.shift_type === "Off") return 0;
  const [sh, sm] = row.start_time.split(":").map(Number);
  const [eh, em] = row.end_time.split(":").map(Number);
  return Math.max(0, ((eh * 60 + em) - (sh * 60 + sm)) / 60);
}

function renderStaffAdministration() {
  const workspace = qs("#staffWorkspace");
  if (!workspace) return;
  qs("#staffLockedState")?.classList.toggle("collapsed", Boolean(state.can_access_staff));
  workspace.classList.toggle("collapsed", !state.can_access_staff);
  if (!state.can_access_staff) return;
  const sampleNote = staffbaseIsSample() ? `<span class="staffbase-sample">Sample data from tested StaffBase code</span>` : `<span class="staffbase-sample live">Live database</span>`;
  workspace.innerHTML = `
    <aside class="staffbase-sidebar">
      <div class="staffbase-logo">
        <div class="staffbase-logo-mark">S</div>
        <div><strong>StaffBase</strong><span>${escapeHtml(state.settings?.institution_name || "After School Centre")}</span></div>
      </div>
      <div class="staffbase-user">
        ${staffbaseAvatar({ staff_name: state.current_user?.name || "SMP Admin" }, 34)}
        <div><strong>${escapeHtml(state.current_user?.name || "SMP Admin")}</strong><span>${escapeHtml(state.current_user?.role || "Admin / Manager")}</span></div>
      </div>
      <nav class="staffbase-nav">
        ${[
          ["staff-dashboard", "Dashboard"],
          ["staff-schedule", "Weekly Schedule"],
          ["staff-timesheets", "Timesheets"],
          ["staff-roster", "Staff Roster"],
          ["staff-clock", "Time Clock"],
        ].map(([view, label]) => `<button type="button" data-staff-view="${view}" class="${activeStaffView === view ? "active" : ""}">${label}</button>`).join("")}
      </nav>
    </aside>
    <div class="staffbase-main">
      <header class="staffbase-topbar">
        <div><h2>${staffbaseTitle()}</h2>${sampleNote}</div>
        <div class="staffbase-live-clock">${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
      </header>
      <main class="staffbase-content">${staffbaseContent()}</main>
    </div>`;
  workspace.querySelectorAll("[data-staff-view]").forEach((button) => {
    button.addEventListener("click", () => {
      activeStaffView = button.dataset.staffView;
      renderStaffAdministration();
    });
  });
  workspace.querySelector("#staffbaseSearch")?.addEventListener("input", () => renderStaffbaseRoster());
  renderStaffbaseRoster();
}

function staffbaseTitle() {
  return {
    "staff-dashboard": "Live Attendance & Labor Overview",
    "staff-schedule": "Weekly Class & Duty Schedule",
    "staff-timesheets": "Timesheets",
    "staff-roster": "Staff Roster",
    "staff-clock": "Time Clock",
  }[activeStaffView] || "Staff Administration";
}

function staffbaseContent() {
  if (activeStaffView === "staff-schedule") return staffbaseScheduleView();
  if (activeStaffView === "staff-timesheets") return staffbaseTimesheetsView();
  if (activeStaffView === "staff-roster") return staffbaseRosterView();
  if (activeStaffView === "staff-clock") return staffbaseClockView();
  return staffbaseDashboardView();
}

function staffbaseDashboardView() {
  const members = staffbaseMembers().filter((member) => member.active);
  const punches = staffbasePunches();
  const activePunches = punches.filter((punch) => !punch.clock_out);
  const schedules = staffbaseSchedules();
  const laborHours = schedules.reduce((sum, row) => sum + staffbaseHours(row), 0);
  const laborCost = schedules.reduce((sum, row) => {
    const member = members.find((item) => String(item.id) === String(row.staff_id));
    return sum + staffbaseHours(row) * Number(member?.hourly_rate || 0);
  }, 0);
  return `
    <div class="staffbase-banner">
      <div><strong>Good ${new Date().getHours() < 12 ? "morning" : new Date().getHours() < 17 ? "afternoon" : "evening"}.</strong><span>Schedule published for May 26 - 30, 2026</span></div>
      <span class="staffbase-badge gold">Published</span>
    </div>
    <div class="staffbase-metrics">
      <div><span>Total Staff</span><strong>${members.length}</strong><small>active employees</small></div>
      <div><span>Clocked In</span><strong>${activePunches.length}</strong><small>on-site right now</small></div>
      <div><span>Labor Hours</span><strong>${number(laborHours)}h</strong><small>scheduled this week</small></div>
      <div><span>Est. Labor Cost</span><strong>${money(laborCost)}</strong><small>this week</small></div>
    </div>
    <div class="staffbase-grid two">
      <section class="staffbase-card">
        <div class="staffbase-section-head"><h3>Today's attendance</h3><button type="button" data-staff-view="staff-clock">Time Clock</button></div>
        ${members.map((member) => {
          const punch = activePunches.find((row) => String(row.staff_id) === String(member.id));
          return `<div class="staffbase-attendance ${punch ? "active" : ""}">
            <div>${staffbaseAvatar(member, 30)}<span><strong>${escapeHtml(member.staff_name)}</strong><small>${escapeHtml(member.role_title || "Staff")}</small></span></div>
            <span class="staffbase-badge ${punch ? "green" : "gray"}">${punch ? `In ${escapeHtml(punch.clock_in || "")}` : "Away"}</span>
          </div>`;
        }).join("")}
      </section>
      <section class="staffbase-card">
        <div class="staffbase-section-head"><h3>Schedule exceptions</h3><span class="staffbase-badge gold">2 waiting</span></div>
        <div class="staffbase-request"><strong>Emily Chen</strong><span>Vacation request - Jun 3 to Jun 5</span></div>
        <div class="staffbase-request"><strong>Priya Sharma</strong><span>Professional development - Jun 5</span></div>
        <div class="staffbase-section-head lower"><h3>Announcements</h3></div>
        <div class="staffbase-request priority"><strong>End-of-year assembly</strong><span>All staff required on Friday June 14 at 9:00 AM.</span></div>
      </section>
    </div>`;
}

function staffbaseScheduleView() {
  const members = staffMembers().filter((member) => member.active);
  const displayMembers = members.length ? members : staffbaseMembers().filter((member) => member.active && !["Principal", "Vice Principal"].includes(member.role_title));
  const scheduleByStaff = {};
  for (const row of staffbaseSchedules()) {
    scheduleByStaff[row.staff_id] = scheduleByStaff[row.staff_id] || {};
    scheduleByStaff[row.staff_id][row.weekday] = row;
  }
  return `
    <section class="staffbase-card">
      <div class="staffbase-section-head"><div><h3>Week: May 26 - 30, 2026</h3><span>Clicking live database cells can be added next; this view restores the tested layout first.</span></div><span class="staffbase-badge green">Published</span></div>
      <div class="staffbase-schedule-grid" style="grid-template-columns:190px repeat(${staffWeekdays().length}, 1fr)">
        <div class="staffbase-schedule-head staff">Staff Member</div>
        ${staffWeekdays().map((day) => `<div class="staffbase-schedule-head">${day}</div>`).join("")}
        ${displayMembers.map((member) => `
          <div class="staffbase-schedule-employee">${staffbaseAvatar(member, 30)}<span><strong>${escapeHtml(member.staff_name)}</strong><small>${escapeHtml(member.subject || "")}</small></span></div>
          ${staffWeekdays().map((day) => {
        const shift = scheduleByStaff[member.id]?.[day];
        if (!shift || String(shift.shift_type || "").toLowerCase() === "off") return `<div class="staffbase-schedule-cell muted">${staffbaseShiftTag("Off")}</div>`;
        return `<div class="staffbase-schedule-cell">${staffbaseShiftTag(shift.shift_type || "Teaching")}<strong>${escapeHtml(shift.start_time || "")} - ${escapeHtml(shift.end_time || "")}</strong><small>${escapeHtml(shift.location || "Centre")}</small></div>`;
      }).join("")}
        `).join("")}
      </div>
    </section>`;
}

function staffbaseTimesheetsView() {
  const members = staffbaseMembers().filter((member) => member.active && !["Principal", "Vice Principal"].includes(member.role_title));
  const schedules = staffbaseSchedules();
  return `
    <section class="staffbase-card">
      <div class="staffbase-section-head"><h3>Weekly labor summary</h3><button type="button">Export CSV</button></div>
      <table class="staffbase-table">
        <thead><tr><th>Employee</th>${staffWeekdays().map((day) => `<th>${day}</th>`).join("")}<th>Total</th><th>Status</th></tr></thead>
        <tbody>${members.map((member) => {
          const rows = schedules.filter((row) => String(row.staff_id) === String(member.id));
          const total = rows.reduce((sum, row) => sum + staffbaseHours(row), 0);
          return `<tr><td>${staffbaseAvatar(member, 30)}<strong>${escapeHtml(member.staff_name)}</strong><small>${escapeHtml(member.subject || "")}</small></td>
            ${staffWeekdays().map((day) => {
              const row = rows.find((item) => item.weekday === day);
              return `<td>${row && row.shift_type !== "Off" ? `${number(staffbaseHours(row))}h` : "-"}</td>`;
            }).join("")}
            <td><strong>${number(total)}h</strong></td><td><span class="staffbase-badge ${total >= 12 ? "green" : "gold"}">${total >= 12 ? "Complete" : "Partial"}</span></td></tr>`;
        }).join("")}</tbody>
      </table>
    </section>`;
}

function staffbaseRosterView() {
  return `
    <section class="staffbase-card">
      <div class="staffbase-section-head"><h3>Staff directory</h3><input id="staffbaseSearch" class="staffbase-search" placeholder="Search by name, role, department"></div>
      <table class="staffbase-table">
        <thead><tr><th>Name & Email</th><th>Role</th><th>Department</th><th>PIN</th><th>Phone</th><th>Status</th></tr></thead>
        <tbody id="staffbaseRosterBody"></tbody>
      </table>
    </section>`;
}

function renderStaffbaseRoster() {
  const body = qs("#staffbaseRosterBody");
  if (!body) return;
  const query = qs("#staffbaseSearch")?.value?.toLowerCase() || "";
  body.innerHTML = staffbaseMembers()
    .filter((member) => !query || [member.staff_name, member.role_title, member.subject, member.email].some((value) => String(value || "").toLowerCase().includes(query)))
    .map((member) => `<tr>
      <td>${staffbaseAvatar(member, 32)}<strong>${escapeHtml(member.staff_name)}</strong><small>${escapeHtml(member.email || "")}</small></td>
      <td><span class="staffbase-badge blue">${escapeHtml(member.role_title || "Staff")}</span></td>
      <td>${escapeHtml(member.subject || "Administration")}</td>
      <td><code>${escapeHtml(member.pin || "0000")}</code></td>
      <td>${escapeHtml(member.phone || "")}</td>
      <td><span class="staffbase-badge ${member.active ? "green" : "gray"}">${member.active ? "Active" : "Inactive"}</span></td>
    </tr>`)
    .join("");
}

function staffbaseClockView() {
  const punches = staffbasePunches();
  const activePunches = punches.filter((punch) => !punch.clock_out);
  return `
    <div class="staffbase-grid two">
      <section class="staffbase-card">
        <div class="staffbase-phone">
          <div class="staffbase-phone-head"><span>StaffBase Time Clock</span><strong>${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</strong></div>
          <div class="staffbase-phone-body">
            ${staffbaseMembers().filter((member) => member.active && member.role_title.includes("Teacher")).map((member) => {
              const active = activePunches.some((punch) => String(punch.staff_id) === String(member.id));
              return `<div class="staffbase-mobile-row ${active ? "selected" : ""}">${staffbaseAvatar(member, 30)}<span><strong>${escapeHtml(member.staff_name)}</strong><small>${escapeHtml(member.role_title)}</small></span><span class="staffbase-badge ${active ? "green" : "gold"}">${active ? "Clocked In" : "Away"}</span></div>`;
            }).join("")}
          </div>
        </div>
      </section>
      <section class="staffbase-card">
        <div class="staffbase-section-head"><h3>Check-in log</h3><span class="staffbase-badge green">GPS active</span></div>
        ${punches.map((punch) => `<div class="staffbase-attendance ${!punch.clock_out ? "active" : ""}">
          <div>${staffbaseAvatar({ staff_name: punch.staff_name }, 30)}<span><strong>${escapeHtml(punch.staff_name)}</strong><small>${escapeHtml(punch.clock_in || "")}${punch.clock_out ? ` - ${escapeHtml(punch.clock_out)}` : " - Active"}</small></span></div>
          <span class="staffbase-badge ${!punch.clock_out ? "green" : "gray"}">${escapeHtml(punch.source || "Manual")}</span>
        </div>`).join("")}
      </section>
    </div>`;
}

function escapeAttr(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
}

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function renderBatch() {
  const headers = ["#", "Student Name *", "Parent / Guardian", "Status *", "Enrol Date", "Subjects *", "Rate Type", "STD Fee *", "Pay Method", "Phone", "Email"];
  const subjectOptions = configuredSubjects().map((subject) => `<option value="${escapeAttr(subject)}">${subject}</option>`).join("");
  const body = Array.from({ length: 10 }, (_, index) => `
    <tr>
      <td>${index + 1}</td>
      <td><input name="student_name"></td>
      <td><input name="parent_guardian"></td>
      <td><select name="status"><option>C</option><option>D</option></select></td>
      <td><input type="date" name="enrol_date"></td>
      <td><select name="subjects" class="batch-subject-select" multiple>${subjectOptions}</select></td>
      <td><select name="rate_type"><option>Regular</option><option>EL</option></select></td>
      <td><input type="number" step="0.01" name="std_monthly_fee"></td>
      <td><select name="payment_method"><option>PAD</option><option>E-Transfer</option><option>Cash</option><option>Credit Card</option><option>Cheque</option></select></td>
      <td><input name="phone"></td>
      <td><input name="email"></td>
    </tr>
  `).join("");
  qs("#batchTable").innerHTML = `<thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
  renderBatchImportPreview();
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (ch === '"' && quoted && next === '"') {
      cell += '"';
      i += 1;
    } else if (ch === '"') {
      quoted = !quoted;
    } else if (ch === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((ch === "\n" || ch === "\r") && !quoted) {
      if (ch === "\r" && next === "\n") i += 1;
      row.push(cell);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  row.push(cell);
  if (row.some((value) => value.trim())) rows.push(row);
  if (rows.length < 2) return [];
  const headers = rows[0].map((h) => h.trim().toLowerCase());
  return rows.slice(1).map((values) => {
    const item = {};
    headers.forEach((header, index) => {
      item[header] = (values[index] || "").trim();
    });
    return item;
  });
}

function pick(row, names) {
  const keys = Object.keys(row);
  for (const name of names) {
    const match = keys.find((key) => key === name || key.replace(/[^a-z0-9]/g, "") === name.replace(/[^a-z0-9]/g, ""));
    if (match && row[match]) return row[match];
  }
  return "";
}

function matchedKey(row, names) {
  const keys = Object.keys(row);
  for (const name of names) {
    const normalizedName = name.replace(/[^a-z0-9]/g, "");
    const match = keys.find((key) => key === name || key.replace(/[^a-z0-9]/g, "") === normalizedName);
    if (match) return match;
  }
  return "";
}

function normalizeMoney(value) {
  const text = String(value || "").replace(/[$,\s]/g, "");
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalizeDateInput(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const serial = Number(text);
  if (Number.isFinite(serial) && serial >= 20000 && serial <= 80000) {
    const date = new Date(Date.UTC(1899, 11, 30 + serial));
    return date.toISOString().slice(0, 10);
  }
  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) {
    const utc = new Date(Date.UTC(parsed.getFullYear(), parsed.getMonth(), parsed.getDate()));
    return utc.toISOString().slice(0, 10);
  }
  return text;
}

const batchColumnAliases = {
  student_name: ["student name", "student", "name"],
  parent_guardian: ["parent guardian", "parent / guardian", "parent", "guardian", "payer"],
  status: ["status"],
  enrol_date: ["enrol date", "enrolment date", "enrollment date", "start date"],
  subjects: ["subjects", "subject", "subject selections"],
  rate_type: ["rate type", "type"],
  std_monthly_fee: ["std fee", "standard monthly fee", "std monthly fee", "monthly fee", "fee"],
  payment_method: ["payment method", "pay method", "method"],
  phone: ["phone", "mobile"],
  email: ["email", "e-mail"],
  siblings: ["siblings"],
  notes: ["notes", "comments"],
};

function miscellaneousCsvData(row) {
  const knownKeys = new Set(Object.values(batchColumnAliases).flatMap((aliases) => {
    const key = matchedKey(row, aliases);
    return key ? [key] : [];
  }));
  return Object.entries(row)
    .filter(([key, value]) => !knownKeys.has(key) && String(value || "").trim())
    .map(([key, value]) => ({ heading: key, value: String(value).trim() }));
}

function normalizeBatchRow(row) {
  const rawSubjects = pick(row, batchColumnAliases.subjects);
  const subjects = subjectText(rawSubjects);
  const feeColumn = matchedKey(row, batchColumnAliases.std_monthly_fee);
  const miscellaneous = miscellaneousCsvData(row);
  const baseNotes = pick(row, batchColumnAliases.notes).trim();
  const miscText = miscellaneous.length
    ? `Miscellaneous CSV Data: ${miscellaneous.map((item) => `${item.heading}: ${item.value}`).join("; ")}`
    : "";
  return {
    student_name: pick(row, batchColumnAliases.student_name).trim(),
    parent_guardian: pick(row, batchColumnAliases.parent_guardian).trim(),
    status: (pick(row, batchColumnAliases.status) || "C").trim().toUpperCase().slice(0, 1),
    enrol_date: normalizeDateInput(pick(row, batchColumnAliases.enrol_date)),
    subjects,
    rate_type: (pick(row, batchColumnAliases.rate_type) || "Regular").trim(),
    std_monthly_fee: feeColumn ? row[feeColumn] : "",
    std_fee_column_found: Boolean(feeColumn),
    payment_method: (pick(row, batchColumnAliases.payment_method) || "PAD").trim(),
    phone: pick(row, batchColumnAliases.phone).trim(),
    email: pick(row, batchColumnAliases.email).trim(),
    siblings: pick(row, batchColumnAliases.siblings).trim(),
    notes: [baseNotes, miscText].filter(Boolean).join("\n"),
    miscellaneous,
  };
}

function validateBatchStudent(row, seen) {
  const errors = [];
  const warnings = [];
  if (!row.student_name) errors.push("Student name missing");
  if (!["C", "D"].includes(row.status)) errors.push("Status must be C or D");
  if (!row.subjects) errors.push("Subject missing");
  if (row.enrol_date && Number.isNaN(Date.parse(row.enrol_date))) errors.push("Enrol date not valid");
  if (!row.std_fee_column_found) errors.push("STD Fee column missing");
  else if (!String(row.std_monthly_fee || "").trim()) errors.push("STD Fee value missing");
  else if (normalizeMoney(row.std_monthly_fee) <= 0) errors.push("STD Fee not valid");
  const key = `${row.student_name.toLowerCase()}|${row.parent_guardian.toLowerCase()}`;
  if (seen.has(key)) warnings.push("Duplicate in this CSV - Admin acceptance required");
  seen.add(key);
  const existingRecords = state.students.filter((student) =>
    String(student.student_name || "").toLowerCase() === row.student_name.toLowerCase()
    && String(student.parent_guardian || "").toLowerCase() === row.parent_guardian.toLowerCase()
  );
  if (existingRecords.length) {
    const statuses = existingRecords.map((student) => student.status).join(", ");
    warnings.push(`Already exists in Student Roster with status ${statuses} - Admin acceptance required`);
  }
  return { errors, warnings };
}

function buildBatchImportPreview(rows) {
  const seen = new Set();
  return rows.map((row, index) => {
    const normalized = normalizeBatchRow(row);
    const validation = validateBatchStudent(normalized, seen);
    return {
      row_number: index + 1,
      data: normalized,
      errors: validation.errors,
      warnings: validation.warnings,
      accepted: validation.warnings.length === 0,
      valid: validation.errors.length === 0 && validation.warnings.length === 0,
    };
  });
}

function revalidateBatchImportPreview() {
  const seen = new Set();
  state.batchImportPreview = (state.batchImportPreview || []).map((row, index) => {
    const data = row.data;
    const validation = validateBatchStudent(data, seen);
    return {
      ...row,
      row_number: index + 1,
      errors: validation.errors,
      warnings: validation.warnings,
      accepted: validation.warnings.length ? Boolean(row.accepted) : true,
      valid: validation.errors.length === 0 && (!validation.warnings.length || Boolean(row.accepted)),
    };
  });
}

function importInput(index, field, value, type = "text") {
  return `<input class="import-edit-input" type="${type}" data-batch-index="${index}" data-batch-field="${field}" value="${escapeAttr(value || "")}">`;
}

function renderBatchImportPreview() {
  const panel = qs("#batchImportPanel");
  if (!panel) return;
  const rows = state.batchImportPreview || [];
  if (!rows.length) {
    panel.classList.add("collapsed");
    return;
  }
  panel.classList.remove("collapsed");
  const valid = rows.filter((row) => row.valid).length;
  const needsAcceptance = rows.filter((row) => row.warnings?.length && !row.accepted).length;
  const invalid = rows.filter((row) => row.errors?.length).length;
  qs("#batchImportSummary").textContent = `${state.batchImportFileName || "CSV"}: ${valid} green, ${needsAcceptance} need Admin acceptance, ${invalid} need correction. Final import will post only green rows.`;
  qs("#applyBatchImport").disabled = valid === 0;
  const body = rows.map((row, index) => [
    row.row_number,
    row.valid ? `<span class="confidence high">Green</span>` : row.errors.length ? `<span class="confidence low">Fix Required</span>` : `<span class="confidence medium">Accept Required</span>`,
    importInput(index, "student_name", row.data.student_name),
    importInput(index, "parent_guardian", row.data.parent_guardian),
    `<select class="import-edit-input" data-batch-index="${index}" data-batch-field="status">
      <option value="C" ${row.data.status === "C" ? "selected" : ""}>C</option>
      <option value="D" ${row.data.status === "D" ? "selected" : ""}>D</option>
    </select>`,
    importInput(index, "enrol_date", row.data.enrol_date, "date"),
    importInput(index, "subjects", row.data.subjects),
    importInput(index, "rate_type", row.data.rate_type),
    importInput(index, "std_monthly_fee", row.data.std_monthly_fee, "number"),
    importInput(index, "payment_method", row.data.payment_method),
    importInput(index, "phone", row.data.phone),
    importInput(index, "email", row.data.email, "email"),
    row.data.miscellaneous.length ? escapeHtml(row.data.miscellaneous.map((item) => item.heading).join(", ")) : "-",
    escapeHtml([...(row.errors || []), ...(row.warnings || [])].join("; ") || "Validated"),
    `<div class="row-actions">
      <button type="button" class="small ${row.accepted ? "accepted-action" : ""}" data-accept-import-row="${index}">${row.accepted ? "Accepted" : "Accept"}</button>
      <button type="button" class="small danger" data-delete-import-row="${index}">Delete</button>
    </div>`,
  ]);
  renderTable(qs("#batchImportTable"), ["Row", "Status", "Student", "Parent / Guardian", "C/D", "Enrol Date", "Subjects", "Rate Type", "STD Fee", "Pay Method", "Phone", "Email", "Miscellaneous Headings", "Validation", "Action"], body);
  document.querySelectorAll("[data-batch-field]").forEach((control) => {
    control.addEventListener("change", () => updateBatchImportCell(control));
    control.addEventListener("input", () => updateBatchImportCell(control, true));
  });
  document.querySelectorAll("[data-delete-import-row]").forEach((button) => {
    button.addEventListener("click", () => deleteBatchImportRow(Number(button.dataset.deleteImportRow)));
  });
  document.querySelectorAll("[data-accept-import-row]").forEach((button) => {
    button.addEventListener("click", () => acceptBatchImportRow(Number(button.dataset.acceptImportRow)));
  });
}

function updateBatchImportCell(control, quiet = false) {
  const row = state.batchImportPreview[Number(control.dataset.batchIndex)];
  if (!row) return;
  const field = control.dataset.batchField;
  row.data[field] = control.value;
  if (field === "status") row.data.status = String(control.value || "").toUpperCase().slice(0, 1);
  if (field === "subjects") row.data.subjects = subjectText(control.value);
  if (field === "std_monthly_fee") row.data.std_fee_column_found = true;
  row.accepted = false;
  revalidateBatchImportPreview();
  if (!quiet) {
    renderBatchImportPreview();
    toast("Import row updated and revalidated");
  } else {
    const valid = state.batchImportPreview.filter((item) => item.valid).length;
    const needsAcceptance = state.batchImportPreview.filter((item) => item.warnings?.length && !item.accepted).length;
    const invalid = state.batchImportPreview.filter((item) => item.errors?.length).length;
    qs("#batchImportSummary").textContent = `${state.batchImportFileName || "CSV"}: ${valid} green, ${needsAcceptance} need Admin acceptance, ${invalid} need correction. Final import will post only green rows.`;
    qs("#applyBatchImport").disabled = valid === 0;
  }
}

function acceptBatchImportRow(index) {
  const row = state.batchImportPreview[index];
  if (!row) return;
  if (row.errors.length) {
    toast("Fix required fields before accepting this row");
    return;
  }
  row.accepted = true;
  revalidateBatchImportPreview();
  renderBatchImportPreview();
  toast("Row accepted by Admin");
}

function deleteBatchImportRow(index) {
  const row = state.batchImportPreview[index];
  if (!row) return;
  if (!confirm(`Remove ${row.data.student_name || "this row"} from the import preview?`)) return;
  state.batchImportPreview.splice(index, 1);
  revalidateBatchImportPreview();
  renderBatchImportPreview();
  toast("Import row removed");
}

async function applyBatchImport() {
  const validRows = (state.batchImportPreview || []).filter((row) => row.valid).map((row) => ({
    ...row.data,
    _preview_row: row.row_number,
    std_monthly_fee: normalizeMoney(row.data.std_monthly_fee),
  }));
  if (!validRows.length) {
    toast("No valid student rows are ready to import");
    return;
  }
  const totalLines = state.batchImportPreview.length;
  const blocked = totalLines - validRows.length;
  if (!confirm(`CSV lines in preview: ${totalLines}\nRecords to post: ${validRows.length}\nRows not posted: ${blocked}\n\nContinue and update Student Roster, Fee Tracker, and Dashboard?`)) return;
  const result = await api("/api/batch", { method: "POST", body: JSON.stringify({ rows: validRows }) });
  let rejectedPreview = null;
  const rejected = result.rejected || [];
  if (rejected.length) {
    const rejectedRows = new Set(rejected.map((item) => Number(item.row)));
    rejectedPreview = state.batchImportPreview
      .filter((row) => rejectedRows.has(row.row_number))
      .map((row) => ({
        ...row,
        accepted: false,
        valid: false,
        errors: [rejected.find((item) => Number(item.row) === row.row_number)?.error || "Server rejected this row"],
      }));
    toast(`${result.saved} imported. ${rejected.length} row${rejected.length === 1 ? "" : "s"} still need correction.`);
  } else {
    state.batchImportRows = [];
    state.batchImportPreview = [];
    toast(`${result.saved} student record${result.saved === 1 ? "" : "s"} imported`);
  }
  await load();
  if (rejectedPreview) {
    state.batchImportPreview = rejectedPreview;
    renderBatchImportPreview();
  }
}

function paymentCsvRows(rows) {
  return rows.map((row) => {
    const debit = normalizeMoney(pick(row, ["debit", "withdrawal"]));
    const credit = normalizeMoney(pick(row, ["credit", "deposit"]));
    const amount = Math.abs(normalizeMoney(pick(row, ["amount"])) || credit || debit);
    return {
      date: pick(row, ["date", "transaction date", "posted date"]),
      description: pick(row, ["description", "memo", "details", "name", "payee"]),
      amount,
      source: pick(row, ["source", "account", "institution", "card"]),
    };
  }).filter((row) => row.date || row.description || row.amount);
}

function renderReconciliation() {
  if (!qs("#reconciliationTable")) return;
  const approved = (state.reconciliation || []).find((item) => item.match_status === "approved") || {};
  const rows = state.reconciliationPreview || [];
  const verifiedCount = rows.filter((row) => row.verified).length;
  qs("#reconSummary").innerHTML = [
    `<div class="info-tile"><span>Approved Matches</span><strong>${approved.count || 0}</strong></div>`,
    `<div class="info-tile"><span>Approved Total</span><strong>${money(approved.total || 0)}</strong></div>`,
    `<div class="info-tile"><span>Saved Payer Aliases</span><strong>${state.payer_aliases.length}</strong></div>`,
    `<div class="info-tile"><span>Ready to Add</span><strong>${verifiedCount} / ${rows.length}</strong></div>`,
  ].join("");
  qs("#reconReadyCount").textContent = `${verifiedCount} verified`;
  qs("#applyVerifiedRows").disabled = verifiedCount === 0;

  if (!rows.length) {
    qs("#reconciliationTable").innerHTML = `<tbody><tr><td class="empty-state">Upload a bank or credit-card CSV to preview matches.</td></tr></tbody>`;
    return;
  }
  const body = rows.map((row, index) => {
    const best = row.best_match || {};
    const confidence = best.confidence || "low";
    const reasons = (best.reasons || []).join("; ") || "No strong matching reason yet";
    const selectedStudentId = row.selected_student_id || best.student_id || "";
    const selectedCandidate = (row.candidates || []).find((candidate) => String(candidate.student_id) === String(selectedStudentId)) || best;
    const monthValue = row.selected_month || row.month_label || "";
    const buttonClass = row.verified ? "verify-action verified" : "verify-action needs-review";
    const buttonLabel = row.verified ? (row.manually_verified ? "Verified Now to ADD" : "Verified to ADD") : "Verify and Correct";
    return [
      row.date,
      escapeHtml(row.description),
      money(row.amount),
      escapeHtml(row.source || "-"),
      `<select data-recon-student="${index}">
        ${(row.candidates || []).map((candidate) => `<option value="${candidate.student_id}" ${String(candidate.student_id) === String(selectedStudentId) ? "selected" : ""}>${escapeHtml(candidate.student_name)} - ${escapeHtml(candidate.parent_guardian || "No guardian")} - ${candidate.score}%</option>`).join("")}
      </select>`,
      escapeHtml(selectedCandidate?.parent_guardian || "-"),
      money(selectedCandidate?.expected_fee || 0),
      `<select data-recon-month="${index}">${state.months.map((month) => `<option value="${month}" ${month === monthValue ? "selected" : ""}>${month}</option>`).join("")}</select>`,
      selectedCandidate?.previous_month ? `${selectedCandidate.previous_month}: ${money(selectedCandidate.previous_paid || 0)}` : "-",
      `<span class="confidence ${confidence}">${confidence}</span>`,
      `<span class="muted-note">${escapeHtml(reasons)}</span>`,
      `<button class="${buttonClass}" data-verify-recon="${index}">${buttonLabel}</button>`,
    ];
  });
  renderTable(qs("#reconciliationTable"), ["CSV Date", "CSV Description", "CSV Amount", "CSV Source", "Suggested Student", "Parent / Guardian", "Expected Fee", "Fee Month", "Previous Month", "Confidence", "Match Reason", "Validation"], body);
  document.querySelectorAll("[data-recon-student]").forEach((select) => select.addEventListener("change", () => updateReconSelection(Number(select.dataset.reconStudent))));
  document.querySelectorAll("[data-recon-month]").forEach((select) => select.addEventListener("change", () => updateReconSelection(Number(select.dataset.reconMonth))));
  document.querySelectorAll("[data-verify-recon]").forEach((button) => button.addEventListener("click", () => verifyReconciliationRow(Number(button.dataset.verifyRecon))));
}

function renderTable(table, headers, rows, options = {}) {
  const bodyRows = rows.map((row, index) => `<tr class="${options.footerLast && index === rows.length - 1 ? "totals-row" : ""}">${row.map((v) => `<td>${v ?? ""}</td>`).join("")}</tr>`).join("");
  table.innerHTML = `
    <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${bodyRows}</tbody>
  `;
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function editStudent(id) {
  const student = state.students.find((s) => String(s.id) === String(id));
  if (!student) {
    toast("Student record was not found");
    return;
  }
  const form = qs("#studentForm");
  closeWorkflow();
  form.classList.remove("collapsed");
  Object.entries(student).forEach(([key, value]) => {
    if (form.elements[key]) form.elements[key].value = value ?? "";
  });
  setSelectedSubjects(student.subjects);
  qs("#formTitle").textContent = "Modify Student";
}

async function deleteStudent(id) {
  if (!confirm("Delete this student record?")) return;
  await api(`/api/students/${id}`, { method: "DELETE" });
  toast("Record deleted");
  await load();
}

function clearStudentForm() {
  qs("#studentForm").reset();
  qs("#studentForm").elements.id.value = "";
  setSelectedSubjects("");
  qs("#formTitle").textContent = "Add Student";
  qs("#studentForm").classList.add("collapsed");
}

function showAddStudentForm() {
  qs("#studentForm").reset();
  qs("#studentForm").elements.id.value = "";
  closeWorkflow();
  setSelectedSubjects("");
  qs("#studentForm").classList.remove("collapsed");
  qs("#formTitle").textContent = "Add Student";
}

function openStudentWorkflow() {
  qs("#studentWorkflow").classList.remove("collapsed");
  qs("#modifySearchArea").classList.add("collapsed");
  qs("#modifySearch").value = "";
  qs("#modifyResults").innerHTML = "";
}

function closeWorkflow() {
  qs("#studentWorkflow").classList.add("collapsed");
}

function showModifySearch() {
  qs("#modifySearchArea").classList.remove("collapsed");
  qs("#modifySearch").focus();
  renderModifyResults();
}

function renderModifyResults() {
  const term = qs("#modifySearch").value.toLowerCase().trim();
  const matches = state.students
    .filter((s) => term.length >= 1 && `${s.student_name} ${s.parent_guardian}`.toLowerCase().includes(term))
    .slice(0, 12);
  qs("#modifyResults").innerHTML = matches.length
    ? matches.map((s) => `<button type="button" class="result-row" data-modify="${s.id}"><strong>${s.student_name}</strong><span>${s.parent_guardian || ""} - ${s.subjects} - ${s.status}</span></button>`).join("")
    : `<p class="muted-note">Type a name to find a student record.</p>`;
  document.querySelectorAll("[data-modify]").forEach((button) => button.addEventListener("click", () => editStudent(button.dataset.modify)));
}

async function saveRate(id) {
  const row = qs(`tr[data-rate="${id}"]`);
  const data = Object.fromEntries([...row.querySelectorAll("input")].map((el) => [el.name, el.value]));
  await api(`/api/rates/${id}`, { method: "PUT", body: JSON.stringify(data) });
  toast("Rate saved");
  await load();
}

async function deleteRate(id) {
  if (!confirm("Delete this rate?")) return;
  await api(`/api/rates/${id}`, { method: "DELETE" });
  toast("Rate deleted");
  await load();
}

async function saveDiscount(id) {
  const row = qs(`tr[data-discount="${id}"]`);
  const controls = [...row.querySelectorAll("input,select")];
  const data = Object.fromEntries(controls.map((el) => [el.name, el.value]));
  await api(`/api/discounts/${id}`, { method: "PUT", body: JSON.stringify(data) });
  toast("Discount code saved");
  await load();
}

async function deleteDiscount(id) {
  if (!confirm("Delete this discount code?")) return;
  await api(`/api/discounts/${id}`, { method: "DELETE" });
  toast("Discount code deleted");
  await load();
}

async function addUser(event) {
  event.preventDefault();
  const data = formData(event.currentTarget);
  await api("/api/users", { method: "POST", body: JSON.stringify(data) });
  event.currentTarget.reset();
  const roleSelect = qs('#userForm [name="role"]');
  if (roleSelect) roleSelect.value = "Office Assistant";
  toast("User access saved");
  await load();
}

async function saveUser(id) {
  const row = qs(`tr[data-user="${id}"]`);
  const controls = [...row.querySelectorAll("input,select")];
  const data = Object.fromEntries(controls.map((el) => [el.name, el.value]));
  await api(`/api/users/${id}`, { method: "PUT", body: JSON.stringify(data) });
  toast("User access updated");
  await load();
}

async function deleteUser(id) {
  if (!confirm("Delete this user access? They will no longer be able to open this centre.")) return;
  await api(`/api/users/${id}`, { method: "DELETE" });
  toast("User access deleted");
  await load();
}

async function restoreSelectedBackup() {
  const name = qs("#backupSelect").value;
  const confirmText = qs("#restoreConfirm").value;
  if (!name) {
    toast("No backup selected");
    return;
  }
  if (confirmText !== "RESTORE") {
    toast("Type RESTORE to confirm recovery");
    return;
  }
  if (!confirm("Restore this backup and replace the current database?")) return;
  await api("/api/restore", { method: "POST", body: JSON.stringify({ name, confirm: confirmText }) });
  toast("Backup restored");
  qs("#restoreConfirm").value = "";
  await load();
}

async function readCsvFile(file) {
  if (!file) return [];
  const text = await file.text();
  return parseCsv(text);
}

async function handlePaymentUpload(event) {
  const file = event.target.files[0];
  if (!file) return;
  state.reconciliationFileName = file.name;
  const rows = paymentCsvRows(await readCsvFile(file));
  if (!rows.length) {
    toast("No payment rows found in this CSV");
    return;
  }
  const result = await api("/api/reconciliation/preview", { method: "POST", body: JSON.stringify({ rows, file_name: file.name }) });
  state.reconciliationPreview = (result.rows || []).map((row) => ({
    ...row,
    selected_student_id: row.best_match?.student_id || "",
    selected_month: row.month_label || "",
    verified: row.best_match?.confidence === "high",
    manually_verified: false,
  }));
  renderReconciliation();
  toast(`${state.reconciliationPreview.length} transaction${state.reconciliationPreview.length === 1 ? "" : "s"} ready for review`);
}

async function handleFeeImportCsv(event) {
  const file = event.target.files[0];
  if (!file) return;
  const rows = await readCsvFile(file);
  if (!rows.length) {
    toast("No fee tracker rows found in this CSV");
    return;
  }
  const result = await api("/api/fee-import/preview", { method: "POST", body: JSON.stringify({ rows }) });
  state.feeImportRows = rows;
  state.feeImportPreview = result.rows || [];
  renderFeeImportPreview();
  const ready = state.feeImportPreview.filter((row) => row.valid).length;
  toast(`${ready} of ${state.feeImportPreview.length} fee rows validated`);
}

async function applyFeeImport() {
  const ready = (state.feeImportPreview || []).filter((row) => row.valid).length;
  if (!ready) {
    toast("No matched fee rows are ready to import");
    return;
  }
  if (!confirm(`Apply monthly payment data for ${ready} validated student row${ready === 1 ? "" : "s"}?`)) return;
  const result = await api("/api/fee-import/apply", { method: "POST", body: JSON.stringify({ rows: state.feeImportRows }) });
  state.feeImportRows = [];
  state.feeImportPreview = [];
  toast(`${result.applied} monthly payment cell${result.applied === 1 ? "" : "s"} imported`);
  await load();
}

function updateReconSelection(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  const select = qs(`[data-recon-student="${index}"]`);
  const month = qs(`[data-recon-month="${index}"]`);
  row.selected_student_id = select?.value || "";
  row.selected_month = month?.value || row.month_label || "";
  row.verified = false;
  row.manually_verified = false;
  renderReconciliation();
}

function verifyReconciliationRow(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  const selectedStudentId = qs(`[data-recon-student="${index}"]`)?.value || row.selected_student_id || row.best_match?.student_id || "";
  const selectedMonth = qs(`[data-recon-month="${index}"]`)?.value || row.selected_month || row.month_label || "";
  if (!selectedStudentId) {
    toast("Select a student before applying");
    return;
  }
  if (!selectedMonth) {
    toast("Select the fee month before verifying");
    return;
  }
  row.selected_student_id = selectedStudentId;
  row.selected_month = selectedMonth;
  row.verified = true;
  row.manually_verified = true;
  renderReconciliation();
  toast("Row verified and ready to add");
}

async function applyReconciliation(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  await postReconciliationRow(row);
  state.reconciliationPreview.splice(index, 1);
  const remaining = state.reconciliationPreview;
  toast("Payment applied and payer alias saved");
  await load();
  state.reconciliationPreview = remaining;
  renderReconciliation();
}

async function postReconciliationRow(row) {
  const selectedStudentId = row.selected_student_id || row.best_match?.student_id || "";
  const selectedMonth = row.selected_month || row.month_label || "";
  const candidate = (row.candidates || []).find((item) => String(item.student_id) === String(selectedStudentId)) || row.best_match || {};
  if (!selectedStudentId || !selectedMonth || !row.verified) {
    throw new Error("Verify each row before adding");
  }
  await api("/api/reconciliation/apply", {
    method: "POST",
    body: JSON.stringify({
      ...row,
      student_id: selectedStudentId,
      month_label: selectedMonth,
      score: candidate.score || 0,
      notes: (candidate.reasons || []).join("; "),
      file_name: state.reconciliationFileName,
    }),
  });
}

async function applyVerifiedRows() {
  const verifiedRows = (state.reconciliationPreview || [])
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => row.verified && (row.selected_student_id || row.best_match?.student_id) && (row.selected_month || row.month_label));
  if (!verifiedRows.length) {
    toast("No verified payment rows are ready");
    return;
  }
  if (!confirm(`Add ${verifiedRows.length} verified payment row${verifiedRows.length === 1 ? "" : "s"} to Fee Tracker?`)) return;
  const remaining = [];
  for (const row of state.reconciliationPreview) {
    if (row.verified) await postReconciliationRow(row);
    else remaining.push(row);
  }
  toast("Verified payments added to Fee Tracker");
  await load();
  state.reconciliationPreview = remaining;
  renderReconciliation();
}

async function handleBatchCsv(event) {
  const file = event.target.files[0];
  if (!file) return;
  const rows = await readCsvFile(file);
  if (!rows.length) {
    toast("No student rows found in this CSV");
    return;
  }
  state.batchImportFileName = file.name;
  state.batchImportRows = rows;
  state.batchImportPreview = buildBatchImportPreview(rows);
  renderBatchImportPreview();
  const valid = state.batchImportPreview.filter((row) => row.valid).length;
  toast(`${valid} of ${state.batchImportPreview.length} student row${state.batchImportPreview.length === 1 ? "" : "s"} validated`);
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    activeAdminArea = "student";
    document.querySelectorAll(".tab, .panel").forEach((node) => node.classList.remove("active"));
    tab.classList.add("active");
    qs(`#${tab.dataset.tab}`).classList.add("active");
    renderAdminAreas();
  });
});

qs("#studentAdminArea")?.addEventListener("click", () => switchAdminArea("student"));
qs("#staffAdminArea")?.addEventListener("click", () => switchAdminArea("staff"));
document.querySelectorAll("[data-staff-view]").forEach((button) => {
  button.addEventListener("click", () => {
    activeStaffView = button.dataset.staffView;
    renderStaffAdministration();
  });
});

qs("#search").addEventListener("input", renderRoster);
qs("#feeSearch").addEventListener("input", renderFeeTracker);
qs("#feeStatusFilter").addEventListener("change", renderFeeTracker);
qs("#feeHistoryMonths").addEventListener("click", () => {
  feeMonthOffset = Math.max(-(state.months.length || 0), feeMonthOffset - FEE_MONTH_WINDOW);
  renderFeeTracker();
});
qs("#feeFutureMonths").addEventListener("click", () => {
  feeMonthOffset = Math.min(state.months.length || 0, feeMonthOffset + FEE_MONTH_WINDOW);
  renderFeeTracker();
});
qs("#feeCurrentMonths").addEventListener("click", () => {
  feeMonthOffset = 0;
  renderFeeTracker();
});
qs("#rosterStatusFilter").addEventListener("change", renderRoster);
qs("#resetForm").addEventListener("click", clearStudentForm);
qs("#openStudentWorkflow").addEventListener("click", openStudentWorkflow);
qs("#closeWorkflow").addEventListener("click", closeWorkflow);
qs("#chooseAdd").addEventListener("click", showAddStudentForm);
qs("#chooseModify").addEventListener("click", showModifySearch);
qs("#modifySearch").addEventListener("input", renderModifyResults);

qs("#dashboardMonth").addEventListener("change", async (event) => {
  state.settings.current_month = event.target.value;
  feeMonthOffset = 0;
  reportSelectedMonths = [event.target.value];
  await api("/api/settings", { method: "POST", body: JSON.stringify({ current_month: event.target.value }) });
  await load();
});

qs("#reportMonths").addEventListener("change", (event) => {
  reportSelectedMonths = [...event.currentTarget.selectedOptions].map((option) => option.value);
  renderReporting();
});

qs("#reportCurrentMonth").addEventListener("click", () => {
  reportSelectedMonths = [state.settings.current_month || "May-26"];
  hydrateMonthSelectors();
  renderReporting();
});

qs("#reportLast13Months").addEventListener("click", () => {
  const currentIndex = currentMonthIndex();
  const start = Math.max(0, currentIndex - 12);
  reportSelectedMonths = state.months.slice(start, currentIndex + 1);
  hydrateMonthSelectors();
  renderReporting();
});

qs("#studentForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formData(event.currentTarget);
  data.subjects = selectedSubjectValues();
  const id = data.id;
  delete data.id;
  await api(id ? `/api/students/${id}` : "/api/students", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
  toast(id ? "Student updated" : "Student added");
  clearStudentForm();
  await load();
});

qs("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  applyInstitutionDefaults();
  await api("/api/settings", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
  toast("Centre setup saved");
  await load();
});

qs("#rateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/api/rates", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
  event.currentTarget.reset();
  toast("Rate added");
  await load();
});

qs("#discountForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/api/discounts", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
  event.currentTarget.reset();
  toast("Discount code added");
  await load();
});

qs("#userForm").addEventListener("submit", addUser);
qs('#settingsForm [name="institution_name"]').addEventListener("change", applyInstitutionDefaults);
qs('#settingsForm [name="institution_name"]').addEventListener("blur", applyInstitutionDefaults);
qs("#restoreBackup").addEventListener("click", restoreSelectedBackup);
qs("#feeImportCsv").addEventListener("change", handleFeeImportCsv);
qs("#applyFeeImport").addEventListener("click", applyFeeImport);
qs("#paymentCsv").addEventListener("change", handlePaymentUpload);
qs("#batchCsv").addEventListener("change", handleBatchCsv);
qs("#applyBatchImport").addEventListener("click", applyBatchImport);
qs("#applyVerifiedRows").addEventListener("click", applyVerifiedRows);
qs("#authForm")?.addEventListener("submit", sendMagicLink);
qs("#googleLogin")?.addEventListener("click", signInWithGoogle);
qs("#signOut")?.addEventListener("click", signOut);
qs("#switchAccount")?.addEventListener("click", switchAccount);
qs("#showLogin")?.addEventListener("click", () => setAuthMode("login"));
qs("#showSignup")?.addEventListener("click", () => setAuthMode("signup"));

qs("#batchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const rows = [...qs("#batchTable tbody").rows].map((tr) => {
    const data = Object.fromEntries([...tr.querySelectorAll("input,select:not([multiple])")].map((el) => [el.name, el.value]));
    const multi = tr.querySelector('select[name="subjects"][multiple]');
    data.subjects = multi ? [...multi.selectedOptions].map((option) => option.value) : "";
    return data;
  });
  const nonEmpty = rows.filter((row) => row.student_name && row.student_name.trim());
  if (!nonEmpty.length) {
    toast("Enter at least one student");
    return;
  }
  if (!confirm(`Permanently save ${nonEmpty.length} student record${nonEmpty.length === 1 ? "" : "s"} to Student Roster?`)) return;
  const result = await api("/api/batch", { method: "POST", body: JSON.stringify({ rows }) });
  toast(`${result.saved} record${result.saved === 1 ? "" : "s"} saved to Student Roster`);
  renderBatch();
  await load();
});

qs("#clearBatch").addEventListener("click", renderBatch);

(async function start() {
  const ready = await initAuth();
  if (ready) await load();
  if (supabaseClient) {
    supabaseClient.auth.onAuthStateChange(async (_event, session) => {
      authSession = session;
      renderAuthState();
      if (session) {
        await load();
      }
    });
  }
})().catch((error) => toast(error.message));
