/* Attendance UAT interface. Authentication is shared with the main application. */
(() => {
  const el = id => document.getElementById(id);
  const safe = value => escapeHtml(String(value ?? ''));
  let snapshot = null, busy = false, pending = null, generation = 0;
  try { pending=JSON.parse(sessionStorage.getItem('smp_pending_attendance_scan')||'null'); } catch(_) {}
  function rememberPending() {
    try { if(pending)sessionStorage.setItem('smp_pending_attendance_scan',JSON.stringify(pending));else sessionStorage.removeItem('smp_pending_attendance_scan'); } catch(_) {}
  }
  const statusLabels = {expected:'Expected', absent:'Absent', in_center:'In centre', completed:'Completed', incomplete:'Incomplete', excused:'Excused', cancelled:'Cancelled'};
  const kindLabels = {regular:'Regular', makeup:'Makeup', unscheduled:'Unscheduled'};
  const active = () => ['attendance','student-barcodes'].some(id=>el(id)?.classList.contains('active')) && !document.hidden;
  const clock = value => value ? new Intl.DateTimeFormat('en-CA',{timeZone:snapshot.timezone,hour:'numeric',minute:'2-digit'}).format(new Date(value)) : '—';
  const localInput = value => {
    if (!value) return '';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:snapshot.timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(p=>[p.type,p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  };
  function banner(text, kind='regular') {
    el('attendanceBanner').textContent=text;
    el('attendanceBanner').className=`attendance-banner attendance-${kind}`;
  }
  function setBusy(value) {
    busy=value; el('attendanceScanButton').disabled=value;
    el('attendanceCode').disabled=value;
    el('attendanceRetry').hidden=!pending || value;
  }
  async function refresh() {
    if (!active() || busy || el('attendanceCorrection').open) return;
    const run=++generation;
    const date=el('attendanceDate').value;
    try {
      const data=await api(`/api/attendance${date?'?date='+encodeURIComponent(date):''}`);
      if(run!==generation)return;
      snapshot=data;
      if(!date)el('attendanceDate').value=data.today;
      el('attendanceConnection').textContent=`Updated ${new Intl.DateTimeFormat('en-CA',{timeZone:data.timezone,hour:'numeric',minute:'2-digit',second:'2-digit'}).format(new Date())} · refreshes every 15 seconds while open`;
      el('attendanceConnection').classList.remove('attendance-offline');
      render();
    } catch(error) {
      el('attendanceConnection').textContent=`Attendance unavailable: ${error.message}. No scans are recorded offline.`;
      el('attendanceConnection').classList.add('attendance-offline');
    }
  }
  function render() {
    el('attendanceZone').textContent=`Centre timezone: ${snapshot.timezone} · tracking since ${snapshot.settings.started_on}`;
    const counts={};snapshot.records.forEach(r=>counts[r.status]=(counts[r.status]||0)+1);
    el('attendanceMetrics').innerHTML=['in_center','expected','completed','absent','incomplete'].map(k=>`<div class="metric"><span>${safe(statusLabels[k])}</span><strong>${counts[k]||0}</strong></div>`).join('');
    const filter=el('attendanceFilter').value;
    const rows=snapshot.records.filter(r=>!filter||r.status===filter);
    el('attendanceTable').innerHTML='<thead><tr><th>Student</th><th>Scheduled days</th><th>Arrival</th><th>Departure</th><th>Duration</th><th>Session</th><th>Status</th><th>Review</th></tr></thead><tbody>'+rows.map(r=>{
      const minutes=r.status==='in_center'?Math.max(0,Math.floor((Date.now()-Date.parse(r.check_in))/60000)):r.duration_minutes;
      return `<tr><td>${safe(r.student_name)}</td><td>${safe(r.scheduled_days)}</td><td>${safe(clock(r.check_in))}</td><td>${safe(clock(r.check_out))}</td><td>${minutes==null?'—':`${minutes} min`}</td><td><span class="attendance-badge attendance-${safe(r.session_type)}">${safe(kindLabels[r.session_type])}</span></td><td>${safe(statusLabels[r.status])}${r.makeup_state?' · '+safe(r.makeup_state):''}</td><td>${snapshot.permissions.correct?`<button type="button" data-correct="${safe(r.id)}" class="ghost">Review</button>`:''}<small>${safe(r.note)}</small></td></tr>`;
    }).join('')+(!rows.length?'<tr><td colspan="8">No attendance records for this date.</td></tr>':'')+'</tbody>';
    el('attendanceAbsences').innerHTML=snapshot.absences.length?'<ul>'+snapshot.absences.map(r=>`<li>${safe(r.student_name)} · ${safe(r.day)} · ${safe(r.makeup_state)}</li>`).join('')+'</ul>':'<p>No unfulfilled absences.</p>';
    const chosen=el('attendanceStudent').value;
    el('attendanceStudent').innerHTML='<option value="">Choose a student</option>'+snapshot.students.filter(s=>String(s.status).toUpperCase()==='C').map(s=>`<option value="${safe(s.barcode)}">${safe(s.name)}</option>`).join('');
    el('attendanceStudent').value=chosen;
    el('attendanceSettingsPanel').hidden=!snapshot.permissions.settings;
    const form=el('attendanceSettingsForm');
    if(!form.contains(document.activeElement)) {
      ['timezone','debounce_seconds','max_hours','grace_minutes'].forEach(k=>form.elements[k].value=snapshot.settings[k]);
      form.elements.holidays.value=JSON.parse(snapshot.settings.holidays).join('\n');
    }
    el('attendanceAudit').innerHTML='<ul>'+snapshot.audit.slice(0,50).map(a=>`<li>${safe(a.created_at)} · ${safe(a.action)} · ${safe(a.actor)} · ${safe(a.reason)}</li>`).join('')+'</ul>';
    renderTags();
  }
  function renderTags() {
    if(!snapshot)return;
    const checked=new Set([...el('attendanceTags').querySelectorAll('input:checked')].map(n=>n.value));
    const query=el('attendanceTagSearch').value.trim().toLowerCase();
    el('attendanceTags').innerHTML='<thead><tr><th>Select</th><th>Student</th><th>Barcode</th><th>Tag</th></tr></thead><tbody>'+snapshot.students.filter(s=>`${s.name} ${s.barcode}`.toLowerCase().includes(query)).map(s=>`<tr><td><input type="checkbox" aria-label="Select ${safe(s.name)}" value="${safe(s.id)}" ${checked.has(s.id)?'checked':''}></td><td>${safe(s.name)}</td><td><code>${safe(s.barcode)}</code></td><td><button type="button" class="ghost" data-print="${safe(s.id)}">Print tag</button> ${snapshot.permissions.correct?`<button type="button" class="ghost" data-replace="${safe(s.id)}">Replace lost tag</button>`:''}</td></tr>`).join('')+'</tbody>';
  }
  async function sendScan(payload) {
    if(busy)return;
    pending=payload;rememberPending();setBusy(true);banner('Recording scan…');
    try {
      const headers={'Content-Type':'application/json'};
      if(authSession?.access_token)headers.Authorization=`Bearer ${authSession.access_token}`;
      const response=await fetch('/api/attendance/scan',{method:'POST',headers,body:JSON.stringify(payload),signal:AbortSignal.timeout(20000)});
      const data=await response.json();
      if(!response.ok||data.ok===false){const error=new Error(data.error||'Scan failed');error.status=response.status;throw error;}
      pending=null;rememberPending();
      if(data.action==='REVIEW_REQUIRED') {
        setBusy(false);
        if(snapshot?.permissions.correct && confirm(`${data.student_name}: ${data.message}\nRecord an extra visit?`)) {
          const reason=prompt('Reason for the extra visit:');
          if(reason?.trim())return sendScan({...payload,request_id:crypto.randomUUID(),extra_visit:true,reason});
        }
        banner(data.message,'unscheduled');return;
      }
      banner(`${data.student_name}: ${data.action.replaceAll('_',' ')} · ${data.message}`,data.record?.session_type||'unscheduled');
      try {
        const audio=new (window.AudioContext||window.webkitAudioContext)();const oscillator=audio.createOscillator();const gain=audio.createGain();gain.gain.value=0.04;oscillator.frequency.value=data.record?.session_type==='makeup'?660:880;oscillator.connect(gain);gain.connect(audio.destination);oscillator.start();oscillator.stop(audio.currentTime+0.15);oscillator.onended=()=>audio.close();
      } catch(_) { /* Text feedback remains available when audio is blocked. */ }
      el('attendanceCode').value='';
    } catch(error) {
      // Retain the same request ID: a lost response may have followed a successful write.
      if(error.status>=400&&error.status<500){pending=null;rememberPending();banner(error.message,'unscheduled');}
      else banner(`${error.message} · Retry the pending scan before scanning another tag.`,'unscheduled');
    } finally {
      setBusy(false);await refresh();
      if(el('attendance').classList.contains('active')&&!el('attendanceCorrection').open)el('attendanceCode').focus();
    }
  }
  async function printTags(ids) {
    if(!ids.length){toast('Select at least one student tag');return;}
    const popup=window.open('','_blank');
    if(!popup){toast('Allow popups to print tags');return;}
    popup.document.body.textContent='Preparing printable tags…';
    try {
      const tags=[];
      for(const id of ids) {
        const student=snapshot.students.find(s=>s.id===id);if(!student)continue;
        const headers={};if(authSession?.access_token)headers.Authorization=`Bearer ${authSession.access_token}`;
        const response=await fetch('/api/attendance/barcode.svg?student_id='+encodeURIComponent(id),{headers});
        if(!response.ok)throw Error('Barcode could not be loaded');
        const svg=await response.text();
        const url='data:image/svg+xml;base64,'+btoa(unescape(encodeURIComponent(svg)));
        tags.push(`<article><strong>${safe(student.name)}</strong><img alt="Student barcode" src="${url}"><span>SMP · Student attendance</span></article>`);
      }
      popup.document.open();popup.document.write('<!doctype html><html><head><title>Student attendance tags</title><style>body{font-family:Arial;display:flex;flex-wrap:wrap;gap:8mm}article{width:85mm;padding:4mm;border:1px dashed #888;text-align:center;break-inside:avoid}img{display:block;width:100%;height:auto}span{font-size:11px}@media print{body{margin:0}}</style></head><body>'+tags.join('')+'</body></html>');popup.document.close();
      await Promise.all([...popup.document.images].map(img=>img.decode()));popup.focus();popup.print();
    }catch(error){popup.document.body.textContent=error.message;toast(error.message);}
  }
  el('attendanceScanForm').addEventListener('submit',event=>{
    event.preventDefault();
    if(pending){banner('Retry the pending scan before starting another.','unscheduled');return;}
    sendScan({barcode_id:el('attendanceCode').value.trim(),request_id:crypto.randomUUID(),kiosk_device_id:'browser-scan-station'});
  });
  el('attendanceRetry').addEventListener('click',()=>pending&&sendScan(pending));
  el('attendanceManual').addEventListener('click',()=>{el('attendanceCode').value=el('attendanceStudent').value;el('attendanceCode').focus();});
  el('attendanceRefresh').addEventListener('click',refresh);
  el('attendanceDate').addEventListener('change',refresh);
  el('attendanceFilter').addEventListener('change',()=>snapshot&&render());
  el('attendanceTagSearch').addEventListener('input',renderTags);
  el('attendanceFullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await el('attendance').requestFullscreen();}catch(error){toast(error.message);}});
  el('attendanceTable').addEventListener('click',event=>{
    const id=event.target.closest('[data-correct]')?.dataset.correct;if(!id)return;
    const row=snapshot.records.find(r=>r.id===id);const form=el('attendanceCorrectionForm');form.reset();form.elements.record_id.value=id;form.elements.status.value=row.status==='in_center'?'completed':row.status==='expected'?'absent':row.status;
    form.elements.check_in.value=localInput(row.check_in);form.elements.check_out.value=localInput(row.check_out);el('attendanceCorrectionStudent').textContent=`${row.student_name} · ${row.day} · ${snapshot.timezone}`;el('attendanceCorrectionError').textContent='';el('attendanceCorrection').showModal();
  });
  el('attendanceCorrectionCancel').addEventListener('click',()=>el('attendanceCorrection').close());
  el('attendanceCorrectionForm').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    try{await api('/api/attendance/correct',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});el('attendanceCorrection').close();await refresh();}catch(error){el('attendanceCorrectionError').textContent=error.message;}finally{button.disabled=false;}
  });
  el('attendanceSettingsForm').addEventListener('submit',async event=>{
    event.preventDefault();const data=Object.fromEntries(new FormData(event.target));data.holidays=data.holidays.split(/\s+/).filter(Boolean);const button=event.submitter;button.disabled=true;
    try{await api('/api/attendance/settings',{method:'POST',body:JSON.stringify(data)});toast('Attendance settings saved');await refresh();}catch(error){toast(error.message);}finally{button.disabled=false;}
  });
  el('attendanceTags').addEventListener('click',async event=>{
    const print=event.target.closest('[data-print]');if(print)return printTags([print.dataset.print]);
    const replacement=event.target.closest('[data-replace]');if(!replacement)return;
    if(!confirm('Replace this lost tag? The previous barcode will stop working. Reprinting does not require replacement.'))return;
    const reason=prompt('Why is the tag being replaced?');if(!reason?.trim())return;
    try{await api('/api/attendance/replace-barcode',{method:'POST',body:JSON.stringify({student_id:replacement.dataset.replace,reason})});await refresh();toast('New barcode assigned; print the replacement tag');}catch(error){toast(error.message);}
  });
  el('attendancePrintSelected').addEventListener('click',()=>printTags([...el('attendanceTags').querySelectorAll('input:checked')].map(n=>n.value)));
  el('attendanceExport').addEventListener('click',()=>{
    if(!snapshot)return;
    const fields=['student_name','day','planned_start','planned_end','check_in','check_out','duration_minutes','session_type','status','makeup_state','note'];
    const cell=value=>'"'+String(value??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';
    const csv=[fields,...snapshot.records.map(r=>fields.map(k=>r[k]))].map(row=>row.map(cell).join(',')).join('\r\n');
    const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`attendance-${snapshot.date}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  document.querySelectorAll('[data-tab="attendance"],[data-tab="student-barcodes"]').forEach(tab=>tab.addEventListener('click',()=>{refresh();if(tab.dataset.tab==='attendance')el('attendanceCode').focus();}));
  window.addEventListener('online',refresh);document.addEventListener('visibilitychange',()=>{if(active())refresh();});
  document.addEventListener('smp:loaded',()=>{if(active())refresh();});
  if(pending){banner('An earlier scan needs confirmation. Retry it before scanning another tag.','unscheduled');el('attendanceRetry').hidden=false;}
  setInterval(()=>{if(active())refresh();},15000);
})();
