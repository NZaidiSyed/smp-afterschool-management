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
  reconciliationPreview: [],
  reconciliationFileName: "",
};

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
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || "Request failed");
  return data;
}

async function load() {
  state = await api("/api/bootstrap");
  hydrateMonthSelectors();
  renderAll();
}

function renderAll() {
  renderBrand();
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
            <span>${row.parent_guardian || "No guardian"} · ${subjectText(row.subjects)} · Expected ${money(row.std_monthly_fee)}</span>
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

function renderFeeTracker() {
  const rows = filteredFeeRows();
  const headers = ["#", "Student Name", "Parent / Guardian", "Status", "Enrol Date", "Subjects", "Type", "STD Fee", "Pay Method", "Units", ...state.months, "Total Paid", "Balance"];
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
    ...state.months.map((m) => `<input class="payment-input" data-student="${row.id}" data-month="${m}" value="${row.months[m] || ""}" inputmode="decimal">`),
    money(row.total_paid),
    money(row.balance),
  ]);
  const totals = feeTotals(rows);
  body.push([
    "", "TOTAL", "", "", "", "", "", money(totals.stdFee), "", number(totals.units),
    ...state.months.map((m) => money(totals.months[m])),
    money(totals.totalPaid),
    money(totals.balance),
  ]);
  renderTable(qs("#feeTable"), headers, body, { footerLast: true });
  document.querySelectorAll(".payment-input").forEach((input) => {
    input.addEventListener("change", () => savePayment(input));
  });
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
  document.querySelectorAll("[data-profile]").forEach((button) => button.addEventListener("click", () => showStudentProfile(Number(button.dataset.profile))));
  document.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", () => deleteStudent(Number(button.dataset.delete))));
}

function isStudentOverdue(studentId) {
  const row = state.fee_tracker.find((item) => item.id === studentId);
  return row ? isCurrentMonthOverdue(row) : false;
}

function showStudentProfile(id) {
  const student = state.students.find((s) => s.id === id);
  const fee = state.fee_tracker.find((s) => s.id === id);
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
      <p>Total paid: <strong>${money(fee.total_paid)}</strong> · Balance: <strong>${money(fee.balance)}</strong></p>
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
  document.querySelectorAll("[data-save-rate]").forEach((button) => button.addEventListener("click", () => saveRate(Number(button.dataset.saveRate))));
  document.querySelectorAll("[data-delete-rate]").forEach((button) => button.addEventListener("click", () => deleteRate(Number(button.dataset.deleteRate))));
}

function selectedSubjectValues() {
  return [...document.querySelectorAll('[name="subjects_choice"]:checked')].map((input) => input.value);
}

function setSelectedSubjects(value) {
  renderSubjectChoices(value);
}

function renderBillingAndAccess() {
  const subscription = state.subscriptions[0] || {};
  qs("#subscriptionSummary").innerHTML = [
    `<div class="info-tile"><span>Status</span><strong>${subscription.status || "trialing"}</strong></div>`,
    `<div class="info-tile"><span>Trial Ends</span><strong>${subscription.trial_end || "Not set"}</strong></div>`,
    `<div class="info-tile"><span>Monthly Price</span><strong>$15.99 CAD</strong></div>`,
    `<div class="info-tile"><span>Payments</span><strong>Stripe / Google Pay</strong></div>`,
  ].join("");

  renderTable(
    qs("#usersTable"),
    ["User", "Display Name", "Role", "Provider"],
    state.users.map((u) => [u.email, u.display_name || "", u.role, u.auth_provider])
  );

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

  document.querySelectorAll("[data-save-discount]").forEach((button) => button.addEventListener("click", () => saveDiscount(Number(button.dataset.saveDiscount))));
  document.querySelectorAll("[data-delete-discount]").forEach((button) => button.addEventListener("click", () => deleteDiscount(Number(button.dataset.deleteDiscount))));
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

function normalizeMoney(value) {
  const text = String(value || "").replace(/[$,\s]/g, "");
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fillBatchFromCsv(rows) {
  renderBatch();
  const tableRows = [...qs("#batchTable tbody").rows];
  rows.slice(0, tableRows.length).forEach((row, index) => {
    const tr = tableRows[index];
    const values = {
      student_name: pick(row, ["student name", "student", "name"]),
      parent_guardian: pick(row, ["parent guardian", "parent", "guardian", "payer"]),
      status: pick(row, ["status"]) || "C",
      enrol_date: pick(row, ["enrol date", "enrolment date", "enrollment date", "start date"]),
      rate_type: pick(row, ["rate type", "type"]) || "Regular",
      std_monthly_fee: pick(row, ["std fee", "standard monthly fee", "monthly fee", "fee"]),
      payment_method: pick(row, ["payment method", "pay method", "method"]) || "PAD",
      phone: pick(row, ["phone", "mobile"]),
      email: pick(row, ["email", "e-mail"]),
    };
    Object.entries(values).forEach(([key, value]) => {
      const control = tr.querySelector(`[name="${key}"]`);
      if (control) control.value = value;
    });
    const rawSubjects = pick(row, ["subjects", "subject"]);
    const subjects = subjectList(rawSubjects).map((subject) => subject.toLowerCase());
    const select = tr.querySelector('[name="subjects"]');
    if (select) {
      [...select.options].forEach((option) => {
        option.selected = subjects.includes(option.value.toLowerCase());
      });
    }
  });
  toast(`${Math.min(rows.length, tableRows.length)} CSV student row${rows.length === 1 ? "" : "s"} loaded for review`);
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
    const selectedCandidate = (row.candidates || []).find((candidate) => candidate.student_id === Number(selectedStudentId)) || best;
    const monthValue = row.selected_month || row.month_label || "";
    const buttonClass = row.verified ? "verify-action verified" : "verify-action needs-review";
    const buttonLabel = row.verified ? (row.manually_verified ? "Verified Now to ADD" : "Verified to ADD") : "Verify and Correct";
    return [
      row.date,
      escapeHtml(row.description),
      money(row.amount),
      escapeHtml(row.source || "-"),
      `<select data-recon-student="${index}">
        ${(row.candidates || []).map((candidate) => `<option value="${candidate.student_id}" ${candidate.student_id === Number(selectedStudentId) ? "selected" : ""}>${escapeHtml(candidate.student_name)} - ${escapeHtml(candidate.parent_guardian || "No guardian")} - ${candidate.score}%</option>`).join("")}
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
  const student = state.students.find((s) => s.id === id);
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
    ? matches.map((s) => `<button type="button" class="result-row" data-modify="${s.id}"><strong>${s.student_name}</strong><span>${s.parent_guardian || ""} · ${s.subjects} · ${s.status}</span></button>`).join("")
    : `<p class="muted-note">Type a name to find a student record.</p>`;
  document.querySelectorAll("[data-modify]").forEach((button) => button.addEventListener("click", () => editStudent(Number(button.dataset.modify))));
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

function updateReconSelection(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  const select = qs(`[data-recon-student="${index}"]`);
  const month = qs(`[data-recon-month="${index}"]`);
  row.selected_student_id = Number(select?.value || 0);
  row.selected_month = month?.value || row.month_label || "";
  row.verified = false;
  row.manually_verified = false;
  renderReconciliation();
}

function verifyReconciliationRow(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  const selectedStudentId = Number(qs(`[data-recon-student="${index}"]`)?.value || row.selected_student_id || row.best_match?.student_id || 0);
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
  const selectedStudentId = Number(row.selected_student_id || row.best_match?.student_id || 0);
  const selectedMonth = row.selected_month || row.month_label || "";
  const candidate = (row.candidates || []).find((item) => item.student_id === selectedStudentId) || row.best_match || {};
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
  fillBatchFromCsv(rows);
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
qs("#rosterStatusFilter").addEventListener("change", renderRoster);
qs("#resetForm").addEventListener("click", clearStudentForm);
qs("#openStudentWorkflow").addEventListener("click", openStudentWorkflow);
qs("#closeWorkflow").addEventListener("click", closeWorkflow);
qs("#chooseAdd").addEventListener("click", showAddStudentForm);
qs("#chooseModify").addEventListener("click", showModifySearch);
qs("#modifySearch").addEventListener("input", renderModifyResults);

qs("#dashboardMonth").addEventListener("change", async (event) => {
  state.settings.current_month = event.target.value;
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

qs('#settingsForm [name="institution_name"]').addEventListener("change", applyInstitutionDefaults);
qs('#settingsForm [name="institution_name"]').addEventListener("blur", applyInstitutionDefaults);
qs("#restoreBackup").addEventListener("click", restoreSelectedBackup);
qs("#paymentCsv").addEventListener("change", handlePaymentUpload);
qs("#batchCsv").addEventListener("change", handleBatchCsv);
qs("#applyVerifiedRows").addEventListener("click", applyVerifiedRows);

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

load().catch((error) => toast(error.message));
