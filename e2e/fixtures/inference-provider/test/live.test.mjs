import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';
import * as chat from '../lib/chat.mjs';
import * as paced from '../lib/paced.mjs';

const request = {model:'acceptance-live', stream:true, messages:[{role:'user',content:'ignored'}], max_tokens:4096};
class Response extends EventEmitter {
  frames=[]; destroyed=false; writableEnded=false;
  writeHead(status, headers) {this.status=status;}
  write(frame) {this.frames.push(frame); return true;}
  end() {this.writableEnded=true;}
}
test('finite fixed profile emits real text, terminal then usage then DONE', async () => {
  const frames=chat.streamChat(request);
  assert.equal(frames.filter(f=>f?.choices?.[0]?.delta?.content).length, 120);
  assert.equal(frames.at(-3).choices[0].finish_reason,'stop');
  assert.ok(frames.at(-2).usage.completion_tokens > 0);
  assert.equal(frames.at(-1),'[DONE]');
  const response=new Response(), delays=[];
  await paced.writePacedStream(response, frames, {sleep:async ms=>delays.push(ms)});
  assert.equal(delays.length,120);
  assert.ok(delays.every(ms=>ms===500));
  assert.equal(response.frames.length, frames.length);
  assert.ok(response.writableEnded);
});
test('backpressure waits for drain and disconnect aborts without terminal success', async () => {
  const response=new Response();
  response.write=function(frame) {this.frames.push(frame); return false;};
  const work=paced.writePacedStream(response, chat.streamChat(request), {sleep:async()=>{}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(response.frames.length,1);
  response.destroyed=true; response.emit('close');
  await assert.rejects(work, /disconnect/);
  assert.equal(response.writableEnded,false);
  assert.equal(response.listenerCount('close'),0);
  assert.equal(response.listenerCount('drain'),0);
});
test('live rejects invoke/tools/tiny bound, preserving old fixed profile', () => {
  assert.throws(()=>chat.completeChat({...request,stream:false}));
  assert.throws(()=>chat.streamChat({...request,tools:[{type:'function',function:{name:'x'}}]}));
  assert.throws(()=>chat.streamChat({...request,max_tokens:1}));
  assert.equal(chat.CAPACITY_SUCCESS_DELAY_MS,100);
  assert.equal(chat.completeChat({...request,model:'acceptance-capacity',stream:false}).choices[0].finish_reason,'stop');
});

test('real request listener routes finite profile without binding a socket', async () => {
  const {createServer}=await import('../server.mjs');
  const server=createServer();
  const requestBody={...request};
  const req={url:'/v1/chat/completions',method:'POST',headers:{authorization:'Bearer acceptance-provider-token','content-type':'application/json'},
    async *[Symbol.asyncIterator]() { yield Buffer.from(JSON.stringify(requestBody)); }};
  const response=new Response();
  response.writeHead=function(status) {this.status=status; this.headersSent=true;};
  response.write=function(frame) {
    this.frames.push(frame); this.destroyed=true; this.emit('close'); return true;
  };
  response.destroy=function() {this.destroyed=true;};
  await server.listeners('request')[0](req,response);
  assert.equal(response.status,200);
  assert.equal(response.frames.length,1);
  assert.equal(response.writableEnded,false);
  assert.ok(!response.frames.some(f=>f.includes('[DONE]')));
  assert.equal(response.listenerCount('close'),0);
});
