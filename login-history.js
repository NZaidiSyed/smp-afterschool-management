/* Private history loads on demand; no records are saved to browser storage. */
(() => {
  const tab=document.getElementById('tabLoginHistory');
  if(!tab)return;
  const panel=document.getElementById('login-history');
  const rows=document.getElementById('loginHistoryRows');
  const status=document.getElementById('loginHistoryStatus');
  const refresh=document.getElementById('loginHistoryRefresh');
  const more=document.getElementById('loginHistoryMore');
  const zone=Intl.DateTimeFormat().resolvedOptions().timeZone;
  const formatter=new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'long'});
  document.getElementById('loginHistoryZone').textContent=`Times shown in ${zone}; recorded in UTC.`;
  let cursor=null,request=null,generation=0;
  function reset(){
    generation++;request?.abort();request=null;cursor=null;rows.replaceChildren();more.hidden=true;
    refresh.disabled=false;more.disabled=false;status.dataset.error='false';
    status.textContent='Select Refresh history to load verified sign-ins.';
    document.getElementById('loginHistoryScope').textContent='History is recorded from feature activation; earlier logins are not reconstructed.';
  }
  async function load(append=false){
    if(tab.hidden)return;
    request?.abort();request=new AbortController();
    const current=++generation;
    refresh.disabled=true;more.disabled=true;status.dataset.error='false';status.textContent='Loading verified sign-ins…';
    try{
      // Reuse the primary app's bearer/session transport; enforce permission again on the server.
      const data=await window.api('/api/auth/history?limit=50'+(append&&cursor?'&cursor='+encodeURIComponent(cursor):''),
                                  {credentials:'same-origin',cache:'no-store',signal:request.signal});
      if(current!==generation||tab.hidden)return;
      if(!append)rows.replaceChildren();
      for(const item of data.records){
        const row=document.createElement('tr');
        const event={session_first_verified:'Session first verified',token_first_verified:'Token first verified',staff_sign_in:'Staff sign-in'}[item.event_kind]||'Verified access';
        const method={supabase:'Centre account',staff_pin:'Staff PIN',staff_password:'Staff password',staff:'Staff'}[item.method]||item.method;
        const role={office_manager:'Office Manager',office_assistant:'Office Assistant',principal_owner:'Owner',administrator:'Admin',staff:'Staff'}[item.role]||item.role;
        for(const value of [item.login_id,item.display_name||'—',role,method]){
          const cell=document.createElement('td');cell.textContent=value;row.append(cell);
        }
        const cell=document.createElement('td'),time=document.createElement('time');
        time.dateTime=item.verified_at;time.textContent=formatter.format(new Date(item.verified_at));time.title=item.verified_at;cell.append(time);row.append(cell);
        const kind=document.createElement('td');kind.textContent=event;row.append(kind);rows.append(row);
      }
      cursor=data.next_cursor;more.hidden=!cursor;
      document.getElementById('loginHistoryScope').textContent=`Branch ${data.branch_code} · Historical verified sessions, not a list of users currently online.`;
      status.textContent=rows.children.length?`${rows.children.length} record${rows.children.length===1?'':'s'} displayed, newest first.`:'No verified sign-ins recorded for this branch yet.';
    }catch(error){
      if(current!==generation||error.name==='AbortError')return;
      if(!append)rows.replaceChildren();
      status.dataset.error='true';status.textContent=error.message;more.hidden=true;
    }finally{
      if(current===generation){refresh.disabled=false;more.disabled=false;request=null;}
    }
  }
  tab.addEventListener('click',()=>load());
  refresh.addEventListener('click',()=>load());more.addEventListener('click',()=>load(true));
  document.addEventListener('smp:auth-changed',reset);
  document.addEventListener('smp:loaded',()=>{reset();if(panel.classList.contains('active')&&!tab.hidden)load();});
  document.getElementById('signOut')?.addEventListener('click',reset,true);
  new MutationObserver(()=>{if(tab.hidden)reset();}).observe(tab,{attributes:true,attributeFilter:['hidden']});
})();
