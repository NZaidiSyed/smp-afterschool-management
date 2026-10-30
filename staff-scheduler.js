(() => {
  const $=id=>document.getElementById(id),safe=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const weekdays=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
  const iso=day=>`${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
  const today=iso(new Date()),date=value=>new Date(value+'T12:00:00'),offset=(value,n)=>{const d=date(value);d.setDate(d.getDate()+n);return iso(d);};
  const currentMonday=()=>{const d=new Date();d.setDate(d.getDate()-(d.getDay()+6)%7);return iso(d)};
  let week=currentMonday(),data=null,rows=[],dirty=false,availabilityDirty=false,busy=false,copyPayload=null,tab='schedule',availabilitySelection=null;
  $('weekStart').value=week;$('availabilityDate').value=today;
  const staffName=id=>data?.staff.find(s=>s.id===id)?.name||'Archived staff';
  const summary=s=>`${s.day} · ${s.start}–${s.end} · ${s.kind}`;
  const setMessage=(text,error=false)=>{$('message').textContent=text;$('message').className=error?'error':'success';};
  async function api(path,payload){
    const headers={'Content-Type':'application/json'},token=localStorage.getItem('sb_jwt');if(token)headers.Authorization='Bearer '+token;
    const response=await fetch('/api/scheduling'+path,{method:payload?'POST':'GET',headers,body:payload?JSON.stringify(payload):undefined,signal:AbortSignal.timeout(20000)});
    const result=await response.json();if(!response.ok)throw Object.assign(new Error(result.error||'Request failed'),{status:response.status});return result;
  }
  async function operation(fn,dialog,keepOpen=false){
    if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{await fn();if(dialog&&!keepOpen)$(dialog).close();}catch(error){setMessage(error.message,true);if(dialog){let n=$(dialog).querySelector('.dialog-error');if(!n){n=document.createElement('p');n.className='dialog-error';n.setAttribute('role','alert');$(dialog).append(n)}n.textContent=error.message;}}
    finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);setControls();}
  }
  function setControls(){if(!data)return;$('managerActions').hidden=!data.actor.manager;$('saveDraft').disabled=!dirty||busy;$('publishOpen').disabled=dirty||busy;$('copyConfirm').disabled=!copyPayload||busy;$('swapOpen').disabled=!data.actor.staff_id||busy;}
  async function load(){
    data=await api('?week_start='+encodeURIComponent(week));rows=structuredClone(data.shifts);dirty=false;availabilityDirty=false;render();
  }
  function issues(checks){return checks.conflicts.length||checks.warnings.length?'<div class="issues"><strong>'+checks.conflicts.length+' conflicts · '+checks.warnings.length+' warnings</strong><ul>'+checks.conflicts.map(c=>'<li>'+safe(c)+'</li>').join('')+checks.warnings.map(c=>'<li>'+safe(c)+'</li>').join('')+'</ul></div>':'<p class="muted">No availability or overlap conflicts detected.</p>';}
  function render(){
    document.body.dataset.schedulerRole=data.actor.manager?'manager':'staff';
    $('actor').textContent=data.actor.name+' · '+(data.actor.manager?'Manager':'Staff');
    $('weekStatus').innerHTML=`<span class="badge ${data.has_unpublished_changes||dirty?'warning':''}">${data.actor.manager?(dirty?'Unsaved local edits':data.has_unpublished_changes?'Draft changes pending publication':'Saved'):'Published schedule'}</span><span>Draft revision ${data.actor.manager?data.revision:'—'}</span><span>Published revision ${data.published_revision||'none'}</span><span>${rows.length} shifts · ${(rows.reduce((a,s)=>a+(time(s.end)-time(s.start)-s.break_minutes)/60,0)).toFixed(1)} paid hours</span>`;
    $('issues').innerHTML=data.actor.manager?issues(data.checks):'';
    const staff=data.actor.manager?data.staff:data.staff.filter(s=>s.id===data.actor.staff_id);
    $('scheduleGrid').innerHTML='<thead><tr><th>Staff / paid hours</th>'+weekdays.map((d,i)=>`<th>${d.slice(0,3)}<br>${offset(week,i)}</th>`).join('')+'</tr></thead><tbody>'+staff.map(person=>{
      const mine=rows.filter(s=>s.staff_id===person.id),hours=mine.reduce((a,s)=>a+(time(s.end)-time(s.start)-s.break_minutes)/60,0);
      return `<tr><td>${safe(person.name)}<p class="muted">${hours.toFixed(1)} hours</p></td>`+weekdays.map((_,i)=>{const day=offset(week,i);return '<td>'+mine.filter(s=>s.day===day).map(s=>`<button type="button" class="shift-card" data-edit="${safe(s.id)}"><strong>${safe(s.start)}–${safe(s.end)}</strong>${safe(s.kind)}<small>${safe(s.location)}${s.break_minutes?' · '+s.break_minutes+'m break':''}</small></button>`).join('')+(data.actor.manager?`<button type="button" class="add-shift" data-staff="${safe(person.id)}" data-day="${day}">+ Add shift</button>`:'')+'</td>';}).join('')+'</tr>';
    }).join('')+'</tbody>';
    $('mobileMyShifts').innerHTML=rows.length?rows.map(s=>`<article class="request-card"><strong>${safe(s.day)} · ${safe(s.start)}–${safe(s.end)}</strong><p>${safe(s.kind)} · ${safe(s.location)}</p><small>${safe(s.notes)}</small></article>`).join(''):'<p class="empty">No published shifts for you this week.</p>';
    $('receiptsPanel').hidden=$('auditPanel').hidden=!data.actor.manager;
    $('receipts').innerHTML=data.receipts.length?'<ul>'+data.receipts.map(r=>`<li>${safe(staffName(r.staff_id))}: ${r.acknowledged_at?'Acknowledged':r.read_at?'Read — acknowledgement pending':'Available in inbox — unread'}</li>`).join('')+'</ul>':'<p class="muted">No publication notifications for this week.</p>';
    $('audit').innerHTML='<ul>'+data.audit.map(a=>`<li>${safe(a.created_at)} · ${safe(a.actor)} · ${safe(a.action.replaceAll('_',' '))}</li>`).join('')+'</ul>';
    const selected=$('availabilityStaff').value;
    $('availabilityStaff').innerHTML=(data.actor.manager?data.staff:data.staff.filter(s=>s.id===data.actor.staff_id)).map(s=>`<option value="${safe(s.id)}">${safe(s.name)}</option>`).join('');
    if(selected&&[...$('availabilityStaff').options].some(s=>s.value===selected))$('availabilityStaff').value=selected;
    renderAvailability();renderSwaps();renderInbox();setControls();
  }
  function time(v){const [h,m]=v.split(':').map(Number);return h*60+m;}
  function renderAvailability(){
    const id=$('availabilityStaff').value,effective=$('availabilityDate').value;
    availabilitySelection={id,effective};
    const profile=data.availability.filter(a=>a.staff_id===id&&a.effective_from<=effective).at(-1);
    const windows=profile?JSON.parse(profile.windows_json):{};
    $('availabilityGrid').innerHTML='<thead><tr><th>Day</th><th>Availability</th><th>From</th><th>Until</th></tr></thead><tbody>'+weekdays.map((day,i)=>{
      const value=windows[i],state=value==null?'unknown':value.length?'available':'unavailable';
      return `<tr data-weekday="${i}"><td>${day}</td><td><select aria-label="${day} availability"><option value="unknown" ${state==='unknown'?'selected':''}>Not provided</option><option value="available" ${state==='available'?'selected':''}>Available</option><option value="unavailable" ${state==='unavailable'?'selected':''}>Unavailable</option></select></td><td><input aria-label="${day} available from" type="time" data-start value="${safe(value?.[0]?.start||'15:00')}"></td><td><input aria-label="${day} available until" type="time" data-end value="${safe(value?.[0]?.end||'20:00')}"></td></tr>`;
    }).join('')+'</tbody>';
  }
  function renderSwaps(){
    $('swapCount').textContent=data.swaps.filter(s=>['awaiting_colleague','awaiting_manager'].includes(s.status)).length||'';
    $('swapList').innerHTML=data.swaps.length?data.swaps.map(r=>{
      const source=JSON.parse(r.source_json),target=r.target_json?JSON.parse(r.target_json):null;let buttons='';
      if(r.target_id===data.actor.staff_id&&r.status==='awaiting_colleague')buttons+=`<button data-swap-action="accept" data-id="${safe(r.id)}">Accept request</button><button data-swap-action="decline" data-id="${safe(r.id)}" class="secondary">Decline</button>`;
      if(r.owner_id===data.actor.staff_id&&['awaiting_colleague','awaiting_manager'].includes(r.status))buttons+=`<button data-swap-action="cancel" data-id="${safe(r.id)}" class="secondary">Cancel request</button>`;
      if(data.actor.manager&&['awaiting_colleague','awaiting_manager'].includes(r.status))buttons+=`${r.status==='awaiting_manager'?`<button data-swap-action="approve" data-id="${safe(r.id)}" class="primary">Approve change</button>`:''}<button data-swap-action="reject" data-id="${safe(r.id)}" class="danger">Reject</button>`;
      return `<article class="request-card"><header><strong>${safe(staffName(r.owner_id))} → ${safe(staffName(r.target_id))}</strong><span class="badge warning">${safe(r.status.replaceAll('_',' '))}</span></header><p>${safe(summary(source))}${target?'<br>Exchange: '+safe(summary(target)):'<br>Cover request — no return shift'}</p><p>${safe(r.reason)}</p>${r.decision_reason?'<p>Decision: '+safe(r.decision_reason)+'</p>':''}<div class="inline-actions">${buttons}</div></article>`;
    }).join(''):'<p class="empty">No shift requests yet.</p>';
  }
  function renderInbox(){
    $('unreadCount').textContent=data.inbox.filter(n=>!n.read_at).length||'';
    $('inboxList').innerHTML=data.inbox.length?data.inbox.map(n=>`<article class="notification ${n.read_at?'':'unread'}"><header><strong>${safe(n.title)}</strong><span class="muted">${safe(new Date(n.created_at).toLocaleString())}</span></header><p>${safe(n.body)}</p><span class="badge">${n.acknowledged_at?'Acknowledged':n.read_at?'Read':'Unread'}</span><div class="inline-actions">${!n.read_at?`<button data-notification="${safe(n.id)}" data-action="read" class="secondary">Mark read</button>`:''}${n.schedule_revision&&!n.acknowledged_at?`<button data-notification="${safe(n.id)}" data-action="acknowledge">Acknowledge revision ${n.schedule_revision}</button>`:''}</div></article>`).join(''):'<p class="empty">Your inbox is empty. Publication and swap updates appear here.</p>';
  }
  async function changeWeek(value){if(date(value).getDay()!==1){$('weekStart').value=week;setMessage('Choose a Monday for the week start.',true);return;}if(dirty&&!confirm('Discard unsaved local edits?')){$('weekStart').value=week;return;}$('weekStart').value=week=value;await operation(load);}
  $('prevWeek').onclick=()=>changeWeek(offset(week,-7));$('nextWeek').onclick=()=>changeWeek(offset(week,7));$('thisWeek').onclick=()=>changeWeek(currentMonday());$('weekStart').onchange=()=>changeWeek($('weekStart').value);
  document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{if(dirty){setMessage('Save your draft before switching sections.',true);return;}if(availabilityDirty&&!confirm('Discard unsaved availability changes?'))return;availabilityDirty=false;tab=button.dataset.tab;document.querySelectorAll('.section,.tabs button').forEach(n=>n.classList.remove('active'));button.classList.add('active');$(tab).classList.add('active');operation(load);});
  document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
  $('scheduleGrid').onclick=event=>{
    const button=event.target.closest('button');if(!button||!data.actor.manager)return;
    const shift=button.dataset.edit?rows.find(r=>r.id===button.dataset.edit):{staff_id:button.dataset.staff,day:button.dataset.day,start:'15:00',end:'20:00',break_minutes:0,kind:'Teaching'};
    const form=$('shiftForm');form.reset();form.elements.staff_id.innerHTML=data.staff.map(s=>`<option value="${safe(s.id)}">${safe(s.name)}</option>`).join('');
    for(const key of ['id','staff_id','day','start','end','break_minutes','kind','location','notes'])form.elements[key].value=shift[key]??'';
    $('deleteShift').hidden=!shift.id;$('shiftTitle').textContent=shift.id?'Edit shift':'Add shift';$('shiftDialog').showModal();
  };
  $('shiftForm').onsubmit=event=>{event.preventDefault();const s=Object.fromEntries(new FormData(event.target));if(time(s.end)<=time(s.start)||Number(s.break_minutes)>=time(s.end)-time(s.start)){alert('End must follow start; breaks must be shorter than the shift.');return;}s.break_minutes=Number(s.break_minutes);s.id=s.id||crypto.randomUUID();rows=rows.filter(r=>r.id!==s.id);rows.push(s);dirty=true;$('shiftDialog').close();render();};
  $('deleteShift').onclick=()=>{rows=rows.filter(s=>s.id!==$('shiftForm').elements.id.value);dirty=true;$('shiftDialog').close();render();};
  $('saveDraft').onclick=()=>operation(async()=>{await api('/draft',{week_start:week,revision:data.revision,shifts:rows});await load();setMessage('Draft saved in the database. Published shifts have not changed.');});
  $('copyOpen').onclick=()=>{if(dirty){setMessage('Save or discard local edits before copying a week.',true);return;}copyPayload=null;$('copyConfirm').disabled=true;$('copySource').value=offset(week,-7);$('copyTarget').textContent='Target week: '+week;$('copyPreview').textContent='';$('copyDialog').showModal();};
  ['copySource','copyVersion','copyMode'].forEach(id=>$(id).onchange=()=>{copyPayload=null;$('copyConfirm').disabled=true;$('copyPreview').textContent='Options changed. Preview again.';});
  // Preview stays open; use its actual dialog for inline errors.
  $('copyForm').onsubmit=event=>{event.preventDefault();operation(async()=>{const payload={week_start:week,revision:data.revision,source_week:$('copySource').value,source_version:$('copyVersion').value,mode:$('copyMode').value};const preview=await api('/copy-preview',payload);copyPayload={...payload,source_revision:preview.source_revision};$('copyPreview').innerHTML=`<p><strong>${preview.shifts.length} shifts in the resulting draft.</strong></p>`+issues(preview);},'copyDialog',true);};
  $('copyConfirm').onclick=()=>operation(async()=>{await api('/copy',copyPayload);await load();copyPayload=null;setMessage('Week copied to the selected draft. Review conflicts before publishing.');},'copyDialog');
  $('publishOpen').onclick=()=>{$('publishReview').innerHTML=`<p>Week of ${week} · ${rows.length} shifts · draft revision ${data.revision}</p>`+issues(data.checks);$('acceptWarnings').checked=false;$('publishDialog').showModal();};
  $('publishForm').onsubmit=event=>{event.preventDefault();operation(async()=>{const result=await api('/publish',{week_start:week,revision:data.revision,accept_warnings:$('acceptWarnings').checked});await load();setMessage(`${result.message} ${result.notifications_created} notification(s) created.`);},'publishDialog');};
  $('availabilityGrid').addEventListener('input',()=>availabilityDirty=true);
  $('refreshData').onclick=()=>{if((dirty||availabilityDirty)&&!confirm('Discard unsaved changes and refresh?'))return;operation(load);};
  $('availabilityStaff').onchange=$('availabilityDate').onchange=()=>{
    if(availabilityDirty&&!confirm('Discard the unsaved availability profile?')){
      $('availabilityStaff').value=availabilitySelection.id;$('availabilityDate').value=availabilitySelection.effective;return;
    }
    availabilityDirty=false;renderAvailability();
  };
  $('availabilityForm').onsubmit=event=>{event.preventDefault();operation(async()=>{
    const sid=$('availabilityStaff').value,effective=$('availabilityDate').value,old=data.availability.find(r=>r.staff_id===sid&&r.effective_from===effective),windows={};
    $('availabilityGrid').querySelectorAll('[data-weekday]').forEach(row=>{const state=row.querySelector('select').value;windows[row.dataset.weekday]=state==='unknown'?null:state==='unavailable'?[]:[{start:row.querySelector('[data-start]').value,end:row.querySelector('[data-end]').value}];});
    await api('/availability',{staff_id:sid,effective_from:effective,revision:old?.revision||0,windows});await load();setMessage('Availability saved. Managers will see conflicts before publication.');
  });};
  function fillExchange(){const sid=$('swapTarget').value;$('swapExchange').innerHTML='<option value="">Cover only — no return shift</option>'+data.team_shifts.filter(s=>s.staff_id===sid).map(s=>`<option value="${safe(s.id)}">${safe(summary(s))}</option>`).join('');}
  $('swapOpen').onclick=()=>{const mine=data.team_shifts.filter(s=>s.staff_id===data.actor.staff_id);if(!mine.length){setMessage('You have no published shifts in the selected week.',true);return;}$('swapForm').reset();$('swapSource').innerHTML=mine.map(s=>`<option value="${safe(s.id)}">${safe(summary(s))}</option>`).join('');$('swapTarget').innerHTML=data.staff.filter(s=>s.id!==data.actor.staff_id).map(s=>`<option value="${safe(s.id)}">${safe(s.name)}</option>`).join('');fillExchange();$('swapDialog').showModal();};$('swapTarget').onchange=fillExchange;
  $('swapForm').onsubmit=event=>{event.preventDefault();operation(async()=>{await api('/swaps',{week_start:week,source_id:$('swapSource').value,target_staff_id:$('swapTarget').value,target_shift_id:$('swapExchange').value,reason:$('swapReason').value});await load();setMessage('Request sent to the colleague’s in-app inbox. Manager approval is still required.');},'swapDialog');};
  $('swapList').onclick=event=>{const button=event.target.closest('[data-swap-action]');if(!button)return;const request=data.swaps.find(r=>r.id===button.dataset.id),action=button.dataset.swapAction;const reason=['approve','reject'].includes(action)?prompt('Decision reason (required). Approval rechecks overlaps, leave and availability:'):'';if(reason===null)return;operation(async()=>{await api('/swaps/decide',{id:request.id,revision:request.revision,action,reason});await load();setMessage('Request updated; affected staff have in-app updates.');});};
  $('inboxList').onclick=event=>{const button=event.target.closest('[data-notification]');if(!button)return;operation(async()=>{await api('/inbox',{id:button.dataset.notification,action:button.dataset.action});await load();setMessage('Notification updated.');});};$('inboxRefresh').onclick=()=>operation(load);
  $('loginOpen').onclick=()=>$('loginDialog').showModal();$('loginForm').onsubmit=async event=>{event.preventDefault();await operation(async()=>{if(dirty&&!confirm('Discard unsaved edits and switch account?'))return;const response=await fetch('/api/staffbase/auth/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});const result=await response.json();if(!response.ok||!result.token)throw Error(result.error||'Sign-in failed');localStorage.setItem('sb_jwt',result.token);await load();setMessage('Signed in to the staff scheduling workspace.');},'loginDialog');};
  window.addEventListener('beforeunload',event=>{if(dirty||availabilityDirty){event.preventDefault();event.returnValue='';}});
  setInterval(async()=>{
    if(busy||document.hidden||!data)return;
    try {
      const fresh=await api('?week_start='+encodeURIComponent(week));
      if(!dirty&&!availabilityDirty&&!document.querySelector('dialog[open]')&&!document.activeElement?.closest('#availabilityForm')) {
        data=fresh;rows=structuredClone(data.shifts);render();
      } else {
        data.inbox=fresh.inbox;renderInbox();
        if(fresh.revision!==data.revision&&data.actor.manager)setMessage('The server schedule changed. Refresh before saving.',true);
      }
    }catch(_){}
  },30000);
  operation(load);
})();
