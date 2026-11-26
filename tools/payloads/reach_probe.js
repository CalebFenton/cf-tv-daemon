// Enumerate what the renderer can reach, without crashing.
//
// Every escalation route in the prior art needs a device node reachable from
// uid 5001 (`owner`), usually by group membership: commit-regrets' chain needs
// gid 44 (`video`) for /dev/video40. This payload cannot read /dev directly
// (the Web View blocks file://), so it measures the indirect signals:
//
//   * which Web APIs the sandbox permits, since each one maps to a device the
//     renderer was granted;
//   * whether media decode works, because it is what holds gid 44 open in the
//     first place;
//   * what the process says about itself through error strings and timing.
//
// It is diagnostic only. Nothing here is expected to grant access.

const out = {};
const t = (name, fn) => {
  try {
    const v = fn();
    out[name] = (v && typeof v.then === 'function') ? 'promise-pending' : v;
  } catch (e) { out[name] = 'ERR:' + String(e.message).slice(0, 60); }
};

// Group-revealing surfaces. A renderer in `video` can usually do these; one
// stripped of the group usually cannot, or gets a NotAllowedError.
t('mediaDevices', () => typeof navigator.mediaDevices);
t('enumerateDevices', () => navigator.mediaDevices
  ? 'present' : 'absent');
t('getUserMedia', () => typeof (navigator.mediaDevices || {}).getUserMedia);

// Codec support is the practical signal: hardware decode needs the video node.
t('h264Support', () => {
  const v = document.createElement('video');
  return v.canPlayType('video/mp4; codecs="avc1.42E01E"') || 'no';
});
t('vp8Support', () => {
  const v = document.createElement('video');
  return v.canPlayType('video/webm; codecs="vp8"') || 'no';
});
t('hevcSupport', () => {
  const v = document.createElement('video');
  return v.canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"') || 'no';
});

// MSE availability: a TV renderer almost always has it, and it is the API that
// keeps the media pipeline (and therefore the video group) alive in-process.
t('mediaSource', () => typeof MediaSource);
t('mediaSourceCodecs', () => {
  if (typeof MediaSource === 'undefined' || !MediaSource.isTypeSupported) return 'n/a';
  return ['video/mp4; codecs="avc1.42E01E"', 'audio/mp4; codecs="mp4a.40.2"']
    .filter(m => MediaSource.isTypeSupported(m)).length + '/2';
});

// WebGL reveals the GPU device without needing a privilege we do not have.
t('webglVendor', () => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl');
  if (!gl) return 'no-webgl';
  const d = gl.getExtension('WEBGL_debug_renderer_info');
  return d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
});

// Audio: the renderer is usually in the `audio` group as well, and an AudioContext
// that reaches 'running' proves a working ALSA path.
t('audioContext', () => {
  const AC = window.AudioContext || window.webkitAudioContext;
  return AC ? 'present' : 'absent';
});

// Storage tells us whether /opt is writable from the renderer's sandbox, which
// is gate 3 in the prior-art document.
t('localStorageBytes', () => {
  try {
    const key = 'probe' + Date.now();
    localStorage.setItem(key, 'x'.repeat(1000));
    const ok = localStorage.getItem(key).length === 1000;
    localStorage.removeItem(key);
    return ok ? 'writable' : 'silent-fail';
  } catch (e) { return 'ERR:' + String(e.message).slice(0, 40); }
});
t('indexedDB', () => typeof indexedDB);
t('storageEstimate', () => navigator.storage && navigator.storage.estimate
  ? 'present' : 'absent');

// The sandbox's own report, if it has one.
t('permissionsAPI', () => typeof navigator.permissions);
t('crossOriginIsolated', () => typeof crossOriginIsolated !== 'undefined' && crossOriginIsolated);
t('hardwareConcurrency', () => navigator.hardwareConcurrency);

// Process hints from timing: a renderer sharing a small core count behaves
// differently from an idle one, and this is cheap to collect.
const t0 = performance.now();
for (let i = 0; i < 2e6; i++) Math.sqrt(i);
out.loopMs = Math.round(performance.now() - t0);

return JSON.stringify(out, null, 1);
