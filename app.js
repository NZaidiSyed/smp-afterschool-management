const DEFAULT_RECON_RULES = ["student_name", "parent_name", "payment_amount", "payment_date", "payment_method"];
const RECON_SESSION_KEY = "smp.reconciliation.pending.v1";
const RECON_SESSION_TTL_MS = 30 * 60 * 1000;

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
  audit_logs: [],
  can_access_staff: false,
  staff: { members: [], schedules: [], punches: [], weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"], summary: {} },
  current_user: {},
  role_options: ["Owner", "Admin", "Office Manager", "Office Assistant", "Staff"],
  reconciliationPreview: [],
  reconciliationSummary: null,
  reconciliationFileName: "",
  reconciliationPaymentMethod: "PAD",
  reconciliationMatchRules: [...DEFAULT_RECON_RULES],
  reconciliationSkippedZeroRows: 0,
  feeImportRows: [],
  feeImportPreview: [],
  batchImportRows: [],
  batchImportPreview: [],
  batchImportFileName: "",
  rosterSortField: null,
  rosterSortOrder: "asc",
};

let appConfig = { auth_required: false };
let supabaseClient = null;
let authSession = null;
let supabasePersistenceMode = null;
let feeMonthOffset = 0;
let reportSelectedMonths = [];
let activeAdminArea = "choice";
let activeStaffView = "staff-dashboard";
let reconSearchRenderTimer = null;
const FEE_PAST_MONTHS = 1;
const FEE_FUTURE_MONTHS = 2;
const FEE_MONTH_WINDOW = FEE_PAST_MONTHS + 1 + FEE_FUTURE_MONTHS;
const PRESENCE_WEEKDAYS = ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const RECON_RULES = [
  ["student_id", "Student ID"],
  ["student_name", "Student Name"],
  ["parent_name", "Parent Name"],
  ["email", "Email Address"],
  ["payment_amount", "Payment Amount"],
  ["payment_date", "Payment Date"],
  ["payment_method", "Payment Method"],
  ["organization_id", "Organization ID"],
  ["branch_id", "Branch ID"],
];

const money = (value) => new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 }).format(value || 0);
const number = (value) => new Intl.NumberFormat("en-CA", { maximumFractionDigits: 2 }).format(value || 0);
const qs = (selector, root = document) => root.querySelector(selector);

function saveReconciliationSession() {
  if (!state.reconciliationPreview?.length) {
    localStorage.removeItem(RECON_SESSION_KEY);
    return;
  }
  localStorage.setItem(RECON_SESSION_KEY, JSON.stringify({
    saved_at: Date.now(),
    file_name: state.reconciliationFileName,
    payment_method: state.reconciliationPaymentMethod,
    match_rules: state.reconciliationMatchRules,
    skipped_zero_rows: state.reconciliationSkippedZeroRows || 0,
    summary: state.reconciliationSummary,
    rows: state.reconciliationPreview,
  }));
}

function restoreReconciliationSession() {
  try {
    const saved = JSON.parse(localStorage.getItem(RECON_SESSION_KEY) || "null");
    if (!saved?.rows?.length) return;
    if (Date.now() - Number(saved.saved_at || 0) > RECON_SESSION_TTL_MS) {
      localStorage.removeItem(RECON_SESSION_KEY);
      return;
    }
    state.reconciliationFileName = saved.file_name || state.reconciliationFileName;
    state.reconciliationPaymentMethod = saved.payment_method || state.reconciliationPaymentMethod;
    state.reconciliationMatchRules = saved.match_rules?.length ? saved.match_rules : state.reconciliationMatchRules;
    state.reconciliationSkippedZeroRows = Number(saved.skipped_zero_rows || 0);
    state.reconciliationSummary = saved.summary || state.reconciliationSummary;
    state.reconciliationPreview = saved.rows || [];
  } catch {
    localStorage.removeItem(RECON_SESSION_KEY);
  }
}

function clearReconciliationSession() {
  state.reconciliationPreview = [];
  state.reconciliationSummary = null;
  state.reconciliationSkippedZeroRows = 0;
  localStorage.removeItem(RECON_SESSION_KEY);
}

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
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new Error(`Server returned an invalid response for ${path}`);
  }
  if (!res.ok || data.ok === false) throw new Error(data.error || "Request failed");
  return data;
}

async function initAuth() {
  const res = await fetch("/api/config", { headers: { "Content-Type": "application/json" } });
  const text = await res.text();
  try {
    appConfig = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new Error("Server configuration could not be loaded. Please restart the SMP server.");
  }
  if (!appConfig.auth_required) {
    renderAuthState();
    return true;
  }
  
  // Check for saved mock session first
  const savedMock = window.localStorage.getItem("smp_mock_session") || window.sessionStorage.getItem("smp_mock_session");
  if (savedMock) {
    try {
      authSession = JSON.parse(savedMock);
      renderAuthState();
      return true;
    } catch (e) {
      window.localStorage.removeItem("smp_mock_session");
      window.sessionStorage.removeItem("smp_mock_session");
    }
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

function canDeleteStudentRecords() {
  const r = String(currentUserRole() || "").toLowerCase();
  return r === "admin" || r === "owner";
}

function paymentMethodLabel(value) {
  const method = String(value || "").trim();
  if (!method) return "Unspecified";
  const normalized = method.replace(/[\s_-]+/g, "").toLowerCase();
  const labels = {
    etransfer: "E-Transfer",
    pad: "PAD",
    cash: "Cash",
    creditcard: "Credit Card",
    cheque: "Cheque",
  };
  return labels[normalized] || method;
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
  window.localStorage.removeItem("smp_mock_session");
  window.sessionStorage.removeItem("smp_mock_session");
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

async function signInWithPassword() {
  const email = qs("#loginEmail").value.trim().toLowerCase();
  const password = qs("#loginPassword").value;
  if (!email || !password) {
    qs("#authMessage").textContent = "Please enter both email and password.";
    return;
  }
  
  qs("#authMessage").textContent = "Signing in...";
  
  try {
    const res = await fetch("/api/staffbase/auth/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();
    if (data.ok) {
      authSession = {
        access_token: "mock-token-" + email,
        user: {
          email: email,
          user_metadata: {
            full_name: data.user.name,
            name: data.user.name
          }
        }
      };
      
      const keepSignedIn = Boolean(qs("#keepSignedIn")?.checked);
      const storage = keepSignedIn ? window.localStorage : window.sessionStorage;
      storage.setItem("smp_mock_session", JSON.stringify(authSession));
      
      qs("#authMessage").textContent = "";
      renderAuthState();
      
      await load();
      renderAll();
    } else {
      qs("#authMessage").textContent = data.error || "Login failed. Incorrect credentials.";
    }
  } catch (err) {
    qs("#authMessage").textContent = "Connection error. Please try again.";
    console.error(err);
  }
}

async function load() {
  state = await api("/api/bootstrap");
  
  const role = String(currentUserRole() || "").toLowerCase();
  if (role === "staff") {
    window.location.href = "/staffbase.html";
    return;
  }
  
  state.expenses = state.expenses || [];
  // Write EL student cache so staffbase.html can auto-populate without needing
  // the Presence tab to have been visited first.
  try {
    const elCache = (state.students || [])
      .filter(s => !s.deleted_at)
      .map(s => ({
        student_name: s.student_name,
        rate_type: s.rate_type || 'R',
        status: s.status,
        schedules: (s.schedules || []).map(sc => ({
          weekday: sc.weekday,
          start: sc.start_time,
          end: sc.end_time,
        })),
      }));
    localStorage.setItem('smp_el_cache', JSON.stringify(elCache));
  } catch(e) {}
  if (!state.activeSettingsSubTab) {
    state.activeSettingsSubTab = 'centre';
  }
  if (!state.activePlMonth) {
    state.activePlMonth = state.settings.current_month || "May-26";
  }
  if (!state.activePresenceSubTab) {
    const todayName = new Date().toLocaleDateString('en-US', { weekday: 'long' });
    const validDays = ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    state.activePresenceSubTab = validDays.includes(todayName) ? todayName : "Tuesday";
  }

  // Bootstrap state.staff from staffbase localStorage data so the staff views
  // in index.html reflect real data even before the iframe fires any messages.
  // Server data (if present) takes priority over localStorage.
  if (!state.staff) state.staff = { members: [], schedules: [], punches: [], weekdays: [] };
  try {
    const sbUsers = JSON.parse(localStorage.getItem('sb_users') || 'null');
    if (sbUsers && !state.staff.members?.length) {
      state.staff.members = sbUsersToStaffMembers(sbUsers);
    }
    const sbSchedule = JSON.parse(localStorage.getItem('sb_schedule') || 'null');
    if (sbSchedule && !state.staff.schedules?.length) {
      state.staff.schedules = sbScheduleToStaffSchedules(sbSchedule);
      if (sbSchedule.openDays?.length) state.staff.weekdays = sbSchedule.openDays;
    }
    const sbClock = JSON.parse(localStorage.getItem('sb_clock_data') || 'null');
    if (sbClock) {
      state.staff.punches = sbClockToStaffPunches(sbClock);
    }
  } catch (e) { /* localStorage may be unavailable */ }

  restoreReconciliationSession();
  hydrateMonthSelectors();
  renderAll();
}

function applyRoleTabVisibility() {
  const role = String(currentUserRole() || "").toLowerCase();
  const isStaff = role === "staff";
  
  const reconTab = document.querySelector('.tab[data-tab="reconciliation"]');
  if (reconTab) reconTab.style.display = isStaff ? "none" : "inline-flex";

  const batchTab = document.querySelector('.tab[data-tab="batch"]');
  if (batchTab) batchTab.style.display = isStaff ? "none" : "inline-flex";

  const settingsTab = document.querySelector('.tab[data-tab="settings"]');
  if (settingsTab) settingsTab.style.display = isStaff ? "none" : "inline-flex";

  const plTab = document.querySelector('.tab[data-tab="pl"]');
  if (plTab) {
    const hasPlAccess = ["admin", "office manager", "office_manager", "owner"].includes(role);
    plTab.style.display = hasPlAccess ? "inline-flex" : "none";
  }

  // Redirect if currently on a hidden tab
  const activeTab = document.querySelector('.tab.active');
  if (activeTab && activeTab.style.display === "none") {
    document.querySelectorAll(".tab, .panel").forEach((node) => node.classList.remove("active"));
    const dashTab = document.querySelector('.tab[data-tab="dashboard"]');
    if (dashTab) dashTab.classList.add("active");
    const dashPanel = document.querySelector('#dashboard');
    if (dashPanel) dashPanel.classList.add("active");
  }
}

function renderAll() {
  try {
    saveTeacherAssignmentsToLocalStorage();
  } catch (e) {
    console.error("Error auto-calculating teacher assignments:", e);
  }
  const isSetupCompleted = String(state.settings?.center_setup_completed || "0") === "1";
  if (!isSetupCompleted && activeAdminArea !== "choice") {
    activeAdminArea = "student";
    document.querySelectorAll(".tab, .panel").forEach((node) => node.classList.remove("active"));
    qs("#tabCentreSetup")?.classList.add("active");
    qs("#centre-setup")?.classList.add("active");
  }
  renderBrand();
  renderAuthState();
  applyRoleTabVisibility();
  const recCount = qs("#recordCount");
  if (recCount) recCount.textContent = state.students.length;
  renderAdminAreas();
  renderSubjectChoices();
  renderDashboard();
  renderFeeTracker();
  renderRoster();
  renderPresence();
  renderReporting();
  renderPlTab();
  renderSettings();
  renderCentreSetup();
  renderBatch();
  renderReconciliationRules();
  renderReconciliation();
  renderStaffAdministration();
}

function hasStaffAccess() {
  const role = String(state.current_user?.role || "").toLowerCase();
  return Boolean(state.can_access_staff || ["admin", "administrator", "owner", "principal_owner", "office manager", "office_manager", "staff", "office assistant", "office_assistant"].includes(role));
}

function renderAdminAreas() {
  const canOpenStaff = hasStaffAccess();
  
  if (!canOpenStaff && (activeAdminArea === "staff" || activeAdminArea === "choice")) {
    activeAdminArea = "student";
  }
  
  document.body.dataset.adminArea = activeAdminArea;
  
  const welcomeHeading = qs("#adminChoicePanel h2");
  if (welcomeHeading) {
    const name = currentUserName() || "User";
    const inst = state.settings.institution_name || "Kumon Cityscape Square";
    welcomeHeading.textContent = `Welcome ${name} to "${inst}" School Management Programme`;
  }
  
  const choicePanel = qs("#adminChoicePanel");
  if (choicePanel) {
    choicePanel.classList.toggle("collapsed", activeAdminArea !== "choice");
  }
  
  const choiceStaffBtn = qs("#choiceStaffAdmin");
  if (choiceStaffBtn) {
    choiceStaffBtn.style.display = canOpenStaff ? "flex" : "none";
  }
  
  qs("#studentTabs")?.classList.toggle("collapsed", activeAdminArea !== "student");
  
  const staffPanel = qs("#staffAdministration");
  if (staffPanel) {
    staffPanel.classList.toggle("active", activeAdminArea === "staff");
    staffPanel.classList.toggle("collapsed", activeAdminArea !== "staff");
  }
  
  document.querySelectorAll("main > section.panel:not(#staffAdministration)").forEach((panel) => {
    panel.classList.toggle("area-hidden", activeAdminArea !== "student");
  });
  
  const switchModuleBtn = qs("#switchModule");
  if (switchModuleBtn) {
    const shouldShowSwitch = canOpenStaff && (activeAdminArea === "student" || activeAdminArea === "staff");
    switchModuleBtn.style.display = shouldShowSwitch ? "inline-flex" : "none";
  }
  renderStaffAdministration();
}

function switchAdminArea(area) {
  if (area === "staff" && !hasStaffAccess()) {
    toast("Staff Administration is limited to Admin, Manager, and Staff");
    return;
  }
  const isSetupCompleted = String(state.settings?.center_setup_completed || "0") === "1";
  if (!isSetupCompleted && (area === "student" || area === "staff")) {
    activeAdminArea = "student";
    document.querySelectorAll(".tab, .panel").forEach((node) => node.classList.remove("active"));
    qs("#tabCentreSetup")?.classList.add("active");
    qs("#centre-setup")?.classList.add("active");
    renderAdminAreas();
    toast("Please complete the One-Time Centre Setup first.");
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
  const orgName = settings.institution_name || "SMP Kumon";
  const branchName = settings.branch_name || "Calgary NE";
  const branchCode = settings.branch_code;
  
  // Combine Organization Name and Branch Name
  const headingText = branchName ? `${orgName} - ${branchName}` : orgName;
  qs("#institutionHeading").textContent = headingText;
  
  // Append branch code to description/subtitle if it exists
  const codeText = branchCode ? `Branch Code: ${branchCode} | ` : "";
  qs("#institutionDetails").textContent = codeText + (settings.institution_details || "Student roster, fee tracking, and monthly collection dashboard.");
  
  qs("#institutionPhone").textContent = settings.institution_phone ? `Phone: ${settings.institution_phone}` : "";
  qs("#institutionPhone").style.display = settings.institution_phone ? "block" : "none";
  document.title = `SMP - ${headingText}`;
}

function hydrateMonthSelectors() {
  const options = (state.months || []).map((m) => `<option value="${m}">${m}</option>`).join("");
  for (const selector of ["#dashboardMonth", "#settingsMonth"]) {
    const node = qs(selector);
    if (node && node.options.length !== (state.months || []).length) node.innerHTML = options;
    if (node) node.value = state.settings.current_month || "May-26";
  }
  const reportMonths = qs("#reportMonths");
  if (reportMonths && reportMonths.options.length !== (state.months || []).length) {
    reportMonths.innerHTML = options;
  }
  if (!reportSelectedMonths.length) reportSelectedMonths = [state.settings.current_month || "May-26"];
  if (reportMonths) {
    [...reportMonths.options].forEach((option) => {
      option.selected = reportSelectedMonths.includes(option.value);
    });
  }
  const plMonth = qs("#plMonth");
  if (plMonth && plMonth.options.length !== (state.months || []).length) {
    plMonth.innerHTML = options;
  }
  if (plMonth) {
    plMonth.value = state.activePlMonth || state.settings.current_month || "May-26";
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

function scheduleText(student) {
  return student.weekly_schedule || (student.schedules || [])
    .map((item) => `${String(item.weekday || "").slice(0, 3)} ${item.start_time}-${item.end_time}`)
    .join("; ") || "-";
}

function timeToMinutes(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function minutesToTimeLabel(minutes) {
  if (minutes == null) return "-";
  const hour24 = Math.floor(minutes / 60);
  const minute = minutes % 60;
  const suffix = hour24 >= 12 ? "PM" : "AM";
  const hour12 = hour24 % 12 || 12;
  return `${hour12}:${String(minute).padStart(2, "0")} ${suffix}`;
}

function presenceEntries() {
  const weekdayRank = Object.fromEntries(PRESENCE_WEEKDAYS.map((day, index) => [day, index]));
  return (state.students || [])
    .filter((student) => !student.deleted_at && String(student.status || "").toUpperCase() === "C")
    .flatMap((student) => (student.schedules || []).map((schedule) => {
      const startMinutes = timeToMinutes(schedule.start_time);
      const endMinutes = timeToMinutes(schedule.end_time);
      return {
        student_id: student.id,
        student_name: student.student_name || "-",
        weekday: schedule.weekday,
        start_time: schedule.start_time,
        end_time: schedule.end_time,
        startMinutes,
        endMinutes,
        rate_type: student.rate_type || "R",
      };
    }))
    .filter((entry) => PRESENCE_WEEKDAYS.includes(entry.weekday) && entry.startMinutes != null && entry.endMinutes != null && entry.startMinutes < entry.endMinutes)
    .sort((a, b) => (weekdayRank[a.weekday] - weekdayRank[b.weekday]) || a.startMinutes - b.startMinutes || a.student_name.localeCompare(b.student_name));
}

function saveTeacherAssignmentsToLocalStorage() {
  const allEntries = presenceEntries();
  const TIME_SLOTS = [
    { label: "3:00 - 3:30", start: 15 * 60, end: 15 * 60 + 30 },
    { label: "3:30 - 4:00", start: 15 * 60 + 30, end: 16 * 60 },
    { label: "4:00 - 4:30", start: 16 * 60, end: 16 * 60 + 30 },
    { label: "4:30 - 5:00", start: 16 * 60 + 30, end: 17 * 60 },
    { label: "5:00 - 5:30", start: 17 * 60, end: 17 * 60 + 30 },
    { label: "5:30 - 6:00", start: 17 * 60 + 30, end: 18 * 60 },
    { label: "6:00 - 6:30", start: 18 * 60, end: 18 * 60 + 30 },
    { label: "6:30 - 7:00", start: 18 * 60 + 30, end: 19 * 60 },
    { label: "7:00 - 7:30", start: 19 * 60, end: 19 * 60 + 30 }
  ];

  const results = [];

  PRESENCE_WEEKDAYS.forEach(day => {
    const tableEntries = allEntries.filter((entry) => entry.weekday === day);
    const assignments = Array.from({ length: TIME_SLOTS.length }, () => ({}));

    for (let s = 0; s < TIME_SLOTS.length; s++) {
      const slot = TIME_SLOTS[s];
      const elStudentsInSlot = tableEntries.filter(entry => {
        const isEL = String(entry.rate_type || "").toUpperCase() === "EL";
        return isEL && entry.startMinutes < slot.end && entry.endMinutes > slot.start;
      });

      elStudentsInSlot.sort((a, b) => a.student_name.localeCompare(b.student_name));
      const N = elStudentsInSlot.length;
      if (N === 0) continue;

      const assignedTeachersInSlot = new Set();
      const studentsToPair = [...elStudentsInSlot];

      if (N % 2 !== 0) {
        let leftoverEntry = null;
        if (s > 0) {
          for (const entry of studentsToPair) {
            const prevTeacher = assignments[s - 1][entry.student_id];
            if (prevTeacher) {
              leftoverEntry = entry;
              break;
            }
          }
        }
        if (!leftoverEntry) {
          leftoverEntry = studentsToPair[0];
        }

        let teacherNum;
        if (s > 0 && assignments[s - 1][leftoverEntry.student_id]) {
          teacherNum = assignments[s - 1][leftoverEntry.student_id];
        } else {
          let t = 1;
          while (assignedTeachersInSlot.has(t)) {
            t++;
          }
          teacherNum = t;
        }

        assignments[s][leftoverEntry.student_id] = teacherNum;
        assignedTeachersInSlot.add(teacherNum);

        const idx = studentsToPair.indexOf(leftoverEntry);
        if (idx > -1) {
          studentsToPair.splice(idx, 1);
        }
      }

      for (let i = 0; i < studentsToPair.length; i += 2) {
        const entry1 = studentsToPair[i];
        const entry2 = studentsToPair[i + 1];
        let t = 1;
        while (assignedTeachersInSlot.has(t)) {
          t++;
        }
        assignments[s][entry1.student_id] = t;
        assignments[s][entry2.student_id] = t;
        assignedTeachersInSlot.add(t);
      }

      elStudentsInSlot.forEach(entry => {
        const teacherNum = assignments[s][entry.student_id] || 1;
        results.push({
          weekday: day,
          slotIdx: s,
          teacherName: "Teacher " + teacherNum,
          studentName: entry.student_name
        });
      });
    }
  });

  localStorage.setItem('sb_teacher_assignments', JSON.stringify(results));
}

function presenceBlocks(entries) {
  const startStr = state.settings?.operating_start || "15:00";
  const endStr = state.settings?.operating_end || "20:00";
  let minStart = timeToMinutes(startStr);
  let maxEnd = timeToMinutes(endStr);
  if (minStart == null) minStart = 15 * 60; // 3:00 PM
  if (maxEnd == null) maxEnd = 20 * 60; // 8:00 PM
  if (minStart >= maxEnd) {
    minStart = 15 * 60;
    maxEnd = 20 * 60;
  }
  const blocks = [];
  for (let minutes = minStart; minutes < maxEnd; minutes += 30) {
    blocks.push(minutes);
  }
  return blocks;
}

function renderPresence() {
  if (!qs("#presenceTable")) return;
  saveTeacherAssignmentsToLocalStorage();
  const selectedDay = qs("#presenceDayFilter")?.value || "all";
  const allEntries = presenceEntries();
  const visibleEntries = selectedDay === "all" ? allEntries : allEntries.filter((entry) => entry.weekday === selectedDay);
  const activeScheduledStudents = new Set(allEntries.map((entry) => String(entry.student_id)));
  const dayCounts = PRESENCE_WEEKDAYS.map((day) => {
    const dayEntries = allEntries.filter((entry) => entry.weekday === day);
    const distinctStudents = new Set(dayEntries.map((entry) => String(entry.student_id))).size;
    return {
      day,
      count: dayEntries.length,
      studentsCount: distinctStudents,
    };
  });
  const blocks = presenceBlocks(allEntries);
  const daySeries = PRESENCE_WEEKDAYS.map((day) => ({
    day,
    counts: blocks.map((block) => allEntries.filter((entry) => entry.weekday === day && entry.startMinutes <= block && entry.endMinutes > block).length),
  }));
  const maxCount = Math.max(1, ...daySeries.flatMap((series) => series.counts));
  const peak = daySeries.flatMap((series) => series.counts.map((count, index) => ({ day: series.day, count, block: blocks[index] })))
    .sort((a, b) => b.count - a.count)[0] || { day: "-", count: 0, block: null };
  const quietest = [...dayCounts].sort((a, b) => a.count - b.count)[0] || { day: "-", count: 0, studentsCount: 0 };

  qs("#presenceMetrics").innerHTML = [
    metric("Scheduled Students", activeScheduledStudents.size, "accent"),
    metric("Weekly Visits", `${allEntries.length} (${activeScheduledStudents.size} students)`, "success"),
    metric("Peak Time", `${peak.day} ${minutesToTimeLabel(peak.block)}`, peak.count ? "warning" : ""),
    metric("Peak Students", peak.count, peak.count ? "warning" : ""),
    metric("Quietest Day", quietest.day, ""),
    metric("Quietest Visits", `${quietest.count} (${quietest.studentsCount} students)`, ""),
  ].join("");

  const chartDays = selectedDay === "all" ? daySeries : daySeries.filter((series) => series.day === selectedDay);
  qs("#presenceChart").innerHTML = allEntries.length ? chartDays.map((series) => {
    const width = 640;
    const height = 170;
    const left = 42;
    const right = 16;
    const top = 14;
    const bottom = 34;
    const usableWidth = width - left - right;
    const usableHeight = height - top - bottom;
    const points = series.counts.map((count, index) => {
      const x = left + (blocks.length === 1 ? usableWidth / 2 : (index / (blocks.length - 1)) * usableWidth);
      const y = top + usableHeight - (count / maxCount) * usableHeight;
      return `${x},${y}`;
    }).join(" ");
    const labels = blocks.map((block, index) => {
      const x = left + (blocks.length === 1 ? usableWidth / 2 : (index / (blocks.length - 1)) * usableWidth);
      return index % 2 === 0 ? `<text x="${x}" y="${height - 10}" text-anchor="middle">${minutesToTimeLabel(block).replace(":00 ", " ")}</text>` : "";
    }).join("");
    const dots = series.counts.map((count, index) => {
      const x = left + (blocks.length === 1 ? usableWidth / 2 : (index / (blocks.length - 1)) * usableWidth);
      const y = top + usableHeight - (count / maxCount) * usableHeight;
      return `<circle cx="${x}" cy="${y}" r="4"><title>${series.day} ${minutesToTimeLabel(blocks[index])}: ${count} student${count === 1 ? "" : "s"}</title></circle>`;
    }).join("");
    return `
      <div class="presence-chart-card">
        <div class="presence-chart-title"><strong>${series.day}</strong><span>${series.counts.reduce((sum, count) => Math.max(sum, count), 0)} peak students</span></div>
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${series.day} scheduled student traffic">
          <line x1="${left}" y1="${top + usableHeight}" x2="${width - right}" y2="${top + usableHeight}" class="axis"></line>
          <line x1="${left}" y1="${top}" x2="${left}" y2="${top + usableHeight}" class="axis"></line>
          <text x="10" y="${top + 5}" class="axis-label">${maxCount}</text>
          <text x="10" y="${top + usableHeight}" class="axis-label">0</text>
          <polyline points="${points}" class="presence-line"></polyline>
          ${dots}
          ${labels}
        </svg>
      </div>
    `;
  }).join("") : `<div class="empty-state">No active student schedules have been entered yet.</div>`;

  const activeSubTab = state.activePresenceSubTab || "Tuesday";
  document.querySelectorAll("#presenceSubtabs .subtab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.day === activeSubTab);
  });
  const tableEntries = allEntries.filter((entry) => entry.weekday === activeSubTab);

  const TIME_SLOTS = [
    { label: "3:00 - 3:30", start: 15 * 60, end: 15 * 60 + 30 },
    { label: "3:30 - 4:00", start: 15 * 60 + 30, end: 16 * 60 },
    { label: "4:00 - 4:30", start: 16 * 60, end: 16 * 60 + 30 },
    { label: "4:30 - 5:00", start: 16 * 60 + 30, end: 17 * 60 },
    { label: "5:00 - 5:30", start: 17 * 60, end: 17 * 60 + 30 },
    { label: "5:30 - 6:00", start: 17 * 60 + 30, end: 18 * 60 },
    { label: "6:00 - 6:30", start: 18 * 60, end: 18 * 60 + 30 },
    { label: "6:30 - 7:00", start: 18 * 60 + 30, end: 19 * 60 },
    { label: "7:00 - 7:30", start: 19 * 60, end: 19 * 60 + 30 }
  ];

  // 1. Run dynamic teacher assignments slot-by-slot chronologically
  const assignments = Array.from({ length: TIME_SLOTS.length }, () => ({}));
  for (let s = 0; s < TIME_SLOTS.length; s++) {
    const slot = TIME_SLOTS[s];
    const elStudentsInSlot = tableEntries.filter(entry => {
      const isEL = String(entry.rate_type || "").toUpperCase() === "EL";
      return isEL && entry.startMinutes < slot.end && entry.endMinutes > slot.start;
    });

    // Stable sort alphabetically
    elStudentsInSlot.sort((a, b) => a.student_name.localeCompare(b.student_name));
    
    const N = elStudentsInSlot.length;
    if (N === 0) continue;

    const assignedTeachersInSlot = new Set();
    const studentsToPair = [...elStudentsInSlot];

    if (N % 2 !== 0) {
      let leftoverEntry = null;
      if (s > 0) {
        for (const entry of studentsToPair) {
          const prevTeacher = assignments[s - 1][entry.student_id];
          if (prevTeacher) {
            leftoverEntry = entry;
            break;
          }
        }
      }

      if (!leftoverEntry) {
        leftoverEntry = studentsToPair[0];
      }

      let teacherNum;
      if (s > 0 && assignments[s - 1][leftoverEntry.student_id]) {
        teacherNum = assignments[s - 1][leftoverEntry.student_id];
      } else {
        let t = 1;
        while (assignedTeachersInSlot.has(t)) {
          t++;
        }
        teacherNum = t;
      }

      assignments[s][leftoverEntry.student_id] = teacherNum;
      assignedTeachersInSlot.add(teacherNum);

      const idx = studentsToPair.indexOf(leftoverEntry);
      if (idx > -1) {
        studentsToPair.splice(idx, 1);
      }
    }

    for (let i = 0; i < studentsToPair.length; i += 2) {
      const entry1 = studentsToPair[i];
      const entry2 = studentsToPair[i + 1];

      let t = 1;
      while (assignedTeachersInSlot.has(t)) {
        t++;
      }

      assignments[s][entry1.student_id] = t;
      assignments[s][entry2.student_id] = t;
      assignedTeachersInSlot.add(t);
    }
  }

  // 2. Render daily presence timetable grid
  const table = qs("#presenceTable");
  if (table) {
    table.className = "presence-timetable";
    
    let theadHtml = `
      <thead>
        <tr>
          <th>Student Name</th>
          <th>Rate Type</th>
          ${TIME_SLOTS.map(slot => `<th>${slot.label}</th>`).join("")}
        </tr>
      </thead>
    `;

    const studentRows = {};
    tableEntries.forEach(entry => {
      if (!studentRows[entry.student_id]) {
        studentRows[entry.student_id] = {
          student_name: entry.student_name,
          rate_type: entry.rate_type || "R",
          student_id: entry.student_id,
          scheduleItems: []
        };
      }
      studentRows[entry.student_id].scheduleItems.push(entry);
    });

        const getMinStart = (student) => Math.min(...student.scheduleItems.map(item => item.startMinutes));
    const sortedStudents = Object.values(studentRows).sort((a, b) => {
      return getMinStart(a) - getMinStart(b) || a.student_name.localeCompare(b.student_name);
    });
    let tbodyHtml = "<tbody>";
    
    if (sortedStudents.length === 0) {
      tbodyHtml += `<tr><td colspan="11" class="empty-state" style="text-align: center;">No students scheduled for today.</td></tr>`;
    } else {
      sortedStudents.forEach(student => {
        const isEL = String(student.rate_type || "").toUpperCase() === "EL";
        const catLabel = isEL ? "EL" : "R";
        const catClass = isEL ? "is-el" : "is-r";

        let rowHtml = `
          <tr>
            <td>${escapeHtml(student.student_name)}</td>
            <td><span class="timetable-badge ${catClass}">${catLabel}</span></td>
        `;

        TIME_SLOTS.forEach((slot, sIdx) => {
          const present = student.scheduleItems.some(item => item.startMinutes < slot.end && item.endMinutes > slot.start);
          if (present) {
            if (isEL) {
              const teacher = assignments[sIdx][student.student_id] || 1;
              rowHtml += `<td><div class="timetable-bar is-el" title="Teacher ${teacher}">Teacher ${teacher}</div></td>`;
            } else {
              rowHtml += `<td><div class="timetable-bar is-r"></div></td>`;
            }
          } else {
            rowHtml += `<td></td>`;
          }
        });

        rowHtml += "</tr>";
        tbodyHtml += rowHtml;
      });
    }

    tbodyHtml += "</tbody>";
    table.innerHTML = theadHtml + tbodyHtml;
  }

  const maxDayCount = Math.max(1, ...dayCounts.map((item) => item.count));
  qs("#presenceDayLoad").innerHTML = dayCounts.map((item) => `
    <div class="presence-load-row">
      <div><strong>${item.day}</strong><span>${item.count} scheduled visit${item.count === 1 ? "" : "s"} (${item.studentsCount} student${item.studentsCount === 1 ? "" : "s"})</span></div>
      <div class="presence-load-track"><span style="width:${Math.max(4, Math.round((item.count / maxDayCount) * 100))}%"></span></div>
    </div>
  `).join("");
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
  return normalizedStudentName(row.student_name);
}

function activeDuplicateNameMap() {
  const counts = {};
  for (const row of state.students || []) {
    if (row.deleted_at) continue;
    if (String(row.status || "").toUpperCase() !== "C") continue;
    const key = normalizedDuplicateKey(row);
    if (!normalizedStudentName(row.student_name)) continue;
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function isActiveDuplicateName(row) {
  if (row.deleted_at) return false;
  if (String(row.status || "").toUpperCase() !== "C") return false;
  return (activeDuplicateNameMap()[normalizedDuplicateKey(row)] || 0) > 1;
}

function renderDashboard() {
  const d = state.dashboard;
  const rows = activeRows();
  const currentMonth = state.settings.current_month || "May-26";
  const currentRevenue = rows.reduce((sum, row) => sum + (row.months[currentMonth] || 0), 0);
  const currentUnpaid = rows.filter((row) => isCurrentMonthOverdue(row));
  const currentUnpaidTotal = currentUnpaid.reduce((sum, row) => sum + (row.balance || row.std_monthly_fee || 0), 0);
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

  const enrolmentTotals = d.enrolment_totals || [];
  const enrolmentMax = Math.max(...enrolmentTotals.map((m) => m.count), 1);
  qs("#enrolmentChart").innerHTML = enrolmentTotals.map((m) => {
    const h = Math.max(8, Math.round((m.count / enrolmentMax) * 170));
    return `<div class="bar enrolment-bar" style="height:${h}px" title="${m.month}: ${m.count} enrolments"><strong>${m.count}</strong><span>${m.month}</span></div>`;
  }).join("");

  const monthlyTotals = d.monthly_totals || [];
  const max = Math.max(...monthlyTotals.map((m) => m.total), 1);
  qs("#barChart").innerHTML = monthlyTotals.map((m) => {
    const h = Math.max(8, Math.round((m.total / max) * 170));
    return `<div class="bar" style="height:${h}px" title="${m.month}: ${money(m.total)}"><strong>${money(m.total)}</strong><span>${m.month}</span></div>`;
  }).join("");

  const byMethod = {};
  for (const row of rows) {
    const method = paymentMethodLabel(row.payment_method);
    if (!byMethod[method]) {
      byMethod[method] = { studentCount: 0, expectedRevenue: 0, collectedRevenue: 0, outstandingBalance: 0 };
    }
    byMethod[method].studentCount += 1;
    byMethod[method].expectedRevenue += row.std_monthly_fee || 0;
    byMethod[method].collectedRevenue += row.months[currentMonth] || 0;
    byMethod[method].outstandingBalance += row.balance || 0;
  }
  const entries = Object.entries(byMethod).sort((a, b) => b[1].expectedRevenue - a[1].expectedRevenue);
  qs("#paymentMix").innerHTML = entries.map(([method, value]) => {
    const collectionPct = value.expectedRevenue > 0 ? Math.min(100, Math.round((value.collectedRevenue / value.expectedRevenue) * 100)) : 0;
    return `
    <div class="mix-item payment-mix-detail">
      <div class="mix-heading">
        <strong>${escapeHtml(method)}</strong>
        <span>${number(value.studentCount)} students</span>
      </div>
      <div class="mix-stats">
        <span>Expected <strong>${money(value.expectedRevenue)}</strong></span>
        <span>Collected <strong>${money(value.collectedRevenue)}</strong></span>
      </div>
      <div class="mix-track" title="${collectionPct}% collected"><div class="mix-fill" style="width:${collectionPct}%"></div></div>
      <small>Outstanding ${money(value.outstandingBalance)}</small>
    </div>
  `;
  }).join("");

  qs("#unpaidList").innerHTML = currentUnpaid.length
    ? currentUnpaid
        .map((row) => `
          <div class="unpaid-item">
            <strong>${row.student_name}</strong>
            <span>${row.parent_guardian || "No guardian"} - ${subjectText(row.subjects)} - Outstanding ${money(row.balance || row.std_monthly_fee)}</span>
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
  const monthlyFeeDeduction = statusRows.reduce((sum, row) => sum + (row.std_monthly_fee || 0), 0);
  qs("#enrolmentReportSummary").innerHTML = [
    `<div class="info-tile"><span>Selected Months</span><strong>${selected.length}</strong></div>`,
    `<div class="info-tile"><span>Enrolments</span><strong>${rows.length}</strong></div>`,
    `<div class="info-tile"><span>Current Students</span><strong>${active.length}</strong></div>`,
    `<div class="info-tile"><span>C to D Changes</span><strong>${statusRows.length}</strong></div>`,
    `<div class="info-tile"><span>Subject Units</span><strong>${number(units)}</strong></div>`,
    `<div class="info-tile"><span>Monthly Fee Added</span><strong>${money(monthlyFee)}</strong></div>`,
    `<div class="info-tile" style="border-color:#fda29b;background:#fef3f2"><span>Monthly Fee Deduction</span><strong style="color:#b42318">${money(monthlyFeeDeduction)}</strong></div>`,
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

function renderPlTab() {
  const activeMonth = state.activePlMonth || state.settings.current_month || "May-26";
  
  const plMonth = qs("#plMonth");
  if (plMonth) plMonth.value = activeMonth;
  
  const kpiLabel = qs("#plKpiMonthLabel");
  if (kpiLabel) kpiLabel.textContent = `For Selected Month (${activeMonth})`;

  let expenseRecord = (state.expenses || []).find((e) => e.month_label === activeMonth);
  let isCopied = false;
  let copiedFromLabel = "";

  if (!expenseRecord) {
    const monthIndex = state.months.indexOf(activeMonth);
    if (monthIndex > 0) {
      copiedFromLabel = state.months[monthIndex - 1];
      expenseRecord = (state.expenses || []).find((e) => e.month_label === copiedFromLabel);
      if (expenseRecord) {
        isCopied = true;
      }
    }
  }

  const finalRecord = expenseRecord || {
    rent_expense: 0.00,
    royalty_expense: 0.00,
    utilities_expense: 0.00,
    misc_expense: 0.00,
    misc_details: ""
  };

  if (document.activeElement !== qs("#plRent")) qs("#plRent").value = Number(finalRecord.rent_expense || 0).toFixed(2);
  if (document.activeElement !== qs("#plRoyalty")) qs("#plRoyalty").value = Number(finalRecord.royalty_expense || 0).toFixed(2);
  if (document.activeElement !== qs("#plUtilities")) qs("#plUtilities").value = Number(finalRecord.utilities_expense || 0).toFixed(2);
  if (document.activeElement !== qs("#plMisc")) qs("#plMisc").value = Number(finalRecord.misc_expense || 0).toFixed(2);
  if (document.activeElement !== qs("#plMiscDetails")) qs("#plMiscDetails").value = finalRecord.misc_details || "";

  const copyIndicator = qs("#plFormCopyIndicator");
  if (copyIndicator) {
    if (isCopied) {
      copyIndicator.textContent = `Showing values copied from ${copiedFromLabel} (Unsaved)`;
      copyIndicator.style.display = "block";
    } else {
      copyIndicator.style.display = "none";
    }
  }

  const revenue = state.fee_tracker.reduce((sum, row) => sum + (row.months[activeMonth] || 0), 0);
  const rent = finalRecord.rent_expense ? Number(finalRecord.rent_expense) : 0;
  const royalty = finalRecord.royalty_expense ? Number(finalRecord.royalty_expense) : 0;
  const utilities = finalRecord.utilities_expense ? Number(finalRecord.utilities_expense) : 0;
  const misc = finalRecord.misc_expense ? Number(finalRecord.misc_expense) : 0;
  const totalExpenses = rent + royalty + utilities + misc;
  const netProfit = revenue - totalExpenses;
  const marginPercent = revenue > 0 ? (netProfit / revenue) * 100 : 0;

  const metricsContainer = qs("#plMetrics");
  if (metricsContainer) {
    metricsContainer.innerHTML = [
      metric("Total Revenue", money(revenue), "accent"),
      metric("Total Expenses", money(totalExpenses), "warning"),
      metric("Net Profit", money(netProfit), netProfit >= 0 ? "success" : "warning"),
      metric("Profit Margin %", revenue > 0 ? `${number(marginPercent)}%` : "0%", netProfit >= 0 ? "success" : "warning"),
    ].join("");
  }

  const yearlyData = {};
  for (const monthLabel of state.months) {
    const year = parseYearFromMonthLabel(monthLabel);
    if (!year) continue;

    if (!yearlyData[year]) {
      yearlyData[year] = {
        revenue: 0,
        rent: 0,
        royalty: 0,
        utilities: 0,
        misc: 0,
        expenses: 0,
        netProfit: 0
      };
    }

    const mRev = state.fee_tracker.reduce((sum, row) => sum + (row.months[monthLabel] || 0), 0);
    const mExpRecord = (state.expenses || []).find((e) => e.month_label === monthLabel);
    const mRent = mExpRecord ? Number(mExpRecord.rent_expense || 0) : 0;
    const mRoyalty = mExpRecord ? Number(mExpRecord.royalty_expense || 0) : 0;
    const mUtilities = mExpRecord ? Number(mExpRecord.utilities_expense || 0) : 0;
    const mMisc = mExpRecord ? Number(mExpRecord.misc_expense || 0) : 0;
    const mExpenses = mRent + mRoyalty + mUtilities + mMisc;

    yearlyData[year].revenue += mRev;
    yearlyData[year].rent += mRent;
    yearlyData[year].royalty += mRoyalty;
    yearlyData[year].utilities += mUtilities;
    yearlyData[year].misc += mMisc;
    yearlyData[year].expenses += mExpenses;
    yearlyData[year].netProfit += (mRev - mExpenses);
  }

  const yearlyHeaders = ["Year", "Total Revenue", "Rent", "Royalty", "Utilities", "Miscellaneous", "Total Expenses", "Net Profit", "Profit Margin"];
  const yearlyRows = Object.entries(yearlyData)
    .sort((a, b) => Number(b[0]) - Number(a[0]))
    .map(([year, data]) => {
      const margin = data.revenue > 0 ? (data.netProfit / data.revenue) * 100 : 0;
      return [
        year,
        money(data.revenue),
        money(data.rent),
        money(data.royalty),
        money(data.utilities),
        money(data.misc),
        money(data.expenses),
        `<span class="${data.netProfit >= 0 ? "pl-profit" : "pl-loss"}">${money(data.netProfit)}</span>`,
        `<span class="${data.netProfit >= 0 ? "pl-profit" : "pl-loss"}">${number(margin)}%</span>`
      ];
    });
  
  const yearlyTable = qs("#plYearlyTable");
  if (yearlyTable) {
    renderTable(yearlyTable, yearlyHeaders, yearlyRows);
  }

  const monthlyHeaders = ["Month", "Revenue", "Rent", "Royalty", "Utilities", "Miscellaneous", "Total Expenses", "Net Profit", "Profit Margin", "Misc Details"];
  
  const monthlyRows = [...state.months].reverse().map((monthLabel) => {
    const mRev = state.fee_tracker.reduce((sum, row) => sum + (row.months[monthLabel] || 0), 0);
    const mExpRecord = (state.expenses || []).find((e) => e.month_label === monthLabel);
    const mRent = mExpRecord ? Number(mExpRecord.rent_expense || 0) : 0;
    const mRoyalty = mExpRecord ? Number(mExpRecord.royalty_expense || 0) : 0;
    const mUtilities = mExpRecord ? Number(mExpRecord.utilities_expense || 0) : 0;
    const mMisc = mExpRecord ? Number(mExpRecord.misc_expense || 0) : 0;
    const mExpenses = mRent + mRoyalty + mUtilities + mMisc;
    const mNetProfit = mRev - mExpenses;
    const mMargin = mRev > 0 ? (mNetProfit / mRev) * 100 : 0;
    const detailsText = mExpRecord ? (mExpRecord.misc_details || "") : "";

    return [
      monthLabel,
      money(mRev),
      money(mRent),
      money(mRoyalty),
      money(mUtilities),
      money(mMisc),
      money(mExpenses),
      `<span class="${mNetProfit >= 0 ? "pl-profit" : "pl-loss"}">${money(mNetProfit)}</span>`,
      `<span class="${mNetProfit >= 0 ? "pl-profit" : "pl-loss"}">${number(mMargin)}%</span>`,
      `<span class="pl-notes-cell" title="${escapeHtml(detailsText)}">${escapeHtml(detailsText)}</span>`
    ];
  });

  const monthlyTable = qs("#plMonthlyTable");
  if (monthlyTable) {
    renderTable(monthlyTable, monthlyHeaders, monthlyRows);
  }
}

function parseYearFromMonthLabel(monthLabel) {
  const parts = String(monthLabel).split('-');
  if (parts.length === 2) {
    const yr = parts[1];
    return 2000 + Number(yr);
  }
  return null;
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

function sortHeader(field, label) {
  let indicator = "";
  if (state.rosterSortField === field) {
    indicator = state.rosterSortOrder === "asc" ? " ▲" : " ▼";
  }
  return `<span class="sortable-header" onclick="toggleRosterSort('${field}')" style="cursor:pointer;user-select:none;color:var(--accent);font-weight:bold">${label}${indicator}</span>`;
}

function toggleRosterSort(field) {
  if (state.rosterSortField === field) {
    state.rosterSortOrder = state.rosterSortOrder === "asc" ? "desc" : "asc";
  } else {
    state.rosterSortField = field;
    state.rosterSortOrder = "asc";
  }
  renderRoster();
}

function renderRoster() {
  const term = qs("#search").value.toLowerCase();
  const filter = qs("#rosterStatusFilter").value;
  const deleteAction = canDeleteStudentRecords()
    ? (id) => `<button class="small danger" data-review-delete="${id}">Review/Delete</button>`
    : () => "";

  let filtered = state.students
    .filter((s) => {
      if (filter === "deleted") return Boolean(s.deleted_at);
      if (s.deleted_at) return false;
      return filter === "all" || s.status.toUpperCase() === "C";
    })
    .filter((s) => JSON.stringify(s).toLowerCase().includes(term));

  if (state.rosterSortField) {
    const field = state.rosterSortField;
    const isAsc = state.rosterSortOrder === "asc";
    filtered.sort((a, b) => {
      if (field === "number") {
        const numA = Number(a.number) || 0;
        const numB = Number(b.number) || 0;
        return isAsc ? numA - numB : numB - numA;
      }

      let valA = "";
      let valB = "";
      if (field === "name") {
        valA = a.student_name || "";
        valB = b.student_name || "";
      } else if (field === "status") {
        valA = a.status || "";
        valB = b.status || "";
      } else if (field === "enrol_date") {
        valA = a.enrol_date || "";
        valB = b.enrol_date || "";
      } else if (field === "subjects") {
        valA = subjectText(a.subjects) || "";
        valB = subjectText(b.subjects) || "";
      }

      // Handle cases where enrol_date or other values are missing/blank
      if (!valA && valB) return isAsc ? 1 : -1;
      if (valA && !valB) return isAsc ? -1 : 1;
      if (!valA && !valB) return 0;

      const comp = valA.localeCompare(valB, undefined, { numeric: true, sensitivity: 'base' });
      return isAsc ? comp : -comp;
    });
  }

  const rows = filtered.map((s) => [
    s.number,
    `<button class="link-button ${[isStudentOverdue(s.id) ? "overdue-name" : "", isActiveDuplicateName(s) ? "duplicate-name" : ""].filter(Boolean).join(" ")}" data-profile="${s.id}">${s.student_name}</button>`,
    s.parent_guardian,
    `<span class="status-badge ${s.deleted_at ? "inactive" : s.status.toUpperCase() === "C" ? "current" : "inactive"}">${s.deleted_at ? "Deleted" : s.status}</span>`,
    s.enrol_date,
    subjectText(s.subjects),
    scheduleText(s),
    String(s.rate_type || "").toUpperCase() === "EL"
      ? `<span class="timetable-badge is-el">EL</span>`
      : `<span class="timetable-badge is-r">R</span>`,
    money(s.std_monthly_fee),
    s.payment_method,
    s.phone || "",
    s.email || "",
    s.last_modification || "",
    `<div class="row-actions"><button class="small" data-profile="${s.id}">Profile</button>${s.deleted_at && canDeleteStudentRecords() ? `<button class="small" data-restore-student="${s.id}">Restore</button>` : deleteAction(s.id)}</div>`,
  ]);

  const headers = [
    sortHeader("number", "No#"),
    sortHeader("name", "Student Name"),
    "Parent / Guardian",
    sortHeader("status", "Status"),
    sortHeader("enrol_date", "Enrol Date"),
    sortHeader("subjects", "Subjects"),
    "Weekly Schedule",
    "Rate Type",
    "STD Fee",
    "Pay Method",
    "Phone",
    "Email",
    "Last Modification",
    "Actions"
  ];

  renderTable(qs("#rosterTable"), headers, rows);
  document.querySelectorAll("[data-profile]").forEach((button) => button.addEventListener("click", () => showStudentProfile(button.dataset.profile)));
  document.querySelectorAll("[data-review-delete]").forEach((button) => button.addEventListener("click", () => showStudentDeleteReview(button.dataset.reviewDelete)));
  document.querySelectorAll("[data-restore-student]").forEach((button) => button.addEventListener("click", () => restoreStudent(button.dataset.restoreStudent)));
}

function isStudentOverdue(studentId) {
  const row = state.fee_tracker.find((item) => String(item.id) === String(studentId));
  return row ? isCurrentMonthOverdue(row) : false;
}

function showStudentProfile(id) {
  const student = state.students.find((s) => String(s.id) === String(id));
  const fee = state.fee_tracker.find((s) => String(s.id) === String(id)) || { months: {}, total_paid: 0, balance: 0 };
  if (!student) return;
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
      <div><span>Deleted</span><strong>${student.deleted_at || "No"}</strong></div>
      <div><span>Subjects</span><strong>${subjectText(student.subjects)}</strong></div>
      <div><span>Weekly Schedule</span><strong>${escapeHtml(scheduleText(student))}</strong></div>
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

function paymentTimeline(student, fee) {
  const enrolDate = student.enrol_date ? new Date(`${student.enrol_date}T00:00:00`) : null;
  const timelineMonths = state.months.filter((m) => {
    const d = monthDate(m);
    return !enrolDate || !d || d >= new Date(enrolDate.getFullYear(), enrolDate.getMonth(), 1);
  });
  return timelineMonths.map((m) => `<span class="${fee.months[m] > 0 ? "paid" : ""}" title="${m}: ${money(fee.months[m])}">${m}</span>`).join("");
}

function showStudentDeleteReview(id) {
  const student = state.students.find((s) => String(s.id) === String(id));
  const fee = state.fee_tracker.find((s) => String(s.id) === String(id)) || { months: {}, total_paid: 0, balance: 0 };
  if (!student) {
    toast("Student record was not found");
    return;
  }
  const currentMonth = state.settings.current_month || "May-26";
  qs("#studentProfile").innerHTML = `
    <div class="section-title">
      <div>
        <h2>Review/Delete Student</h2>
        <span>Read-only student profile before permanent deletion</span>
      </div>
      <button type="button" class="ghost" id="cancelDeleteReviewTop">Cancel</button>
    </div>
    <div class="profile-summary">
      <div><span>Student Name</span><strong>${escapeHtml(student.student_name)}</strong></div>
      <div><span>Parent / Guardian</span><strong>${escapeHtml(student.parent_guardian || "-")}</strong></div>
      <div><span>Status</span><strong>${escapeHtml(student.status)}</strong></div>
      <div><span>Subjects</span><strong>${escapeHtml(subjectText(student.subjects))}</strong></div>
      <div><span>Rate Type</span><strong>${escapeHtml(student.rate_type || "-")}</strong></div>
      <div><span>Monthly Fee</span><strong>${money(student.std_monthly_fee)}</strong></div>
      <div><span>Payment Method</span><strong>${escapeHtml(student.payment_method || "-")}</strong></div>
      <div><span>Enrol Date</span><strong>${escapeHtml(student.enrol_date || "-")}</strong></div>
      <div><span>Phone</span><strong>${escapeHtml(student.phone || "-")}</strong></div>
      <div><span>Email</span><strong>${escapeHtml(student.email || "-")}</strong></div>
      <div><span>Siblings</span><strong>${escapeHtml(student.siblings || "-")}</strong></div>
      <div><span>Last Modification</span><strong>${escapeHtml(student.last_modification || "-")}</strong></div>
      <div><span>Deleted At</span><strong>${escapeHtml(student.deleted_at || "-")}</strong></div>
      <div><span>Delete Reason</span><strong>${escapeHtml(student.delete_reason || "-")}</strong></div>
    </div>
    <div class="profile-block">
      <h3>Notes</h3>
      <p>${escapeHtml(student.notes || "No notes recorded.")}</p>
    </div>
    <div class="profile-block">
      <h3>Current Month Balance</h3>
      <p>${escapeHtml(currentMonth)} payment: <strong>${money(fee.months[currentMonth] || 0)}</strong><br>Outstanding balance: <strong>${money(fee.balance || 0)}</strong></p>
    </div>
    <div class="profile-block">
      <h3>Payment Timeline</h3>
      <div class="timeline">${paymentTimeline(student, fee)}</div>
      <p>Total paid: <strong>${money(fee.total_paid)}</strong> - Balance: <strong>${money(fee.balance)}</strong></p>
    </div>
    <div class="review-actions">
      <button type="button" class="danger" id="confirmDeleteStudent">Delete Student</button>
      <button type="button" class="ghost" id="cancelDeleteReview">Cancel</button>
    </div>
  `;
  qs("#studentProfile").classList.remove("collapsed");
  qs("#cancelDeleteReviewTop").addEventListener("click", closeProfile);
  qs("#cancelDeleteReview").addEventListener("click", closeProfile);
  qs("#confirmDeleteStudent").addEventListener("click", () => deleteStudent(id));
}

function closeProfile() {
  qs("#studentProfile").classList.add("collapsed");
}

function renderSettings() {
  const activeSubTab = state.activeSettingsSubTab || "centre";
  document.querySelectorAll("#settingsSubtabs .subtab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.settingsTab === activeSubTab);
  });
  qs("#settingsCentreContent")?.classList.toggle("collapsed", activeSubTab !== "centre");
  qs("#settingsRatesContent")?.classList.toggle("collapsed", activeSubTab !== "rates");
  qs("#settingsUsersContent")?.classList.toggle("collapsed", activeSubTab !== "users");
  qs("#settingsBackupContent")?.classList.toggle("collapsed", activeSubTab !== "backup");
  qs("#settingsAuditContent")?.classList.toggle("collapsed", activeSubTab !== "audit");

  const form = qs("#settingsForm");
  if (form) {
    for (const [key, value] of Object.entries(state.settings)) {
      if (form.elements[key]) form.elements[key].value = value;
    }
    
    // Only Admin role can change organization name, branch name, and branch code
    const rRole = String(currentUserRole() || "").toLowerCase();
    const isAdmin = rRole === "admin" || rRole === "owner";
    const adminFields = ["institution_name", "branch_name", "branch_code"];
    adminFields.forEach(name => {
      const field = form.elements[name];
      if (field) {
        field.disabled = !isAdmin;
        field.readOnly = !isAdmin;
        field.style.backgroundColor = isAdmin ? "" : "#f5f5f5";
        field.style.cursor = isAdmin ? "" : "not-allowed";
      }
    });
  }
  
  const ratesTable = qs("#ratesTable");
  if (ratesTable) {
    ratesTable.innerHTML = `
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
  }
  
  const formulaManifest = qs("#formulaManifest");
  if (formulaManifest) formulaManifest.textContent = JSON.stringify(state.formula_manifest, null, 2);
  
  renderBillingAndAccess();
  document.querySelectorAll("[data-save-rate]").forEach((button) => button.addEventListener("click", () => saveRate(button.dataset.saveRate)));
  document.querySelectorAll("[data-delete-rate]").forEach((button) => button.addEventListener("click", () => deleteRate(button.dataset.deleteRate)));
}

function renderCentreSetup() {
  const isSetupCompleted = String(state.settings?.center_setup_completed || "0") === "1";
  const setupTabButton = qs("#tabCentreSetup");
  if (setupTabButton) {
    setupTabButton.style.display = isSetupCompleted ? "none" : "block";
  }
  const banner = qs("#centreSetupCompletedBanner");
  if (banner) {
    banner.classList.toggle("collapsed", !isSetupCompleted);
  }
  const form = qs("#centreSetupForm");
  if (form) {
    form.elements["organization_name"].value = state.settings?.institution_name || "";
    form.elements["branch_name"].value = state.settings?.branch_name || "";
    form.elements["branch_code"].value = state.settings?.branch_code || "";
    form.elements["organization_name"].disabled = isSetupCompleted;
    form.elements["branch_name"].disabled = isSetupCompleted;
    form.elements["branch_code"].disabled = isSetupCompleted;
  }
  const actions = qs("#setupFormActions");
  if (actions) {
    actions.style.display = isSetupCompleted ? "none" : "block";
  }
}

function selectedSubjectValues() {
  return [...document.querySelectorAll('[name="subjects_choice"]:checked')].map((input) => input.value);
}

function setSelectedSubjects(value) {
  renderSubjectChoices(value);
}

function setScheduleRows(schedules = []) {
  [0, 1].forEach((index) => {
    const item = schedules[index] || {};
    const weekday = qs(`[data-schedule-weekday="${index}"]`);
    const start = qs(`[data-schedule-start="${index}"]`);
    const end = qs(`[data-schedule-end="${index}"]`);
    if (weekday) weekday.value = item.weekday || "";
    if (start) start.value = item.start_time || "";
    if (end) end.value = item.end_time || "";
  });
}

function collectScheduleRows() {
  return [0, 1].map((index) => ({
    weekday: qs(`[data-schedule-weekday="${index}"]`)?.value || "",
    start_time: qs(`[data-schedule-start="${index}"]`)?.value || "",
    end_time: qs(`[data-schedule-end="${index}"]`)?.value || "",
  })).filter((item) => item.weekday || item.start_time || item.end_time);
}

function renderBillingAndAccess() {
  const subscription = state.subscriptions[0] || {};
  const roleOptions = state.role_options?.length ? state.role_options : ["Admin", "Office Manager", "Office Assistant", "Staff"];
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
  renderTable(
    qs("#auditLogTable"),
    ["Date", "Action", "Entity", "Actor", "Summary"],
    (state.audit_logs || []).map((row) => [
      escapeHtml(row.created_at || ""),
      escapeHtml(row.action || ""),
      escapeHtml(`${row.entity_type || ""} ${row.entity_id || ""}`.trim()),
      escapeHtml(row.actor_email || ""),
      escapeHtml(row.summary || ""),
    ])
  );

  document.querySelectorAll("[data-save-discount]").forEach((button) => button.addEventListener("click", () => saveDiscount(button.dataset.saveDiscount)));
  document.querySelectorAll("[data-delete-discount]").forEach((button) => button.addEventListener("click", () => deleteDiscount(button.dataset.deleteDiscount)));
  document.querySelectorAll("[data-save-user]").forEach((button) => button.addEventListener("click", () => saveUser(button.dataset.saveUser)));
  document.querySelectorAll("[data-delete-user]").forEach((button) => button.addEventListener("click", () => deleteUser(button.dataset.deleteUser)));

  renderDelegationPanel();
}

// ── Role Delegation ───────────────────────────────────────────────────────────
// Master Admin can temporarily grant Admin or Office Manager role to any user.
// Delegations are stored in localStorage under 'sb_delegations'.
// Each entry: { id, userId, userEmail, userName, originalRole, delegatedRole, delegatedBy, delegatedAt, active }

function getDelegations() {
  try { return JSON.parse(localStorage.getItem('sb_delegations') || '[]'); } catch { return []; }
}
function saveDelegations(list) {
  localStorage.setItem('sb_delegations', JSON.stringify(list));
}

function isMasterAdmin() {
  const role = String(state.current_user?.role || currentUserRole() || '').toLowerCase();
  return role === 'admin' || role === 'administrator' || role === 'principal_owner';
}

function renderDelegationPanel() {
  const panel = qs('#delegationPanel');
  if (!panel) return;

  // Only show delegation panel to Master Admin
  if (!isMasterAdmin()) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';

  const delegations = getDelegations();
  const listEl = qs('#delegationList');
  if (listEl) {
    const active = delegations.filter(d => d.active);
    if (active.length === 0) {
      listEl.innerHTML = '<p class="muted-note">No active delegations. Use the form below to temporarily grant a role.</p>';
    } else {
      listEl.innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="border-bottom:1px solid var(--border,#e5e7eb)">
            <th style="text-align:left;padding:6px 8px">User</th>
            <th style="text-align:left;padding:6px 8px">Delegated Role</th>
            <th style="text-align:left;padding:6px 8px">Original Role</th>
            <th style="text-align:left;padding:6px 8px">Granted On</th>
            <th style="text-align:left;padding:6px 8px">Action</th>
          </tr></thead>
          <tbody>
            ${active.map(d => `
              <tr style="border-bottom:1px solid var(--border,#f3f4f6)">
                <td style="padding:6px 8px"><strong>${escapeHtml(d.userName || d.userEmail)}</strong><br><small style="color:#6b7280">${escapeHtml(d.userEmail)}</small></td>
                <td style="padding:6px 8px"><span style="background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:9px;font-size:12px;font-weight:600">${escapeHtml(d.delegatedRole)}</span></td>
                <td style="padding:6px 8px;color:#6b7280">${escapeHtml(d.originalRole)}</td>
                <td style="padding:6px 8px;color:#6b7280">${escapeHtml(d.delegatedAt)}</td>
                <td style="padding:6px 8px"><button class="small danger" data-revoke-delegation="${d.id}">Disable</button></td>
              </tr>
            `).join('')}
          </tbody>
        </table>`;
    }
    listEl.querySelectorAll('[data-revoke-delegation]').forEach(btn =>
      btn.addEventListener('click', () => revokeDelegation(btn.dataset.revokeDelegation))
    );
  }

  // Populate user select — all non-admin users
  const userSelect = qs('#delegationUserSelect');
  if (userSelect) {
    const users = state.users || [];
    const nonAdmins = users.filter(u => u.active && !['Admin', 'Administrator'].includes(u.role));
    userSelect.innerHTML = '<option value="">Select user…</option>' +
      nonAdmins.map(u => `<option value="${escapeAttr(String(u.id))}">${escapeHtml(u.display_name || u.email)} — ${escapeHtml(u.role)}</option>`).join('');
  }
}

function grantDelegation(userId, delegatedRole) {
  const user = (state.users || []).find(u => String(u.id) === String(userId));
  if (!user) { toast('User not found', 'err'); return; }

  const delegations = getDelegations();
  // Check no active delegation already exists for this user
  if (delegations.find(d => d.active && String(d.userId) === String(userId))) {
    toast(`${user.display_name || user.email} already has an active delegation. Disable it first.`, 'err');
    return;
  }

  const entry = {
    id: Date.now(),
    userId: user.id,
    userEmail: user.email,
    userName: user.display_name || user.email,
    originalRole: user.role,
    delegatedRole,
    delegatedBy: state.current_user?.email || 'Master Admin',
    delegatedAt: new Date().toLocaleDateString('en-CA', { year:'numeric', month:'short', day:'numeric' }),
    active: true,
  };
  delegations.push(entry);
  saveDelegations(delegations);

  // Apply the role in state.users immediately (UI reflects change without server round-trip)
  user.role = delegatedRole;
  // Persist via API in background (non-blocking)
  api(`/api/users/${user.id}`, { method: 'PUT', body: JSON.stringify({ role: delegatedRole }) }).catch(() => {});

  toast(`${entry.userName} has been granted ${delegatedRole} access.`, 'ok');
  renderBillingAndAccess();
}

function revokeDelegation(delegationId) {
  const delegations = getDelegations();
  const entry = delegations.find(d => String(d.id) === String(delegationId));
  if (!entry) return;

  entry.active = false;
  saveDelegations(delegations);

  // Restore original role in state.users
  const user = (state.users || []).find(u => String(u.id) === String(entry.userId));
  if (user) {
    user.role = entry.originalRole;
    api(`/api/users/${user.id}`, { method: 'PUT', body: JSON.stringify({ role: entry.originalRole }) }).catch(() => {});
  }

  toast(`${entry.userName}'s ${entry.delegatedRole} access has been disabled. Role restored to ${entry.originalRole}.`, 'ok');
  renderBillingAndAccess();
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
        start_time: shiftType === "Off" ? "" : (state.settings?.operating_start || "15:00"),
        end_time: shiftType === "Off" ? "" : (state.settings?.operating_end || "20:00"),
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
  const frame = qs("#staffbaseFrame");
  if (!frame) return;
  const canOpenStaff = hasStaffAccess();
  qs("#staffLockedState")?.classList.toggle("collapsed", canOpenStaff);
  frame.classList.toggle("collapsed", !canOpenStaff);
  
  if (canOpenStaff && activeAdminArea === "staff") {
    const email = currentUserEmail();
    const name = currentUserName();
    const role = currentUserRole();
    let mappedRole = "staff";
    const roleLower = String(role || "").toLowerCase();
    if (roleLower === "admin" || roleLower === "administrator") {
      mappedRole = "administrator";
    } else if (roleLower === "owner" || roleLower === "principal_owner" || roleLower === "principal") {
      mappedRole = "principal_owner";
    } else if (roleLower === "office manager" || roleLower === "office_manager") {
      mappedRole = "office_manager";
    } else if (roleLower === "office assistant" || roleLower === "office_assistant") {
      mappedRole = "office_assistant";
    }
    const schoolName = state.settings.institution_name || "";
    const subjects = state.settings.subjects_offered || "";
    const weekdays = PRESENCE_WEEKDAYS.map(day => day.substring(0, 3)).join(",");

    let src = `staffbase.html?auto_email=${encodeURIComponent(email)}&auto_name=${encodeURIComponent(name)}&auto_role=${encodeURIComponent(mappedRole)}`;
    src += `&school_name=${encodeURIComponent(schoolName)}`;
    src += `&subjects=${encodeURIComponent(subjects)}`;
    src += `&weekdays=${encodeURIComponent(weekdays)}`;
    src += `&operating_start=${encodeURIComponent(state.settings?.operating_start || "15:00")}`;
    src += `&operating_end=${encodeURIComponent(state.settings?.operating_end || "20:00")}`;

    if (authSession && authSession.access_token) {
      src += `&access_token=${encodeURIComponent(authSession.access_token)}`;
    }
    if (frame.getAttribute("src") !== src) {
      frame.setAttribute("src", src);
      // Push settings once the iframe finishes loading so staffbase picks up
      // the current school name, subjects, and hours without requiring a save.
      frame.onload = () => pushSettingsToStaffbase();
    }
  } else {
    if (frame.getAttribute("src") !== "about:blank") {
      frame.setAttribute("src", "about:blank");
    }
  }
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

function renderReconciliationRules() {
  const container = qs("#reconRuleChoices");
  if (!container) return;
  if (!state.reconciliationMatchRules?.length) state.reconciliationMatchRules = [...DEFAULT_RECON_RULES];
  const selected = new Set(state.reconciliationMatchRules);
  container.innerHTML = RECON_RULES.map(([id, label]) => `
    <label>
      <input type="checkbox" data-recon-rule="${id}" ${selected.has(id) ? "checked" : ""}>
      <span>${label}</span>
    </label>
  `).join("");
  document.querySelectorAll("[data-recon-rule]").forEach((input) => {
    input.addEventListener("change", () => {
      state.reconciliationMatchRules = [...document.querySelectorAll("[data-recon-rule]:checked")].map((node) => node.dataset.reconRule);
    });
  });
  const method = qs("#reconPaymentMethod");
  if (method) method.value = state.reconciliationPaymentMethod || "PAD";
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
  const mapped = rows.map((row) => {
    const debit = normalizeMoney(pick(row, ["debit", "withdrawal", "withdrawal amount"]));
    const credit = normalizeMoney(pick(row, ["credit", "deposit", "deposit amount"]));
    const amount = Math.abs(normalizeMoney(pick(row, ["amount", "payment amount", "paid amount", "transaction amount"])) || credit || debit);
    return {
      date: pick(row, ["date", "transaction date", "posted date", "payment date", "paid date", "process date"]),
      description: pick(row, ["description", "memo", "details", "name", "payee", "payor", "payee/payor name", "payer name"]),
      amount,
      source: pick(row, ["source", "account", "institution", "card"]),
      reference: pick(row, ["reference", "reference number", "transaction id", "confirmation", "trace"]),
      student_id: pick(row, ["student id", "student_id", "customer id", "member id"]),
      student_name: pick(row, ["student name", "student"]),
      parent_name: pick(row, ["parent name", "parent", "guardian", "payer name", "payer", "payor", "payee/payor name"]),
      email: pick(row, ["email", "email address", "payer email"]),
      payment_method: pick(row, ["payment method", "method", "payment type"]),
      organization_id: pick(row, ["organization id", "organization_id", "centre id", "center id"]),
      branch_id: pick(row, ["branch id", "branch_id", "location id"]),
    };
  }).filter((row) => row.date || row.description || row.amount || row.student_name || row.parent_name || row.payment_method);
  const paymentRows = mapped.filter((row) => Number(row.amount || 0) > 0);
  return {
    rows: paymentRows,
    skippedZeroRows: mapped.length - paymentRows.length,
    totalRows: mapped.length,
  };
}

function reconSearchText(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function reconTokens(value) {
  return reconSearchText(value).split(/\s+/).filter((token) => token.length >= 2);
}

function reconCommaVariant(value) {
  const text = String(value || "").trim();
  if (!text.includes(",")) return "";
  const [left, right] = text.split(",", 2).map((part) => part.trim());
  return `${right} ${left}`.trim();
}

function reconCandidateHasIdentity(candidate) {
  const reasonText = (candidate?.reasons || []).join(" ").toLowerCase();
  return /student|parent|guardian|alias|email|search|manual/.test(reasonText) && !/no student or parent identity match/.test(reasonText);
}

function reconMeaningfulCandidate(candidate) {
  return Boolean(candidate?.student_id) && (Number(candidate.score || 0) >= 50 || reconCandidateHasIdentity(candidate));
}

function reconSearchScore(student, query, row = {}) {
  const queryText = reconSearchText([
    query,
    reconCommaVariant(query),
    row.student_name,
    row.parent_name,
    reconCommaVariant(row.parent_name),
    row.description,
    reconCommaVariant(row.description),
  ].filter(Boolean).join(" "));
  if (!queryText) return 0;
  const studentText = reconSearchText(student.student_name || "");
  const parentText = reconSearchText(`${student.parent_guardian || ""} ${reconCommaVariant(student.parent_guardian || "")}`);
  const haystack = reconSearchText(`${student.student_name || ""} ${student.parent_guardian || ""} ${reconCommaVariant(student.parent_guardian || "")} ${student.email || ""}`);
  if (studentText && reconSearchText(row.student_name) && studentText === reconSearchText(row.student_name)) return 100;
  if (parentText && reconSearchText(row.parent_name) && parentText.includes(reconSearchText(row.parent_name))) return 95;
  if (haystack.includes(queryText)) return 100;
  const tokens = reconTokens(queryText);
  if (!tokens.length) return 0;
  const hits = tokens.filter((token) => haystack.includes(token)).length;
  const base = Math.round((hits / tokens.length) * 80);
  const studentHits = reconTokens(row.student_name).filter((token) => studentText.includes(token)).length;
  const parentHits = reconTokens(row.parent_name || row.description).filter((token) => parentText.includes(token)).length;
  return Math.max(base, Math.min(100, studentHits * 35 + parentHits * 18));
}

function reconciliationStudentOptions(row) {
  const manualQuery = String(row.student_search || "").trim();
  const query = String(manualQuery || row.corrected_student_name || row.student_name || row.parent_name || row.description || "").trim();
  const existing = new Map((row.candidates || []).filter(reconMeaningfulCandidate).map((candidate) => [String(candidate.student_id), candidate]));
  if (row.best_match && reconMeaningfulCandidate(row.best_match)) existing.set(String(row.best_match.student_id), row.best_match);
  const rosterMatches = (state.students || [])
    .filter((student) => String(student.status || "").toUpperCase() === "C")
    .map((student) => ({ student, score: reconSearchScore(student, query, manualQuery ? {} : row) }))
    .filter((item) => item.score >= 30)
    .sort((a, b) => b.score - a.score || String(a.student.student_name || "").localeCompare(String(b.student.student_name || "")))
    .slice(0, 25)
    .map(({ student, score }) => ({
      student_id: student.id,
      student_name: student.student_name,
      parent_guardian: student.parent_guardian,
      payment_method: student.payment_method,
      expected_fee: Number(student.std_monthly_fee || 0),
      score: existing.has(String(student.id)) ? Math.max(existing.get(String(student.id)).score || 0, score) : score,
      current_paid: 0,
      already_paid: false,
      reasons: existing.get(String(student.id))?.reasons || ["smart roster search match"],
    }));
  for (const match of rosterMatches) {
    if (!existing.has(String(match.student_id))) existing.set(String(match.student_id), match);
  }
  return [...existing.values()].sort((a, b) => Number(b.score || 0) - Number(a.score || 0) || String(a.student_name || "").localeCompare(String(b.student_name || "")));
}

function reconciliationCanCreateStudent(row, options) {
  if (row.excluded || Number(row.amount || 0) <= 0) return false;
  if (row.best_match && reconMeaningfulCandidate(row.best_match)) return false;
  return !(options || []).some(reconMeaningfulCandidate);
}

function renderReconciliation() {
  if (!qs("#reconciliationTable")) return;
  const approved = (state.reconciliation || []).find((item) => item.match_status === "approved") || {};
  const rows = state.reconciliationPreview || [];
  const activeRows = rows.filter((row) => !row.excluded);
  const excludedCount = rows.filter((row) => row.excluded).length;
  const verifiedCount = activeRows.filter((row) => row.verified).length;
  const summary = state.reconciliationSummary || {};
  const stats = summary.csv || {};
  const finance = summary.financial || {};
  const students = summary.students || {};
  qs("#reconSummary").innerHTML = [
    `<div class="info-tile"><span>Approved Matches</span><strong>${approved.count || 0}</strong></div>`,
    `<div class="info-tile"><span>Approved Total</span><strong>${money(approved.total || 0)}</strong></div>`,
    `<div class="info-tile"><span>Saved Payer Aliases</span><strong>${state.payer_aliases.length}</strong></div>`,
    `<div class="info-tile"><span>Ready to Add</span><strong>${verifiedCount} / ${activeRows.length}</strong></div>`,
    `<div class="info-tile"><span>Excluded Rows</span><strong>${excludedCount}</strong></div>`,
    `<div class="info-tile"><span>Zero Rows Skipped</span><strong>${state.reconciliationSkippedZeroRows || 0}</strong></div>`,
    `<div class="info-tile"><span>Upload Mode</span><strong>${escapeHtml(state.reconciliationPaymentMethod || "PAD")}</strong></div>`,
    `<div class="info-tile"><span>Total Rows</span><strong>${stats.total_rows ?? rows.length}</strong></div>`,
    `<div class="info-tile"><span>Manual Review</span><strong>${stats.manual_review_rows ?? 0}</strong></div>`,
    `<div class="info-tile"><span>Rejected</span><strong>${stats.rejected_rows ?? 0}</strong></div>`,
    `<div class="info-tile"><span>Expected</span><strong>${money(finance.expected_amount || 0)}</strong></div>`,
    `<div class="info-tile"><span>CSV Total</span><strong>${money(finance.csv_amount || 0)}</strong></div>`,
    `<div class="info-tile"><span>Verified</span><strong>${money(finance.verified_amount || 0)}</strong></div>`,
    `<div class="info-tile"><span>Difference</span><strong>${money(finance.difference || 0)}</strong></div>`,
    `<div class="info-tile"><span>Matched Students</span><strong>${students.matched_students ?? 0}</strong></div>`,
    `<div class="info-tile"><span>Outstanding Students</span><strong>${students.outstanding_students ?? 0}</strong></div>`,
  ].join("");
  qs("#reconReadyCount").textContent = `${verifiedCount} verified`;
  qs("#applyVerifiedRows").disabled = verifiedCount === 0;
  qs("#downloadExceptionReport").disabled = !rows.length;
  qs("#selectRosterUpdates").disabled = !rows.length;
  qs("#verifyEligibleRows").disabled = !rows.length;

  if (!rows.length) {
    qs("#reconciliationTable").innerHTML = `<tbody><tr><td class="empty-state">Upload a bank or credit-card CSV to preview matches.</td></tr></tbody>`;
    return;
  }
  const body = rows.map((row, index) => {
    const best = row.best_match || {};
    const confidence = best.confidence || "low";
    const warnings = [...(row.warnings || [])];
    if (Number(row.amount || 0) <= 0) warnings.push("Zero amount: not postable");
    const reasons = [...(best.reasons || []), ...warnings].join("; ") || "No strong matching reason yet";
    const options = reconciliationStudentOptions(row);
    const selectedStudentId = row.selected_student_id || (confidence === "high" ? best.student_id : "");
    const selectedCandidate = selectedStudentId ? (options.find((candidate) => String(candidate.student_id) === String(selectedStudentId)) || best) : {};
    const showCreateStudent = reconciliationCanCreateStudent(row, options);
    const monthValue = row.selected_month || row.month_label || "";
    const blocked = row.excluded || warnings.length || selectedCandidate?.already_paid || row.rejected || Number(row.amount || 0) <= 0;
    const buttonClass = row.verified ? "verify-action verified" : blocked ? "verify-action blocked" : "verify-action needs-review";
    const buttonLabel = row.excluded ? "Excluded" : row.verified ? (row.manually_verified ? "Verified Now to ADD" : "Verified to ADD") : blocked ? "Manual Review Required" : "Verify and Correct";
    return [
      row.date,
      escapeHtml(row.description),
      `<input data-recon-student-name="${index}" value="${escapeHtml(row.corrected_student_name ?? row.student_name ?? "")}" placeholder="CSV student">`,
      `<input data-recon-parent-name="${index}" value="${escapeHtml(row.corrected_parent_name ?? row.parent_name ?? row.description ?? "")}" placeholder="CSV parent/payor">`,
      money(row.amount),
      escapeHtml(row.source || "-"),
      `<input data-recon-search="${index}" value="${escapeHtml(row.student_search || "")}" placeholder="Search roster student">`,
      `<select data-recon-student="${index}">
        <option value="">Select student for posting</option>
        ${options.map((candidate) => `<option value="${candidate.student_id}" ${String(candidate.student_id) === String(selectedStudentId) ? "selected" : ""}>${escapeHtml(candidate.student_name)} - ${escapeHtml(candidate.parent_guardian || "No guardian")} - ${candidate.score || "manual"}%</option>`).join("")}
      </select>`,
      escapeHtml(selectedCandidate?.parent_guardian || "-"),
      escapeHtml(selectedCandidate?.payment_method || "PAD"),
      money(selectedCandidate?.expected_fee || 0),
      `<select data-recon-month="${index}">${state.months.map((month) => `<option value="${month}" ${month === monthValue ? "selected" : ""}>${month}</option>`).join("")}</select>`,
      selectedCandidate?.previous_month ? `${selectedCandidate.previous_month}: ${money(selectedCandidate.previous_paid || 0)} / Current: ${money(selectedCandidate.current_paid || 0)}` : "-",
      `<span class="confidence ${confidence}">${confidence}</span>`,
      `<span class="muted-note">${escapeHtml(reasons)}</span>`,
      `<label class="compact-check"><input type="checkbox" data-roster-update="${index}" ${row.roster_update_approved ? "checked" : ""}> Update roster names</label>`,
      `<div class="row-actions"><button class="${buttonClass}" data-verify-recon="${index}" ${row.excluded ? "disabled" : ""}>${buttonLabel}</button>${showCreateStudent ? `<button class="ghost" data-create-recon-student="${index}" type="button">Create Student</button>` : ""}<button class="ghost danger" data-exclude-recon="${index}" type="button">${row.excluded ? "Restore" : "Delete Row"}</button></div>`,
    ];
  });
  renderTable(qs("#reconciliationTable"), ["CSV Date", "CSV Description", "CSV Student", "CSV Parent / Payor", "CSV Amount", "CSV Source", "Find Student", "Suggested Student", "Parent / Guardian", "Pay Method", "Expected Fee", "Fee Month", "Previous / Current", "Confidence", "Match Reason", "Roster Correction", "Validation"], body);
  document.querySelectorAll("[data-recon-student]").forEach((select) => select.addEventListener("change", () => updateReconSelection(Number(select.dataset.reconStudent))));
  document.querySelectorAll("[data-recon-search]").forEach((input) => input.addEventListener("input", () => updateReconSearch(Number(input.dataset.reconSearch))));
  document.querySelectorAll("[data-recon-month]").forEach((select) => select.addEventListener("change", () => updateReconSelection(Number(select.dataset.reconMonth))));
  document.querySelectorAll("[data-recon-student-name]").forEach((input) => input.addEventListener("input", () => updateReconCorrection(Number(input.dataset.reconStudentName))));
  document.querySelectorAll("[data-recon-parent-name]").forEach((input) => input.addEventListener("input", () => updateReconCorrection(Number(input.dataset.reconParentName))));
  document.querySelectorAll("[data-roster-update]").forEach((input) => input.addEventListener("change", () => updateReconCorrection(Number(input.dataset.rosterUpdate))));
  document.querySelectorAll("[data-create-recon-student]").forEach((button) => button.addEventListener("click", () => createStudentFromReconciliationRow(Number(button.dataset.createReconStudent))));
  document.querySelectorAll("[data-exclude-recon]").forEach((button) => button.addEventListener("click", () => toggleExcludeReconciliationRow(Number(button.dataset.excludeRecon))));
  document.querySelectorAll("[data-verify-recon]").forEach((button) => button.addEventListener("click", () => verifyReconciliationRow(Number(button.dataset.verifyRecon))));
}

function renderTable(table, headers, rows, options = {}) {
  if (!table) return;
  const safeRows = rows || [];
  const bodyRows = safeRows.map((row, index) => `<tr class="${options.footerLast && index === safeRows.length - 1 ? "totals-row" : ""}">${(row || []).map((v) => `<td>${v ?? ""}</td>`).join("")}</tr>`).join("");
  table.innerHTML = `
    <thead><tr>${(headers || []).map((h) => `<th>${h}</th>`).join("")}</tr></thead>
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
  setScheduleRows(student.schedules || []);
  qs("#formTitle").textContent = "Modify Student";
}

async function deleteStudent(id) {
  if (!confirm("Are you sure you want to permanently delete this student record?")) return;
  await api(`/api/students/${id}`, { method: "DELETE" });
  toast("Student record has been successfully deleted.");
  closeProfile();
  await load();
}

async function restoreStudent(id) {
  if (!confirm("Restore this student record to active roster views?")) return;
  await api(`/api/students/${id}/restore`, { method: "PUT" });
  toast("Student record restored");
  await load();
}

function clearStudentForm() {
  qs("#studentForm").reset();
  qs("#studentForm").elements.id.value = "";
  setSelectedSubjects("");
  setScheduleRows([]);
  qs("#formTitle").textContent = "Add Student";
  qs("#studentForm").classList.add("collapsed");
}

function showAddStudentForm() {
  qs("#studentForm").reset();
  qs("#studentForm").elements.id.value = "";
  closeWorkflow();
  setSelectedSubjects("");
  setScheduleRows([]);
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
  
  if (window.supabase && supabaseClient) {
    try {
      await supabaseClient.auth.signInWithOtp({
        email: data.email,
        options: { emailRedirectTo: window.location.origin }
      });
      toast("User access saved and invite email sent!");
    } catch (err) {
      console.warn("Could not send invite via Supabase:", err);
      toast("User access saved (could not send invite link)");
    }
  } else {
    toast("User access saved");
  }
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

async function seedDemoData() {
  if (!confirm("Seed demo data for this organization? Use this only for testing or a new evaluation branch.")) return;
  const result = await api("/api/demo/seed", { method: "POST", body: JSON.stringify({}) });
  toast(`Demo data ready: ${result.students_created || 0} students, ${result.staff_created || 0} staff`);
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
  state.reconciliationPaymentMethod = qs("#reconPaymentMethod")?.value || "PAD";
  state.reconciliationMatchRules = [...document.querySelectorAll("[data-recon-rule]:checked")].map((node) => node.dataset.reconRule);
  if (!state.reconciliationMatchRules.length) state.reconciliationMatchRules = [...DEFAULT_RECON_RULES];
  const parsedPayment = paymentCsvRows(await readCsvFile(file));
  const rows = parsedPayment.rows;
  state.reconciliationSkippedZeroRows = parsedPayment.skippedZeroRows || 0;
  if (!rows.length) {
    toast(`No payable rows found. ${state.reconciliationSkippedZeroRows} zero-amount row${state.reconciliationSkippedZeroRows === 1 ? "" : "s"} skipped.`);
    return;
  }
  const result = await api("/api/reconciliation/preview", {
    method: "POST",
    body: JSON.stringify({
      rows,
      file_name: file.name,
      payment_method: state.reconciliationPaymentMethod,
      match_rules: state.reconciliationMatchRules,
    }),
  });
  state.reconciliationSummary = result.summary || null;
  state.reconciliationPreview = (result.rows || []).map((row) => ({
    ...row,
    selected_student_id: row.best_match?.confidence === "high" ? row.best_match?.student_id || "" : "",
    selected_month: row.month_label || "",
    corrected_student_name: row.student_name || "",
    corrected_parent_name: row.parent_name || row.description || "",
    roster_update_approved: false,
    excluded: false,
    verified: Number(row.amount || 0) > 0 && row.best_match?.confidence === "high" && !(row.warnings || []).length && !row.best_match?.already_paid,
    manually_verified: false,
  }));
  saveReconciliationSession();
  renderReconciliation();
  toast(`${parsedPayment.totalRows} rows found. ${rows.length} payment row${rows.length === 1 ? "" : "s"} loaded. ${state.reconciliationSkippedZeroRows} zero-amount row${state.reconciliationSkippedZeroRows === 1 ? "" : "s"} skipped.`);
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
  saveReconciliationSession();
  renderReconciliation();
}

function updateReconSearch(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  row.student_search = qs(`[data-recon-search="${index}"]`)?.value || "";
  row.verified = false;
  row.manually_verified = false;
  saveReconciliationSession();
  clearTimeout(reconSearchRenderTimer);
  reconSearchRenderTimer = setTimeout(renderReconciliation, 450);
}

function updateReconCorrection(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  row.corrected_student_name = qs(`[data-recon-student-name="${index}"]`)?.value || "";
  row.corrected_parent_name = qs(`[data-recon-parent-name="${index}"]`)?.value || "";
  row.roster_update_approved = Boolean(qs(`[data-roster-update="${index}"]`)?.checked);
  row.verified = false;
  row.manually_verified = false;
  saveReconciliationSession();
}

function reconciliationSelectedCandidate(row) {
  const selectedStudentId = row?.selected_student_id || "";
  if (!selectedStudentId) return {};
  return (row.candidates || []).find((item) => String(item.student_id) === String(selectedStudentId)) || {};
}

function reconciliationEligibility(row) {
  if (!row) return { ok: false, reason: "Row was not found" };
  if (row.excluded) return { ok: false, reason: "Excluded row" };
  if (Number(row.amount || 0) <= 0) return { ok: false, reason: "Zero amount rows cannot be posted" };
  if (!row.selected_student_id) return { ok: false, reason: "Student must be selected" };
  if (!(row.selected_month || row.month_label)) return { ok: false, reason: "Fee month must be selected" };
  const candidate = reconciliationSelectedCandidate(row);
  if (!candidate.student_id) return { ok: false, reason: "Selected student is not in the candidate list" };
  if (candidate.payment_method && candidate.payment_method !== state.reconciliationPaymentMethod) {
    return { ok: false, reason: `${state.reconciliationPaymentMethod} upload can only update ${state.reconciliationPaymentMethod} students` };
  }
  if (candidate.already_paid || Number(candidate.current_paid || 0) > 0) {
    return { ok: false, reason: `${row.selected_month || row.month_label} already has a payment for this student` };
  }
  if (row.rejected || (row.warnings || []).length) return { ok: false, reason: "Row has warnings or was rejected" };
  return { ok: true, candidate };
}

function selectAllRosterUpdates() {
  let count = 0;
  (state.reconciliationPreview || []).forEach((row) => {
    if (row.excluded || Number(row.amount || 0) <= 0) return;
    const hasCorrection = String(row.corrected_student_name || row.student_name || "").trim()
      || String(row.corrected_parent_name || row.parent_name || row.description || "").trim();
    if (!hasCorrection) return;
    row.roster_update_approved = true;
    count += 1;
  });
  saveReconciliationSession();
  renderReconciliation();
  toast(`${count} roster correction checkbox${count === 1 ? "" : "es"} selected`);
}

function verifyAllEligibleRows() {
  let verified = 0;
  let skipped = 0;
  (state.reconciliationPreview || []).forEach((row) => {
    const eligibility = reconciliationEligibility(row);
    if (!eligibility.ok) {
      skipped += 1;
      return;
    }
    row.verified = true;
    row.manually_verified = true;
    verified += 1;
  });
  saveReconciliationSession();
  renderReconciliation();
  toast(`${verified} row${verified === 1 ? "" : "s"} verified. ${skipped} skipped for review.`);
}

function reconciliationDateToIso(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const direct = new Date(text);
  if (!Number.isNaN(direct.getTime())) return direct.toISOString().slice(0, 10);
  const match = text.match(/^(\d{4})-([A-Za-z]{3})-(\d{1,2})$/);
  if (!match) return "";
  const months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  const monthIndex = months.indexOf(match[2].toLowerCase());
  if (monthIndex < 0) return "";
  return `${match[1]}-${String(monthIndex + 1).padStart(2, "0")}-${String(Number(match[3])).padStart(2, "0")}`;
}

async function createStudentFromReconciliationRow(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  updateReconCorrection(index);
  const studentName = String(row.corrected_student_name || row.student_name || "").trim();
  const parentName = String(row.corrected_parent_name || row.parent_name || row.description || "").trim();
  if (!studentName) {
    toast("Enter the student name before creating a roster record");
    return;
  }
  if (!confirm(`Create new active Student Roster record for ${studentName}?`)) return;
  const created = await api("/api/students", {
    method: "POST",
    body: JSON.stringify({
      student_name: studentName,
      parent_guardian: parentName,
      status: "C",
      enrol_date: reconciliationDateToIso(row.date),
      subjects: [],
      rate_type: "Regular",
      std_monthly_fee: Number(row.amount || 0),
      payment_method: state.reconciliationPaymentMethod || row.payment_method || "PAD",
      phone: "",
      email: row.email || "",
      siblings: "",
      notes: `Created from ${state.reconciliationPaymentMethod || "payment"} reconciliation`,
      schedules: [],
    }),
  });
  const candidate = {
    student_id: created.id,
    student_name: studentName,
    parent_guardian: parentName,
    payment_method: state.reconciliationPaymentMethod || row.payment_method || "PAD",
    expected_fee: Number(row.amount || 0),
    score: 100,
    confidence: "high",
    current_paid: 0,
    already_paid: false,
    reasons: ["created from reconciliation row"],
  };
  row.candidates = [candidate, ...(row.candidates || []).filter((item) => String(item.student_id) !== String(created.id))];
  row.best_match = candidate;
  row.selected_student_id = String(created.id);
  row.selected_month = row.selected_month || row.month_label || "";
  row.roster_update_approved = false;
  row.verified = false;
  row.manually_verified = false;
  saveReconciliationSession();
  renderReconciliation();
  toast(`Student created and selected for ${studentName}`);
}

function toggleExcludeReconciliationRow(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  row.excluded = !row.excluded;
  row.verified = false;
  row.manually_verified = false;
  saveReconciliationSession();
  renderReconciliation();
  toast(row.excluded ? "Row excluded from posting" : "Row restored to review queue");
}

function downloadExceptionReport() {
  const rows = (state.reconciliationPreview || []).filter((row) => !row.verified || row.rejected || (row.warnings || []).length);
  const headers = ["status", "date", "description", "amount", "source", "suggested_student", "parent_guardian", "month", "confidence", "reason"];
  const csvRows = rows.map((row) => {
    const candidate = row.best_match || {};
    const status = row.rejected ? "Rejected" : (row.warnings || []).length ? "Manual Review" : "Outstanding / Not Verified";
    return {
      status,
      date: row.date || "",
      description: row.description || "",
      amount: row.amount || 0,
      source: row.source || "",
      suggested_student: candidate.student_name || "",
      parent_guardian: candidate.parent_guardian || "",
      month: row.month_label || "",
      confidence: candidate.confidence || "",
      reason: [...(candidate.reasons || []), ...(row.warnings || [])].join("; "),
    };
  });
  const escapeCsv = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const csv = [headers.join(","), ...csvRows.map((row) => headers.map((header) => escapeCsv(row[header])).join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `smp-reconciliation-exceptions-${state.reconciliationPaymentMethod || "payment"}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function verifyReconciliationRow(index) {
  const row = state.reconciliationPreview[index];
  if (!row) return;
  updateReconCorrection(index);
  const selectedStudentId = qs(`[data-recon-student="${index}"]`)?.value || row.selected_student_id || "";
  const selectedMonth = qs(`[data-recon-month="${index}"]`)?.value || row.selected_month || row.month_label || "";
  row.selected_student_id = selectedStudentId;
  row.selected_month = selectedMonth;
  const eligibility = reconciliationEligibility(row);
  if (!eligibility.ok) {
    toast(eligibility.reason);
    return;
  }
  row.verified = true;
  row.manually_verified = true;
  saveReconciliationSession();
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
  const selectedStudentId = row.selected_student_id || "";
  const selectedMonth = row.selected_month || row.month_label || "";
  const candidate = (row.candidates || []).find((item) => String(item.student_id) === String(selectedStudentId)) || {};
  if (!selectedStudentId || !selectedMonth || !row.verified) {
    throw new Error("Verify each row before adding");
  }
  const eligibility = reconciliationEligibility(row);
  if (!eligibility.ok) throw new Error(eligibility.reason);
  await api("/api/reconciliation/apply", {
    method: "POST",
    body: JSON.stringify({
      ...row,
      student_id: selectedStudentId,
      month_label: selectedMonth,
      score: candidate.score || 0,
      notes: (candidate.reasons || []).join("; "),
      file_name: state.reconciliationFileName,
      payment_method: state.reconciliationPaymentMethod,
      match_rules: state.reconciliationMatchRules,
      corrected_student_name: row.corrected_student_name || "",
      corrected_parent_name: row.corrected_parent_name || "",
      roster_update_approved: Boolean(row.roster_update_approved),
    }),
  });
}

async function applyVerifiedRows() {
  const verifiedRows = (state.reconciliationPreview || [])
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => !row.excluded && Number(row.amount || 0) > 0 && row.verified && row.selected_student_id && (row.selected_month || row.month_label));
  if (!verifiedRows.length) {
    toast("No verified payment rows are ready");
    return;
  }
  const excludedCount = (state.reconciliationPreview || []).filter((row) => row.excluded).length;
  const zeroCount = (state.reconciliationPreview || []).filter((row) => Number(row.amount || 0) <= 0 && !row.excluded).length;
  const reviewCount = (state.reconciliationPreview || []).filter((row) => !row.excluded && !row.verified).length;
  const postTotal = verifiedRows.reduce((sum, { row }) => sum + Number(row.amount || 0), 0);
  const rosterUpdates = verifiedRows.filter(({ row }) => row.roster_update_approved).length;
  const summary = [
    `Verified rows to post: ${verifiedRows.length}`,
    `Total to post: ${money(postTotal)}`,
    `Rows still needing review: ${reviewCount}`,
    `Excluded/deleted rows: ${excludedCount}`,
    `Zero amount review rows: ${zeroCount}`,
    `Roster name corrections approved: ${rosterUpdates}`,
    "",
    "Post only after the bank total and selected students are correct."
  ].join("\n");
  if (!confirm(summary)) return;
  if (!confirm("Final approval: post verified payments to Fee Tracker and save approved roster corrections?")) return;
  const remaining = [];
  for (const row of state.reconciliationPreview) {
    if (!row.excluded && Number(row.amount || 0) > 0 && row.verified) await postReconciliationRow(row);
    else remaining.push(row);
  }
  toast("Verified payments added to Fee Tracker");
  await load();
  state.reconciliationPreview = remaining;
  saveReconciliationSession();
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

qs("#choiceStudentAdmin")?.addEventListener("click", () => switchAdminArea("student"));
qs("#choiceStaffAdmin")?.addEventListener("click", () => switchAdminArea("staff"));
qs("#switchModule")?.addEventListener("click", () => switchAdminArea("choice"));
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
  qs("#presenceDayFilter")?.addEventListener("change", renderPresence);
  document.querySelectorAll("#presenceSubtabs .subtab").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activePresenceSubTab = btn.dataset.day;
      renderPresence();
    });
  });
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

document.querySelectorAll("#settingsSubtabs .subtab").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.activeSettingsSubTab = btn.dataset.settingsTab;
    renderSettings();
  });
});

qs("#plMonth")?.addEventListener("change", (event) => {
  state.activePlMonth = event.target.value;
  renderPlTab();
});

qs("#plForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const activeMonth = state.activePlMonth || state.settings.current_month || "May-26";
  const rent = parseFloat(qs("#plRent").value) || 0;
  const royalty = parseFloat(qs("#plRoyalty").value) || 0;
  const utilities = parseFloat(qs("#plUtilities").value) || 0;
  const misc = parseFloat(qs("#plMisc").value) || 0;
  const miscDetails = qs("#plMiscDetails").value || "";

  try {
    await api("/api/expenses", {
      method: "POST",
      body: JSON.stringify({
        month_label: activeMonth,
        rent_expense: rent,
        royalty_expense: royalty,
        utilities_expense: utilities,
        misc_expense: misc,
        misc_details: miscDetails
      })
    });
    toast(`Saved P&L record for ${activeMonth}`);
    await load();
  } catch (err) {
    toast(`Failed to save: ${err.message}`);
  }
});

qs("#studentForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formData(event.currentTarget);
  data.subjects = selectedSubjectValues();
  data.schedules = collectScheduleRows();
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
  pushSettingsToStaffbase();
});

qs("#centreSetupForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formData(event.currentTarget);
  try {
    const res = await api("/api/centre-setup", {
      method: "POST",
      body: JSON.stringify({
        organization_name: data.organization_name,
        branch_name: data.branch_name,
        branch_code: data.branch_code,
      })
    });
    if (res.ok) {
      toast("Centre configured successfully!");
      await load();
      renderAll();
      switchAdminArea("student");
      pushSettingsToStaffbase();
    } else {
      toast(res.error || "Failed to configure centre.");
    }
  } catch (e) {
    toast("Error configuring centre. Please try again.");
    console.error(e);
  }
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

qs("#delegationForm")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const userId = form.elements.delegation_user_id.value;
  const role = form.elements.delegation_role.value;
  if (!userId || !role) { toast('Please select a user and a role to delegate.', 'err'); return; }
  grantDelegation(userId, role);
  form.reset();
});
qs('#settingsForm [name="institution_name"]').addEventListener("change", applyInstitutionDefaults);
qs('#settingsForm [name="institution_name"]').addEventListener("blur", applyInstitutionDefaults);
qs("#restoreBackup").addEventListener("click", restoreSelectedBackup);
qs("#seedDemoData")?.addEventListener("click", seedDemoData);
qs("#feeImportCsv").addEventListener("change", handleFeeImportCsv);
qs("#applyFeeImport").addEventListener("click", applyFeeImport);
qs("#paymentCsv").addEventListener("change", handlePaymentUpload);
qs("#reconPaymentMethod")?.addEventListener("change", (event) => {
  state.reconciliationPaymentMethod = event.target.value;
  clearReconciliationSession();
  renderReconciliation();
});
qs("#batchCsv").addEventListener("change", handleBatchCsv);
qs("#applyBatchImport").addEventListener("click", applyBatchImport);
qs("#applyVerifiedRows").addEventListener("click", applyVerifiedRows);
qs("#selectRosterUpdates")?.addEventListener("click", selectAllRosterUpdates);
qs("#verifyEligibleRows")?.addEventListener("click", verifyAllEligibleRows);
function downloadReconTemplate() {
  const method = qs("#reconPaymentMethod")?.value || "PAD";
  let headers = [];
  let sampleRow = [];
  
  if (method === "PAD") {
    headers = ["Date", "Description", "Amount", "Reference", "Student ID", "Student Name", "Parent Name", "Email"];
    sampleRow = ["2026-06-01", "KUMON PAD DIRECT DEBIT", "165.00", "REF10001", "1024", "John Smith", "Mary Smith", "mary.smith@example.com"];
  } else if (method === "E-Transfer") {
    headers = ["Date", "Description", "Amount", "Reference", "Parent Name", "Email"];
    sampleRow = ["2026-06-01", "E-TRANSFER FROM JOHN SMITH", "165.00", "ETR55621", "John Smith", "john.smith@example.com"];
  } else {
    headers = ["Date", "Description", "Amount", "Reference", "Student Name", "Parent Name"];
    sampleRow = ["2026-06-01", "CREDIT CARD PAYMENT", "165.00", "TXN88921", "John Smith", "Mary Smith"];
  }
  
  const csvContent = [
    headers.join(","),
    sampleRow.map(val => `"${String(val).replace(/"/g, '""')}"`).join(",")
  ].join("\n");
  
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", `smp-reconciliation-template-${method.toLowerCase().replace(/\s+/g, '-')}.csv`);
  link.style.visibility = 'hidden';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

qs("#downloadExceptionReport").addEventListener("click", downloadExceptionReport);
qs("#downloadReconTemplate")?.addEventListener("click", downloadReconTemplate);
qs("#authForm")?.addEventListener("submit", sendMagicLink);
qs("#passwordLogin")?.addEventListener("click", signInWithPassword);
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

// ── StaffBase ↔ SMP two-way bridge ──────────────────────────────────────────
// Converts sb_users (staffbase format) → state.staff.members (app.js format).
function sbUsersToStaffMembers(sbUsers) {
  if (!Array.isArray(sbUsers)) return [];
  return sbUsers
    .filter(u => u.active !== false)
    .map(u => ({
      id: String(u.id),
      staff_name: u.name || '',
      email: u.email || '',
      role_title: u.pos || u.role || '',
      subject: u.dept || '',
      pin: u.pin || '',
      phone: u.phone || '',
      hourly_rate: Number(u.hourly_rate) || 0,
      active: u.active !== false,
    }));
}

// Converts sb_schedule (staffbase format) → state.staff.schedules (app.js format).
function sbScheduleToStaffSchedules(sbSchedule) {
  if (!sbSchedule?.shifts) return [];
  const schedules = [];
  for (const [uid, dayMap] of Object.entries(sbSchedule.shifts)) {
    for (const [day, shift] of Object.entries(dayMap || {})) {
      if (!shift || shift.type === 'Off') continue;
      schedules.push({
        id: `sb-${uid}-${day}`,
        staff_id: String(uid),
        weekday: day,
        shift_type: shift.type,
        start_time: shift.start || '',
        end_time: shift.end || '',
        location: shift.location || '',
        published: sbSchedule.published || false,
      });
    }
  }
  return schedules;
}

// Converts sb_clock_data (staffbase format) → state.staff.punches (app.js format).
function sbClockToStaffPunches(sbClockData) {
  if (!sbClockData || typeof sbClockData !== 'object') return [];
  return Object.entries(sbClockData)
    .filter(([, punch]) => punch?.in)
    .map(([uid, punch]) => {
      const member = (state.staff?.members || []).find(m => String(m.id) === String(uid));
      return {
        id: `sb-punch-${uid}`,
        staff_id: String(uid),
        staff_name: member?.staff_name || '',
        role_title: member?.role_title || '',
        punch_date: new Date().toISOString().slice(0, 10),
        clock_in: punch.in,
        clock_out: punch.out || '',
        duration_hours: 0,
        source: 'StaffBase',
        notes: '',
      };
    });
}

// Pushes current school settings into the staffbase iframe so it stays in sync
// when the manager updates subjects, operating hours, or the school name.
function pushSettingsToStaffbase() {
  const frame = qs("#staffbaseFrame");
  if (!frame?.contentWindow || frame.getAttribute("src") === "about:blank") return;
  const subjects = (state.settings?.subjects_offered || "")
    .split(",").map(s => s.trim()).filter(Boolean);
  const weekdays = PRESENCE_WEEKDAYS.map(day => day.substring(0, 3));
  
  const orgName = state.settings?.institution_name || "";
  const branchName = state.settings?.branch_name || "";
  const schoolName = branchName ? (orgName + " - " + branchName) : orgName;

  const elStudents = (state.students || [])
    .filter(s => !s.deleted_at)
    .map(s => ({
      student_name: s.student_name,
      rate_type: s.rate_type || 'R',
      status: s.status,
      schedules: (s.schedules || []).map(sc => ({
        weekday: sc.weekday,
        start: sc.start_time,
        end: sc.end_time,
      })),
    }));
  
  frame.contentWindow.postMessage({
    type: 'smp:settingsChanged',
    school_name: schoolName,
    subjects,
    weekdays,
    operating_start: state.settings?.operating_start || '15:00',
    operating_end: state.settings?.operating_end || '20:00',
    support_email: state.settings?.support_email || 'support@smp.edu',
    el_students: elStudents,
  }, '*');
}

window.addEventListener("message", (event) => {
  if (event.data?.type === "logout") {
    signOut();
    return;
  }

  // staffbase.html notifies parent whenever it saves data — sync into state.staff
  // so the Staff Administration views in index.html reflect the latest changes.
  if (event.data?.type === "sb:dataChanged") {
    const { key, data } = event.data;
    if (!state.staff) state.staff = { members: [], schedules: [], punches: [], weekdays: [] };

    if (key === "sb_users") {
      state.staff.members = sbUsersToStaffMembers(data);
      renderStaffAdministration();
    } else if (key === "sb_schedule") {
      state.staff.schedules = sbScheduleToStaffSchedules(data);
      if (data?.openDays?.length) state.staff.weekdays = data.openDays;
      renderStaffAdministration();
    } else if (key === "sb_clock_data") {
      state.staff.punches = sbClockToStaffPunches(data);
      renderStaffAdministration();
    }
  }
});

(async function start() {
  const ready = await initAuth();
  if (ready) await load();
  if (supabaseClient) {
    supabaseClient.auth.onAuthStateChange(async (_event, session) => {
      authSession = session;
      renderAuthState();
      if (session) {
        document.cookie = `access_token=${session.access_token}; path=/; max-age=3600; SameSite=Lax`;
        await load();
      } else {
        document.cookie = "access_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
      }
    });
  }
})().catch((error) => toast(error.message));

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then(reg => console.log('Service Worker registered', reg))
      .catch(err => console.error('Service Worker registration failed', err));
  });
}
