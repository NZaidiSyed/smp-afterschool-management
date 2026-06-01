const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = 3000;
const DB_PATH = path.join(__dirname, 'staffbase-database.json');

app.use(cors());
app.use(express.json());

// ─── INITIAL DATASETS ────────────────────────────────────────────────────────
const DEFAULT_SCHOOL = { lat: 43.7615, lng: -79.4111, radius: 300, name: 'Greenfield Academy', address: '150 Greenfield Ave, Toronto, ON M6G 3G7', website: 'www.greenfieldacademy.edu', email: 'admin@greenfield.edu', otThreshold: 8.0, openDays: ['Mon','Tue','Wed','Thu','Fri'] };
const INITIAL_USERS = [
  { id:1, name:'Dr. Sarah Mitchell', email:'s.mitchell@greenfield.edu', role:'principal_owner', dept:'Administration', pin:'0000', password:'school2026', phone:'416-555-0001', pos:'Principal', av:'SM', active:true },
  { id:2, name:'James Harrington',   email:'j.harrington@greenfield.edu', role:'administrator', dept:'Administration', pin:'1111', password:'school2026', phone:'416-555-0002', pos:'Vice Principal', av:'JH', active:true },
  { id:3, name:'Rachel Torres',      email:'r.torres@greenfield.edu', role:'office_manager', dept:'Administration', pin:'2222', password:'school2026', phone:'416-555-0003', pos:'Office Manager', av:'RT', active:true },
  { id:4, name:'Emily Chen',         email:'e.chen@greenfield.edu', role:'teaching_assistant', dept:'Mathematics', pin:'3333', password:'', phone:'416-555-0011', pos:'Math Teacher', av:'EC', active:true },
  { id:5, name:'Marcus Williams',    email:'m.williams@greenfield.edu', role:'teaching_assistant', dept:'Science', pin:'4444', password:'', phone:'416-555-0012', pos:'Science Teacher', av:'MW', active:true },
  { id:6, name:'Priya Sharma',       email:'p.sharma@greenfield.edu', role:'grader', dept:'English', pin:'5555', password:'', phone:'416-555-0013', pos:'English Teacher', av:'PS', active:true },
  { id:7, name:"David O'Brien",     email:'d.obrien@greenfield.edu', role:'teaching_assistant', dept:'History', pin:'6666', password:'', phone:'416-555-0014', pos:'History Teacher', av:'DO', active:true },
  { id:8, name:'Aisha Patel',        email:'a.patel@greenfield.edu', role:'grader', dept:'Arts', pin:'7777', password:'', phone:'416-555-0015', pos:'Art Teacher', av:'AP', active:true },
  { id:9, name:'Tom Nakamura',       email:'t.nakamura@greenfield.edu', role:'teaching_assistant', dept:'PE', pin:'8888', password:'', phone:'416-555-0016', pos:'PE Teacher', av:'TN', active:true },
  { id:10,name:'Lisa Kowalski',      email:'l.kowalski@greenfield.edu', role:'grader', dept:'Support', pin:'9999', password:'', phone:'416-555-0017', pos:'Counselor', av:'LK', active:true },
];
const DEFAULT_SUBJECTS = ['Mathematics', 'Science', 'English', 'History', 'Arts', 'PE', 'Administration', 'Support'];

function buildDefaultSchedule() {
  const patterns = {
    4: ['Teaching','Planning','Teaching','Teaching','Teaching'],
    5: ['Teaching','Teaching','Supervision','Teaching','Meeting'],
    6: ['Planning','Teaching','Teaching','Teaching','Teaching'],
    7: ['Teaching','Teaching','Meeting','Teaching','Supervision'],
    8: ['Teaching','Supervision','Teaching','Teaching','Teaching'],
    9: ['Prof Dev','Teaching','Teaching','Teaching','Planning'],
    10:['Teaching','Teaching','Planning','Meeting','Teaching'],
    3: ['Meeting','Teaching','Planning','Teaching','Supervision'],
  };
  const shifts = {};
  const DAYS = ['Mon','Tue','Wed','Thu','Fri'];
  for (const uid in patterns) {
    shifts[uid] = {};
    DAYS.forEach((d, i) => {
      const type = patterns[uid][i % patterns[uid].length] || 'Off';
      const locs = { Teaching: `Room ${100 + parseInt(uid)}`, Meeting: 'Staff Room', Supervision: 'Main Hallway', Planning: 'Office', 'Prof Dev': 'Off-site' };
      shifts[uid][d] = { type, start: type === 'Off' ? null : '15:30', end: type === 'Off' ? null : '18:30', location: locs[type] || 'Office', notes: '', ack: false };
    });
  }
  return shifts;
}

const DEFAULT_DB = {
  school: DEFAULT_SCHOOL,
  users: INITIAL_USERS,
  subjects: DEFAULT_SUBJECTS,
  schedule: {
    published: true,
    publishedAt: 'May 26, 2026 at 08:45 AM',
    week: 'May 26 – 30, 2026',
    weekKey: '2026-W22',
    shifts: buildDefaultSchedule(),
  },
  requests: [
    { id:1, uid:4, type:'Vacation', dates:'Jun 3–5', days:3, note:'Family trip', status:'pending', submitted:'May 27' },
    { id:2, uid:8, type:'Sick', dates:'May 30', days:1, note:'', status:'approved', submitted:'May 29' },
    { id:3, uid:6, type:'Professional Development', dates:'Jun 5', days:1, note:'Literacy conference', status:'pending', submitted:'May 28' },
    { id:4, uid:9, type:'Personal', dates:'Jun 2', days:1, note:'Medical appointment', status:'denied', submitted:'May 26' },
  ],
  announcements: [
    { id:1, from:1, title:'End-of-year assembly — all staff required', body:'Please note the all-staff assembly on Friday June 14 at 9:00 AM in the gymnasium. Attendance is mandatory for all teaching and support staff. Agenda will be circulated by June 10.', date:'May 29', priority:'high' },
    { id:2, from:2, title:'Schedule published for week of May 26', body:'Your schedule for the week of May 26 has been published. Please log in to review your shifts and acknowledge each day. Contact the office if you have any concerns.', date:'May 28', priority:'normal' },
    { id:3, from:3, title:'Reminder: report card submissions due June 7', body:'All report card submissions must be completed by end of day Friday June 7. Please contact the main office if you require an extension. Late submissions will be flagged.', date:'May 27', priority:'normal' },
  ],
  messages: [
    { id: 1, from: 2, to: 4, body: "Hi Emily, could you cover the afternoon Mathematics session tomorrow?", date: "May 28 at 2:30 PM", read: true },
    { id: 2, from: 4, to: 2, body: "Sure James, I'd be happy to cover that shift. What is the start time?", date: "May 28 at 2:45 PM", read: true },
    { id: 3, from: 2, to: 4, body: "Standard 3:30 PM start is perfect. Thanks so much!", date: "May 28 at 2:50 PM", read: true }
  ],
  clock_data: {},
  checkin_log: []
};

// ─── FILE persistence ENGINE ────────────────────────────────────────────────
function readDb() {
  try {
    if (!fs.existsSync(DB_PATH)) {
      writeDb(DEFAULT_DB);
      return DEFAULT_DB;
    }
    const raw = fs.readFileSync(DB_PATH, 'utf8');
    return JSON.parse(raw);
  } catch (e) {
    console.error("Database read error, falling back to empty state:", e);
    return DEFAULT_DB;
  }
}

function writeDb(data) {
  try {
    fs.writeFileSync(DB_PATH, JSON.stringify(data, null, 2), 'utf8');
  } catch (e) {
    console.error("Database write error:", e);
  }
}

// ─── ROUTES ──────────────────────────────────────────────────────────────────
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', serverTime: new Date().toISOString() });
});

app.get('/api/data', (req, res) => {
  res.json(readDb());
});

app.post('/api/users', (req, res) => {
  const db = readDb();
  db.users = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/school', (req, res) => {
  const db = readDb();
  db.school = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/subjects', (req, res) => {
  const db = readDb();
  db.subjects = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/schedule', (req, res) => {
  const db = readDb();
  db.schedule = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/requests', (req, res) => {
  const db = readDb();
  db.requests = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/announcements', (req, res) => {
  const db = readDb();
  db.announcements = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/messages', (req, res) => {
  const db = readDb();
  db.messages = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/clock_data', (req, res) => {
  const db = readDb();
  db.clock_data = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.post('/api/checkin_log', (req, res) => {
  const db = readDb();
  db.checkin_log = req.body;
  writeDb(db);
  res.json({ success: true });
});

app.listen(PORT, () => {
  console.log(`=======================================================`);
  console.log(`  StaffBase Enterprise API Server Active!               `);
  console.log(`  Server Port: http://localhost:${PORT}                  `);
  console.log(`  Database File: ${DB_PATH}                            `);
  console.log(`=======================================================`);
});
