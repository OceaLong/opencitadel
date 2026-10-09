import { setTimeout as delay } from 'node:timers/promises';

// Fixed finite acceptance-only cadence. The injectable sleep is a pure-test seam,
// never derived from HTTP input. Backpressure extends (never catches up) cadence.
export async function writePacedStream(response, frames, {sleep = delay} = {}) {
  const controller = new AbortController();
  const disconnect = () => controller.abort(new Error('live stream disconnected'));
  response.on('close', disconnect);
  const check = () => {
    if (controller.signal.aborted || response.destroyed || response.writableEnded) {
      throw new Error('live stream disconnected');
    }
  };
  try {
    check();
    response.writeHead(200, {
      'Content-Type':'text/event-stream; charset=utf-8',
      'Cache-Control':'no-cache, no-transform', Connection:'keep-alive',
      'X-Accel-Buffering':'no', 'X-Acceptance-Profile':'finite-text-120x500ms-v1',
    });
    for (const frame of frames) {
      if (frame?.choices?.[0]?.delta?.content) {
        await sleep(500, undefined, {signal:controller.signal});
      }
      check();
      if (!response.write(`data: ${typeof frame === 'string' ? frame : JSON.stringify(frame)}\n\n`)) {
        await new Promise((resolve, reject) => {
          const clean = () => {
            response.off('drain', drained); response.off('close', closed); response.off('error', failed);
          };
          const drained = () => { clean(); resolve(); };
          const closed = () => { clean(); reject(new Error('live stream disconnected')); };
          const failed = error => { clean(); reject(error); };
          response.once('drain', drained); response.once('close', closed); response.once('error', failed);
          if (response.destroyed || controller.signal.aborted) closed();
        });
      }
    }
    check();
    response.end();
  } finally {
    response.off('close', disconnect);
  }
}
