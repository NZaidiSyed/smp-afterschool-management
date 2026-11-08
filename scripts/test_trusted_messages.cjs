/* Exercise actual message listeners with trusted and forged sender contexts. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const origin='https://synthetic.example',frame={},other={};let listener,logouts=0;
let source=fs.readFileSync('app.js','utf8');let start=source.indexOf('window.addEventListener("message", (event) => {');
let end=source.indexOf('\n});',start)+4;
vm.runInNewContext(source.slice(start,end),{
 window:{location:{origin},addEventListener:(_,fn)=>listener=fn},document:{getElementById:()=>({contentWindow:frame})},
 signOut:()=>logouts++,state:{},sbUsersToStaffMembers:()=>assert.fail('Untrusted staff data accepted'),renderStaffAdministration:()=>{}
});
listener({origin:'https://foreign.example',source:frame,data:{type:'logout'}});assert.equal(logouts,0);
listener({origin,source:other,data:{type:'logout'}});assert.equal(logouts,0);
listener({origin,source:frame,data:{type:'logout'}});assert.equal(logouts,1);
listener({origin:'https://foreign.example',source:frame,data:{type:'sb:dataChanged',key:'sb_users',data:[]}});
source=fs.readFileSync('staffbase.html','utf8');start=source.indexOf("window.addEventListener('message', (event) => {");end=source.indexOf('\n});',start)+4;
let writes=0,parent={};
vm.runInNewContext(source.slice(start,end),{
 window:{location:{origin},parent,addEventListener:(_,fn)=>listener=fn},
 localStorage:{setItem:()=>writes++},SCHOOL:{},SUBJECTS:[],JSON
});
const data={type:'smp:settingsChanged',el_students:[{name:'Synthetic student'}]};
listener({origin:'https://foreign.example',source:parent,data});assert.equal(writes,0);
listener({origin,source:other,data});assert.equal(writes,0);
listener({origin,source:parent,data});assert.equal(writes,1);
console.log('PASS: 7 trusted-window message checks');
