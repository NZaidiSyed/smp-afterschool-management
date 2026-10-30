/* Deterministic controller checks; no real camera permission or npm install. */
const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../camera-scanner.js'),'utf8');
function setup({permission,secure=true,allowed=true}={}){
 const track={stopped:0,stop(){this.stopped++},addEventListener(name,callback){this[name]=callback}};
 const stream={getTracks:()=>[track],getVideoTracks:()=>[track]};const states=[],errors=[],codes=[];let reads=0,reader;
 class NotFoundException extends Error{};class ChecksumException extends Error{};class FormatException extends Error{};
 class Reader{constructor(){reader=this;this.resets=0}reset(){this.resets++}async decodeFromStream(s,v,callback){this.callback=callback;v.srcObject=s}}
 const context={Map,window:{isSecureContext:secure,ZXing:{BrowserMultiFormatReader:Reader,DecodeHintType:{POSSIBLE_FORMATS:1},BarcodeFormat:{CODE_128:4},NotFoundException,ChecksumException,FormatException}},navigator:{mediaDevices:{getUserMedia:()=>{reads++;return permission?permission():Promise.resolve(stream)}}}};
 vm.runInNewContext(source,context);const video={srcObject:null};
 const camera=new context.window.AttendanceCameraScanner({video,onCode:code=>codes.push(code),onState:state=>states.push(state),onError:error=>errors.push(error),canScan:()=>allowed});
 return {camera,video,track,stream,states,errors,codes,get reads(){return reads},get reader(){return reader},NotFoundException};
}
const tag='SMP-1234567890ABCDEF1234';
(async()=>{
 let s=setup();await s.camera.start();assert.equal(s.reads,1);assert(s.camera.active);s.reader.callback({getText:()=>tag});s.reader.callback({getText:()=>tag});assert.deepEqual(s.codes,[tag]);assert(!s.camera.active);assert(s.track.stopped);assert.equal(s.video.srcObject,null);
 s=setup({allowed:false});await s.camera.start();assert.equal(s.reads,0);
 s=setup({secure:false});await s.camera.start();assert.equal(s.reads,0);assert.match(s.errors[0],/HTTPS/);
 s=setup({permission:()=>Promise.reject(Object.assign(new Error(),{name:'NotAllowedError'}))});await s.camera.start();assert.match(s.errors[0],/permission was denied/);assert(!s.camera.active);
 let resolve;s=setup({permission:()=>new Promise(r=>resolve=r)});const starting=s.camera.start();s.camera.stop();resolve(s.stream);await starting;assert(s.track.stopped);assert(!s.camera.active);assert.equal(s.reader,undefined);
 s=setup();await s.camera.start();s.reader.callback({getText:()=> 'NOT-A-STUDENT'});assert.equal(s.codes.length,0);assert.match(s.errors[0],/not an SMP/);assert(s.track.stopped);
 s=setup();await s.camera.start();s.reader.callback(null,new s.NotFoundException());assert(s.camera.active);assert.equal(s.errors.length,0);s.track.ended();assert(!s.camera.active);assert.match(s.errors[0],/disconnected/);
 s=setup();await s.camera.start();const callback=s.reader.callback;s.camera.stop();callback({getText:()=>tag});assert.equal(s.codes.length,0);
 console.log('PASS: 8 camera-controller scenarios (single-shot, pending guard, HTTPS, denial, delayed permission cancellation, invalid tag, frame retry/disconnect, stale callback)');
})().catch(error=>{console.error(error);process.exitCode=1});
