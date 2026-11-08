/* Local synthetic UAT only; backend authorization is covered by HTTP/PG tests. */
const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.SMP_SCHEDULER_TEST_URL||'http://127.0.0.1:8778';
 assert(['127.0.0.1','localhost'].includes(new URL(base).hostname),'Use a synthetic local server');
 const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{
  const c=await b.newContext({viewport:{width:1440,height:1000},timezoneId:'America/Winnipeg'}),p=await c.newPage();
  const errors=[];p.on('pageerror',e=>errors.push(e.message));
  let r=await c.request.get(base+'/api/config');const config=await r.json();
  assert(config.test_mode&&config.database==='sqlite'&&!config.auth_required,'Do not modify production/hosted data');
  r=await c.request.post(base+'/api/centre-setup',{data:{organization_name:'Synthetic Kumon',branch_name:'Synthetic YYC',branch_code:'CA-YYC-KMN-0001'}});assert(r.ok());
  r=await c.request.post(base+'/api/staffbase/auth/password',{data:{email:'manager@schedule.uat',password:'UatSchedule2026!'}});assert(r.ok());
  await p.goto(base+'/?attendance=1');await p.locator('#heading-admin').waitFor({state:'visible'});
  await p.locator('#heading-admin').click();await p.locator('#tabLoginHistory').click();
  await p.locator('#loginHistoryRows tr').filter({hasText:'manager@schedule.uat'}).first().waitFor();
  assert.match(await p.locator('#loginHistoryScope').textContent(),/CA-YYC-KMN-0001/);
  assert.match(await p.locator('#loginHistoryZone').textContent(),/recorded in UTC/);
  assert(await p.locator('#loginHistoryRows time').count()>0);
  await p.route('**/api/auth/history?*',async route=>{
   const response=await route.fetch(),body=await response.json();body.records[0].display_name='<img src=x onerror="window.historyPwned=true">';
   await route.fulfill({response,json:body});
  });
  await p.locator('#loginHistoryRefresh').click();await p.locator('#loginHistoryRows').filter({hasText:'<img src=x'}).waitFor();
  assert.equal(await p.locator('#loginHistoryRows img').count(),0);assert.equal(await p.evaluate(()=>window.historyPwned),undefined);
  await p.unroute('**/api/auth/history?*');await p.locator('#loginHistoryRefresh').click();
  await p.locator('#loginHistoryRows tr').first().waitFor();
  await p.screenshot({path:'/tmp/smp-signin-history-desktop.png',fullPage:true});
  await p.setViewportSize({width:390,height:844});
  assert(await p.locator('#login-history').evaluate(e=>e.getBoundingClientRect().width<=innerWidth));
  await p.screenshot({path:'/tmp/smp-signin-history-mobile.png',fullPage:true});
  await p.evaluate(()=>document.dispatchEvent(new Event('smp:auth-changed')));
  assert.equal(await p.locator('#loginHistoryRows tr').count(),0);
  await p.route('**/api/bootstrap',async route=>{
   const response=await route.fetch(),body=await response.json();body.current_user.role='Office Manager';
   await route.fulfill({response,json:body});
  });
  await p.reload();await p.locator('.tab[data-tab="attendance"]').waitFor({state:'visible'});
  assert.equal(await p.locator('#tabLoginHistory').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log('PASS: sign-in history browser checks (real synthetic record, branch, timestamp, refresh, inert HTML, mobile fit, auth reset, role hiding)');
  await c.close();
 }finally{await b.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
