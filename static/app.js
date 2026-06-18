/* ── 상태 ── */
let facingMode = "user";          // 전면 기본
let selectedPresetId = null;      // null = 랜덤
let currentResultId   = null;
let currentResultUrl  = null;
let _autoReturnTimer  = null;     // 결과화면 자동 복귀 타이머
const AUTO_RETURN_SEC = 30;       // 30초 후 카메라 화면으로

/* ── 요소 참조 ── */
const video        = document.getElementById("video");
const loadingImg   = document.getElementById("loading-preview");
const loadingName  = document.getElementById("loading-preset-name");
const resultImg    = document.getElementById("result-image");

/* ── 화면 전환 ── */
const screens = {
  setup:   document.getElementById("screen-setup"),
  camera:  document.getElementById("screen-camera"),
  loading: document.getElementById("screen-loading"),
  result:  document.getElementById("screen-result"),
};

function showScreen(name) {
  Object.values(screens).forEach(s => s.classList.remove("active"));
  screens[name].classList.add("active");
  _clearAutoReturn();
  if (name === "result") _startAutoReturn();
}

function _startAutoReturn() {
  let remaining = AUTO_RETURN_SEC;
  const el = document.getElementById("auto-return-count");
  if (el) el.textContent = remaining;
  _autoReturnTimer = setInterval(() => {
    remaining--;
    if (el) el.textContent = remaining;
    if (remaining <= 0) _resetToCamera();
  }, 1000);
}

function _clearAutoReturn() {
  if (_autoReturnTimer) { clearInterval(_autoReturnTimer); _autoReturnTimer = null; }
  const el = document.getElementById("auto-return-count");
  if (el) el.textContent = AUTO_RETURN_SEC;
}

function _resetToCamera() {
  _clearAutoReturn();
  currentResultId   = null;
  currentResultUrl  = null;
  // 핸들러 먼저 해제 후 src 초기화 — 안 하면 onerror가 재실행되어 결과화면으로 되돌아옴
  resultImg.onload  = null;
  resultImg.onerror = null;
  resultImg.src     = "";
  document.getElementById("modal-qr").classList.add("hidden");
  showScreen("camera");
}

/* ── 카메라 ── */
async function startCamera(facing) {
  if (video.srcObject) {
    video.srcObject.getTracks().forEach(t => t.stop());
  }
  // 현재 화면 방향에 맞게 해상도 설정
  const landscape = window.innerWidth > window.innerHeight;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: facing,
        width:  { ideal: landscape ? 1024 : 768 },
        height: { ideal: landscape ? 768  : 1024 },
      },
      audio: false,
    });
    video.srcObject = stream;
    facingMode = facing;
    // 전면 카메라는 좌우 반전 표시 (셀카 미러링)
    video.classList.toggle("mirror", facing === "user");
  } catch (err) {
    alert("카메라를 사용할 수 없습니다: " + err.message);
  }
}

document.getElementById("btn-flip").addEventListener("click", () => {
  startCamera(facingMode === "user" ? "environment" : "user");
});

// 화면 회전 시 카메라 해상도 갱신
window.addEventListener("orientationchange", () => {
  setTimeout(() => startCamera(facingMode), 300);
});

/* ── 프리셋 로드 ── */
async function loadPresets() {
  let presets = [];
  try {
    presets = await fetch("/presets").then(r => r.json());
  } catch {
    // 프리셋 로드 실패 시 랜덤만 표시
  }
  const list = document.getElementById("preset-list");

  // 랜덤 카드 (기본 선택)
  list.appendChild(makeChip("랜덤", "", true, null));
  presets.forEach(p => list.appendChild(makeChip(p.name, p.id, false, p.id)));
}

function makeChip(label, id, selected, thumbId) {
  const chip = document.createElement("div");
  chip.className = "preset-chip" + (selected ? " selected" : "");
  chip.dataset.id = id;

  // 썸네일
  const thumb = document.createElement("img");
  thumb.className = "preset-thumb";
  thumb.alt = label;
  if (thumbId) {
    thumb.src = `/static/presets/${thumbId}.jpg`;
    thumb.onerror = () => {
      thumb.onerror = () => { thumb.src = "/static/presets/random_thumb.svg"; };
      thumb.src = `/static/presets/${thumbId}.svg`;
    };
  } else {
    thumb.src = "/static/presets/random_thumb.svg";
    thumb.onerror = () => {};
  }

  // 이름 라벨
  const labelEl = document.createElement("div");
  labelEl.className = "preset-label";
  labelEl.textContent = label;

  chip.appendChild(thumb);
  chip.appendChild(labelEl);

  chip.addEventListener("click", () => {
    document.querySelectorAll(".preset-chip").forEach(c => c.classList.remove("selected"));
    chip.classList.add("selected");
    selectedPresetId = id || null;
  });
  return chip;
}

/* ── 셔터음 (Web Audio API) ── */
function playShutterSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // 짧은 클릭음 (고주파 → 저주파)
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.setValueAtTime(1200, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(400, ctx.currentTime + 0.08);
    gain.gain.setValueAtTime(0.4, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.1);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.1);
  } catch { /* 오디오 미지원 환경 무시 */ }
}

/* ── 셔터 플래시 효과 ── */
function triggerFlash() {
  const el = document.createElement("div");
  el.className = "shutter-flash";
  document.body.appendChild(el);
  el.addEventListener("animationend", () => el.remove());
}

/* ── 카운트다운 후 촬영 ── */
let _shootingLock = false;  // 중복 촬영 방지

document.getElementById("btn-shutter").addEventListener("click", async () => {
  if (!video.videoWidth || _shootingLock) return;
  _shootingLock = true;

  const overlay  = document.getElementById("countdown-overlay");
  const numEl    = document.getElementById("countdown-num");
  const shutterBtn = document.getElementById("btn-shutter");
  const hintEl   = document.getElementById("shutter-hint");
  shutterBtn.disabled = true;

  // 5 → 4 → 3 → 2 → 1 카운트다운
  overlay.classList.remove("hidden");
  for (let i = 5; i >= 1; i--) {
    numEl.textContent = i;
    // 애니메이션 재시작
    numEl.style.animation = "none";
    numEl.offsetHeight;  // reflow
    numEl.style.animation = "";
    await new Promise(r => setTimeout(r, 900));
  }
  overlay.classList.add("hidden");

  // 셔터음 + 플래시
  playShutterSound();
  triggerFlash();

  // 사진 촬영
  const canvas = document.createElement("canvas");
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob(blob => {
    _shootingLock = false;
    shutterBtn.disabled = false;
    if (!blob) {
      alert("사진 캡처에 실패했습니다. 다시 시도해주세요.");
      return;
    }
    uploadSnap(blob);
  }, "image/jpeg", 0.9);
});

async function uploadSnap(blob) {
  // 로딩 화면 전환
  loadingImg.src = URL.createObjectURL(blob);
  const selectedChip = document.querySelector(".preset-chip.selected");
  const labelEl = selectedChip && selectedChip.querySelector(".preset-label");
  loadingName.textContent = labelEl ? labelEl.textContent : "변환 중...";
  showScreen("loading");

  // 서버 업로드 (90초 타임아웃)
  const form = new FormData();
  form.append("image", blob, "photo.jpg");
  if (selectedPresetId) form.append("preset_id", selectedPresetId);

  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), 90000);

  try {
    const resp = await fetch("/snap", { method: "POST", body: form, signal: ctrl.signal });
    clearTimeout(tid);
    if (!resp.ok) throw new Error(`서버 오류 ${resp.status}`);
    const data = await resp.json();

    currentResultId  = data.result_id;
    // QR은 항상 로컬(접속 호스트) origin 기반 — 외부 도메인 의존 제거
    currentResultUrl = window.location.origin + data.result_url;

    // 이미지 로드 (실패 시 최대 3회 재시도 — Railway 재시작 직후 대비)
    resultImg.onload = () => showScreen("result");
    let _retries = 0;
    resultImg.onerror = () => {
      if (_retries < 3) {
        _retries++;
        setTimeout(() => { resultImg.src = data.result_url + "?r=" + _retries; }, 1500);
      } else {
        alert("이미지를 불러오지 못했습니다. 다시 시도해주세요.");
        showScreen("camera");
      }
    };
    resultImg.src = data.result_url;
  } catch (err) {
    clearTimeout(tid);
    const msg = err.name === "AbortError"
      ? "변환 시간이 초과됐어요. 다시 시도해주세요."
      : "AI 변환에 실패했어요. 잠시 후 다시 시도해주세요.";
    alert(msg);
    showScreen("camera");
  }
}

/* ── 초기화 ── */
loadPresets();

async function boot() {
  const st = await fetch("/config/status").then(r => r.json()).catch(() => ({configured:false}));
  if (!st.configured) { showScreen("setup"); return; }
  showScreen("camera");
  startCamera("user");
}
boot();

/* ── 키 설정 저장 ── */
document.getElementById("setup-save").addEventListener("click", async () => {
  const provider = document.getElementById("setup-provider").value;
  const key = document.getElementById("setup-key").value.trim();
  const msg = document.getElementById("setup-msg");
  if (!key) { msg.textContent = "키를 입력하세요."; return; }
  msg.textContent = "확인 중...";
  const r = await fetch("/config/key", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({provider, key}),
  });
  if (r.ok) { boot(); }
  else { msg.textContent = "키가 올바르지 않거나 연결에 실패했습니다."; }
});

/* ── QR 코드 ── */
document.getElementById("btn-qr").addEventListener("click", () => {
  if (!currentResultUrl) return;
  const container = document.getElementById("qr-container");
  container.innerHTML = "";
  new QRCode(container, {
    text:   currentResultUrl,
    width:  220,
    height: 220,
  });
  document.getElementById("modal-qr").classList.remove("hidden");
});

document.getElementById("btn-qr-close").addEventListener("click", () => {
  document.getElementById("modal-qr").classList.add("hidden");
});

/* ── 인쇄 ── */
document.getElementById("btn-print").addEventListener("click", () => window.print());

/* ── 다운로드(저장) ── */
document.getElementById("btn-download").addEventListener("click", async () => {
  if (!currentResultId) return;
  const blob = await fetch(`/result/${currentResultId}`).then(r => r.blob());
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `itsyou_${currentResultId.slice(0,8)}.jpg`;
  document.body.appendChild(a); a.click(); a.remove();
});

/* ── Web Share API ── */
document.getElementById("btn-share").addEventListener("click", async () => {
  if (!currentResultId || !currentResultUrl) return;
  if (!navigator.share) {
    alert("이 브라우저에서는 공유하기가 지원되지 않습니다.");
    return;
  }
  try {
    const blob = await fetch(`/result/${currentResultId}`).then(r => r.blob());
    const file = new File([blob], "itsyou.jpg", { type: "image/jpeg" });
    await navigator.share({ title: "itsyou — AI 변환 사진", files: [file] });
  } catch (err) {
    if (err.name !== "AbortError") {
      // 파일 공유 실패 시 URL 공유 폴백
      try { await navigator.share({ url: currentResultUrl }); }
      catch (e) { if (e.name !== "AbortError") alert("공유에 실패했습니다."); }
    }
  }
});

/* ── 프롬프트 수정 (키오스크 운영자용, 선택된 스타일 대상) ── */
const promptModal = document.getElementById("modal-prompt");
const promptTextarea = document.getElementById("prompt-textarea");
const promptMsg = document.getElementById("prompt-modal-msg");

document.getElementById("btn-edit-prompt").addEventListener("click", async () => {
  if (!selectedPresetId) {
    alert("‘랜덤’이 아닌 스타일을 먼저 선택하면 그 스타일의 프롬프트를 수정할 수 있어요.");
    return;
  }
  const chip = document.querySelector(".preset-chip.selected .preset-label");
  document.getElementById("prompt-modal-title").textContent =
    `프롬프트 수정 — ${chip ? chip.textContent : ""}`;
  promptMsg.textContent = "불러오는 중...";
  promptTextarea.value = "";
  promptModal.classList.remove("hidden");
  try {
    const r = await fetch(`/kiosk/preset/${selectedPresetId}`);
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    promptTextarea.value = data.prompt || "";
    promptMsg.textContent = "";
  } catch {
    promptMsg.textContent = "프롬프트를 불러오지 못했습니다.";
  }
});

document.getElementById("btn-prompt-save").addEventListener("click", async () => {
  if (!selectedPresetId) return;
  const prompt = promptTextarea.value.trim();
  if (!prompt) { promptMsg.textContent = "프롬프트를 입력하세요."; return; }
  promptMsg.textContent = "저장 중...";
  try {
    const r = await fetch(`/kiosk/preset/${selectedPresetId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    if (!r.ok) throw new Error(r.status);
    promptMsg.textContent = "저장되었습니다. 다음 촬영부터 반영됩니다.";
    setTimeout(() => promptModal.classList.add("hidden"), 800);
  } catch {
    promptMsg.textContent = "저장에 실패했습니다.";
  }
});

document.getElementById("btn-prompt-close").addEventListener("click", () => {
  promptModal.classList.add("hidden");
});

/* ── 다시 찍기 ── */
document.getElementById("btn-retry").addEventListener("click", _resetToCamera);

/* ── 전체화면 ── */
const btnFs = document.getElementById("btn-fullscreen");
function updateFsIcon() {
  btnFs.textContent = document.fullscreenElement ? "✕" : "⛶";
}
btnFs.addEventListener("click", () => {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
});
document.addEventListener("fullscreenchange", updateFsIcon);
