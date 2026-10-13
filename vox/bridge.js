// Injected into the chatgpt.com window. It lets vox send audio to the same
// /backend-api/transcribe request that the page's own Dictate button sends.
//
// vox never hard-codes the multipart layout or the auth headers. Instead it
// clicks Dictate once with a silent fake microphone, catches the request the
// page builds, and keeps it as a template. Each real clip reuses that
// template with only the audio replaced. When OpenAI changes the field names
// or headers, the next learn() picks up the new layout without a vox update.
(() => {
  if (window.__vox) return;

  const realFetch = window.fetch;
  // An embedded WebKit view can lack navigator.mediaDevices. The page then
  // hides the Dictate button, and learn() has nothing to click.
  if (!navigator.mediaDevices) {
    Object.defineProperty(navigator, "mediaDevices", { value: {}, configurable: true });
  }
  const md = navigator.mediaDevices;
  const realGetUserMedia = md.getUserMedia
    ? md.getUserMedia.bind(md)
    : () => Promise.reject(new DOMException("no microphone", "NotAllowedError"));
  const isTranscribe = (url) =>
    new URL(url, location.href).pathname === "/backend-api/transcribe";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  let template = null;
  let fakeMic = false;

  window.fetch = async function (input, init) {
    try {
      const url = typeof input === "string" ? input : input.url;
      if (isTranscribe(url)) {
        const req = new Request(input, init);
        const form = await req.clone().formData();
        const fields = [];
        for (const [k, v] of form.entries()) {
          fields.push(v instanceof Blob ? { k, blob: true } : { k, v });
        }
        template = {
          url: req.url,
          // FormData sets its own boundary, so the old content-type must go.
          headers: [...req.headers].filter(([k]) => k !== "content-type"),
          fields,
        };
      }
    } catch (_) {
      // Never break the page's own request because the copy failed.
    }
    return realFetch.apply(this, arguments);
  };

  md.getUserMedia = async (constraints) => {
    if (!fakeMic || !constraints || !constraints.audio || constraints.video) {
      return realGetUserMedia(constraints);
    }
    const ctx = new AudioContext();
    const out = ctx.createMediaStreamDestination();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    gain.gain.value = 0.0001;
    osc.connect(gain).connect(out);
    osc.start();
    // learn() runs without a user gesture, so the context can start
    // suspended. A suspended context gives no samples, and the page then
    // sends no request.
    await ctx.resume();
    return out.stream;
  };

  async function waitFor(fn, ms) {
    for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) {
      const v = fn();
      if (v) return v;
    }
    return null;
  }

  const button = (label) => document.querySelector(`button[aria-label="${label}"]`);

  async function learn() {
    const dictate = await waitFor(() => button("Dictate"), 15000);
    if (!dictate) throw new Error("no Dictate button: log in to ChatGPT in the vox window");
    template = null;
    fakeMic = true;
    try {
      // The button is in the DOM before the page's scripts attach their
      // handlers, and a click in that gap does nothing. Click again until
      // dictation starts.
      let stop = null;
      for (let i = 0; i < 10 && !stop; i++) {
        (button("Dictate") || dictate).click();
        stop = await waitFor(() => button("Stop dictation"), 1500);
      }
      if (!stop) throw new Error("dictation did not start");
      await sleep(1000);
      stop.click();
      if (!(await waitFor(() => template, 15000))) {
        throw new Error("page sent no transcribe request");
      }
    } finally {
      fakeMic = false;
    }
  }

  async function send(bytes, mime, filename) {
    const form = new FormData();
    for (const f of template.fields) {
      if (f.blob) form.append(f.k, new Blob([bytes], { type: mime }), filename);
      else form.append(f.k, f.v);
    }
    return realFetch(template.url, {
      method: "POST",
      headers: template.headers,
      body: form,
      credentials: "include",
    });
  }

  async function transcribe(b64, mime, filename) {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    if (!template) await learn();
    let res = await send(bytes, mime, filename);
    // An expired access token gives 401/403. A fresh learn() makes the page
    // fetch a new token, so retry once.
    if (res.status === 401 || res.status === 403) {
      await learn();
      res = await send(bytes, mime, filename);
    }
    if (!res.ok) throw new Error(`transcribe HTTP ${res.status}`);
    return (await res.json()).text || "";
  }

  // The host cannot await a promise: chatgpt.com's CSP forbids eval, so the
  // host has no promise-aware evaluate call. start() keeps the outcome
  // under an id, and the host polls result(id) until it is set.
  const results = {};
  function track(id, promise) {
    promise.then(
      (value) => { results[id] = { ok: true, value }; },
      (e) => { results[id] = { ok: false, error: String((e && e.message) || e) }; },
    );
  }
  function start(id, b64, mime, filename) {
    track(id, transcribe(b64, mime, filename));
  }
  // Learn right after login, so the user's first clip does not wait for it.
  function startLearn(id) {
    track(id, learn().then(() => ""));
  }
  function result(id) {
    const r = results[id];
    if (!r) return null;
    delete results[id];
    return JSON.stringify(r);
  }

  window.__vox = { learn, transcribe, start, startLearn, result, ready: () => !!template };
})();
