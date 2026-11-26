// Fingerprint the panel's V8 without needing a bug.
//
// Chromium 105 pairs with V8 10.5, but Samsung's build may differ, and the
// exploit's object offsets depend on it. Everything here runs as ordinary
// JavaScript, so it is safe to queue on a device we do not want to crash.
//
// Enqueued by tools/fridge_harness.py. Returns a JSON string.

(() => {
  const out = {};
  const t = (name, fn) => {
    try { out[name] = fn(); } catch (e) { out[name] = 'ERR:' + (e && e.message); }
  };

  // Error-message formats change between V8 releases and are hard to fake.
  t('stackShape', () => {
    const e = new Error('x');
    return String(e.stack).split('\n').slice(0, 2).join(' | ');
  });
  t('rangeErrorText', () => {
    try { new Array(-1); } catch (e) { return e.message; }
    return 'no-throw';
  });
  t('invalidLenText', () => {
    try { new ArrayBuffer(-1); } catch (e) { return e.message; }
    return 'no-throw';
  });
  t('toStringTag', () => Object.prototype.toString.call([]));

  // Sort stability landed in V8 7.0; the comparison count in sort changed with
  // the TimSort rewrite. Both bracket the engine independently of the UA.
  t('sortStable', () => {
    const a = [{k: 1, i: 0}, {k: 1, i: 1}, {k: 0, i: 2}, {k: 1, i: 3}];
    a.sort((x, y) => x.k - y.k);
    return a.map(o => o.i).join(',');
  });
  t('sortCalls', () => {
    let n = 0;
    const a = Array.from({length: 16}, (_, i) => 16 - i);
    a.sort((x, y) => { n++; return x - y; });
    return n;
  });

  // Heap geometry. The exploit needs to know when objects land in new space and
  // how large the old space is; these are the observable proxies.
  t('heapLimitMB', () => (performance.memory ?
    Math.round(performance.memory.jsHeapSizeLimit / 1048576) : 'no-perf-memory'));
  t('totalHeapMB', () => (performance.memory ?
    Math.round(performance.memory.totalJSHeapSize / 1048576) : null));

  // Wasm is the vector for several later bugs; record what is usable.
  t('wasmBasic', () => {
    const m = new WebAssembly.Module(new Uint8Array(
      [0,97,115,109,1,0,0,0]));
    return m instanceof WebAssembly.Module;
  });
  t('wasmMemoryMax', () => {
    const m = new WebAssembly.Memory({initial: 1});
    return m.buffer.byteLength;
  });

  // Object layout probes that need no corruption: the number of in-object
  // properties V8 gives a plain object tells us the map layout generation.
  t('inObjectProps', () => {
    const o = {};
    for (let i = 0; i < 32; i++) o['p' + i] = i;
    return Object.keys(o).length;
  });

  // ArrayBuffer byte length limits differ by build and reveal pointer size.
  t('maxArrayBuffer', () => {
    try {
      const b = new ArrayBuffer(0x7fffffff);
      return b.byteLength;
    } catch (e) { return 'throw:' + e.message.slice(0, 40); }
  });

  t('ua', () => navigator.userAgent);
  t('devicePixelRatio', () => devicePixelRatio);

  return JSON.stringify(out, null, 1);
})()
