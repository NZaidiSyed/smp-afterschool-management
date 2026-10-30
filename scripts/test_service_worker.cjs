/* Run with Node's built-in modules; no npm install required. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const listeners={};
let network,match,put;
const context={URL,console:{warn(){}},self:{location:{origin:'https://uat.example'},addEventListener:(name,listener)=>listeners[name]=listener},
fetch:request=>network(request),caches:{match:request=>match(request),open:async()=>({put:(...args)=>put(...args)})}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../sw.js'),'utf8'),context);
function event(url,mode='cors') {return {request:{url,mode,method:'GET'},respondWith(value){this.response=value}}}
(async()=>{
for(const url of ['https://cdn.example/script.js','https://uat.example/api/attendance']) {
 const e=event(url);listeners.fetch(e);assert.equal(e.response,undefined);
}
network=async()=>{throw Error('offline')};match=async key=>key==='/'?{html:'shell'}:undefined;
let e=event('https://uat.example/missing-script.js');listeners.fetch(e);await assert.rejects(e.response,/offline/);
e=event('https://uat.example/roster','navigate');listeners.fetch(e);assert.deepEqual(await e.response,{html:'shell'});
const response={status:200,clone(){return this}};network=async()=>response;put=async()=>{throw Error('quota')};
e=event('https://uat.example/app.js');listeners.fetch(e);assert.equal(await e.response,response);
const missing={status:404};network=async()=>missing;put=async()=>assert.fail('404 must not be cached');
e=event('https://uat.example/missing');listeners.fetch(e);assert.equal(await e.response,missing);
const assets=vm.runInNewContext('ASSETS',context);
for(const asset of assets)if(asset!=='/')assert(fs.existsSync(path.join(__dirname,'..',asset.slice(1))),`Precached file missing: ${asset}`);
console.log('PASS: 7 service-worker checks (external/API exclusion, script failure, navigation fallback, quota failure, HTTP error and precache assets)');
})().catch(error=>{console.error(error);process.exitCode=1});
