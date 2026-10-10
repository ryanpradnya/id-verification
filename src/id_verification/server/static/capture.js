// Hosted capture flow: live camera only (no file picker), KTP first, then selfie holding the KTP.
(() => {
  const STEPS = [
    { key: "ktp", facing: "environment", title: "Foto KTP",
      hint: "Letakkan KTP di dalam bingkai, rata, tanpa pantulan cahaya." },
    { key: "selfie", facing: "user", title: "Selfie dengan KTP",
      hint: "Pegang KTP di samping wajah. Hadap kamera, pencahayaan cukup." },
  ];
  const $ = (id) => document.getElementById(id);
  const video = $("video"), preview = $("preview"), frame = $("frame");
  const shoot = $("shoot"), retake = $("retake"), accept = $("accept"), error = $("error");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const shots = {};
  let step = 0, stream = null, blob = null;

  const show = (el, on) => { el.hidden = !on; };
  const fail = (msg) => { error.textContent = msg; show(error, true); };

  async function startCamera() {
    const s = STEPS[step];
    $("step-label").textContent = `Langkah ${step + 1} dari ${STEPS.length}`;
    $("title").textContent = s.title;
    $("hint").textContent = s.hint;
    frame.className = "frame " + s.key;
    video.classList.toggle("mirror", s.facing === "user");
    show(error, false);
    if (stream) stream.getTracks().forEach((t) => t.stop());
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: s.facing }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      video.srcObject = stream;
    } catch (e) {
      fail("Kamera tidak dapat diakses. Izinkan akses kamera lalu muat ulang halaman.");
    }
  }

  function capture() {
    if (!video.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = video.videoWidth;
    c.height = video.videoHeight;
    c.getContext("2d").drawImage(video, 0, 0);
    c.toBlob((b) => {
      blob = b;
      preview.src = URL.createObjectURL(b);
      show(preview, true); show(video, false);
      show(shoot, false); show(retake, true); show(accept, true);
    }, "image/jpeg", 0.92);
  }

  function reset() {
    blob = null;
    show(preview, false); show(video, true);
    show(shoot, true); show(retake, false); show(accept, false);
  }

  async function submit() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    accept.disabled = retake.disabled = true;
    accept.textContent = "Mengirim…";
    const form = new FormData();
    form.append("ktp", shots.ktp, "ktp.jpg");
    form.append("selfie", shots.selfie, "selfie.jpg");
    try {
      const res = await fetch(location.pathname + "/submit", {
        method: "POST", body: form, headers: { "X-CSRF-Token": csrf }, credentials: "same-origin",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((body.error && body.error.message) || res.statusText);
      if (body.redirect_url) { location.assign(body.redirect_url); return; }
      show($("capture"), false); show($("done"), true);
    } catch (e) {
      accept.disabled = retake.disabled = false;
      accept.textContent = "Kirim ulang";
      fail("Gagal mengirim: " + e.message);
    }
  }

  async function use() {
    shots[STEPS[step].key] = blob;
    if (step < STEPS.length - 1) {
      step += 1;
      reset();
      await startCamera();
    } else {
      await submit();
    }
  }

  if (!window.isSecureContext || !navigator.mediaDevices) {
    fail("Halaman ini membutuhkan HTTPS dan browser yang mendukung kamera.");
    shoot.disabled = true;
    return;
  }
  shoot.addEventListener("click", capture);
  retake.addEventListener("click", reset);
  accept.addEventListener("click", use);
  startCamera();
})();
