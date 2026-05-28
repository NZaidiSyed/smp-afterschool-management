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
  renderSubjectChoices();
  renderDashboard();
  renderFeeTracker();
  renderRoster();
  renderSettings();
  renderBatch();
  renderReconciliation();
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

function renderDashboard() {
  const d = state.dashboard;
  const rows = activeRows();
  const currentMonth = state.settings.current_month || "May-26";
  const currentRevenue = rows.reduce((sum, row) => sum + (row.months[currentMonth] || 0), 0);
  const currentUnpaid = rows.filter((row) => isCurrentMonthOverdue(row));
  const currentUnpaidTotal = currentUnpaid.reduce((sum, row) => sum + (row.std_monthly_fee || 0), 0);
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
    `<span class="${isCurrentMonthOverdue(row) ? "overdue-name" : ""}">${row.student_name}</span>`,
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
      `<button class="link-button ${isStudentOverdue(s.id) ? "overdue-name" : ""}" data-profile="${s.id}">${s.student_name}</button>`,
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
    document.querySelectorAll(".tab, .panel").forEach((node) => node.classList.remove("active"));
    tab.classList.add("active");
    qs(`#${tab.dataset.tab}`).classList.add("active");
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
  await api("/api/settings", { method: "POST", body: JSON.stringify({ current_month: event.target.value }) });
  await load();
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
