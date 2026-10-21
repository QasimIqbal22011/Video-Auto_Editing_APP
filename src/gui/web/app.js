// Video Helper Studio - Frontend Client Logic

let audioClips = [];
let currentPlayingPath = null;
let activeAudioObj = null;
let lastStitchedResult = null;
let lastStitchNormalizationApplied = false;

// DOM Elements
const docRoot = document.documentElement;
const themeToggleBtn = document.getElementById('themeToggleBtn');
const themeText = document.getElementById('themeText');
const dropzone = document.getElementById('dropzone');
const clipsList = document.getElementById('clipsList');
const addFilesBtn = document.getElementById('addFilesBtn');
const clearAllBtn = document.getElementById('clearAllBtn');
const sortNaturalBtn = document.getElementById('sortNaturalBtn');
const statClipCount = document.getElementById('statClipCount');
const statTotalDuration = document.getElementById('statTotalDuration');
const htmlAudioInput = document.getElementById('htmlAudioInput');

// Settings Elements
const gapSlider = document.getElementById('gapSlider');
const gapDisplay = document.getElementById('gapDisplay');
const gapChips = document.querySelectorAll('.chip[data-gap]');
const normalizeCheckbox = document.getElementById('normalizeAudio');
const formatRadios = document.querySelectorAll('input[name="outFormat"]');
const sampleRateSelect = document.getElementById('sampleRateSelect');
const outputPathInput = document.getElementById('outputPathInput');
const browseDestBtn = document.getElementById('browseDestBtn');
const startStitchBtn = document.getElementById('startStitchBtn');

// Progress & Success Cards
const progressCard = document.getElementById('progressCard');
const progressStatusText = document.getElementById('progressStatusText');
const progressPercent = document.getElementById('progressPercent');
const progressBarFill = document.getElementById('progressBarFill');
const cancelStitchBtn = document.getElementById('cancelStitchBtn');

const successCard = document.getElementById('successCard');
const successDetails = document.getElementById('successDetails');
const masterAudioPlayer = document.getElementById('masterAudioPlayer');
const openFolderBtn = document.getElementById('openFolderBtn');
const dismissSuccessBtn = document.getElementById('dismissSuccessBtn');
const gpuLabel = document.getElementById('gpuLabel');

// Splash Elements
const splashScreen = document.getElementById('splashScreen');
const splashFill = document.getElementById('splashFill');
const splashStatusText = document.getElementById('splashStatusText');

// Global Client Error Logger to debug_log.txt
function sendClientLog(level, message) {
  try {
    fetch('/client_log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level: level, message: message })
    }).catch(() => {});
  } catch (e) {}
}

window.onerror = function(msg, url, lineNo, columnNo, error) {
  const errStr = `${msg} at ${url}:${lineNo}:${columnNo} - ${error ? error.stack : ''}`;
  console.error(errStr);
  sendClientLog('error', errStr);
  return false;
};

window.onunhandledrejection = function(event) {
  const reason = event.reason ? (event.reason.stack || event.reason) : 'Unknown rejection';
  console.error('Unhandled Promise Rejection:', reason);
  sendClientLog('error', 'Unhandled Promise Rejection: ' + reason);
};

// Shared reference-style audio preview used by every workflow engine.
function initSharedAudioPreviews() {
  const audioIds = ['masterAudioPlayer', 'silenceOriginalPlayer', 'silenceCleanPlayer', 'timelineAudioPlayer'];
  const bars = [18, 27, 38, 24, 48, 33, 58, 42, 66, 36, 54, 72, 44, 61, 35, 52, 70, 43, 29, 47, 63, 35, 55, 74, 42, 31, 57, 68, 46, 60, 35, 51, 72, 38, 26, 48, 65, 42, 58, 31, 49, 67, 39, 55, 72, 44, 29, 50, 64];
  const formatTime = seconds => {
    if (!Number.isFinite(seconds)) return '00:00';
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  };

  audioIds.forEach(audioId => {
    const audio = document.getElementById(audioId);
    if (!audio || audio.closest('.reference-audio-player')) return;
    audio.controls = false;
    audio.classList.add('shared-audio-native');
    const player = document.createElement('div');
    player.className = 'reference-audio-player shared-audio-player';
    player.innerHTML = `
      <div class="audio-waveform" aria-hidden="true">${bars.map(height => `<span style="height:${height}%"></span>`).join('')}</div>
      <div class="audio-transport-row">
        <button class="audio-play-button" type="button" aria-label="Play audio">
          <svg class="play-glyph" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.2v13.6c0 .8.9 1.3 1.6.9l10.2-6.8c.6-.4.6-1.3 0-1.7L9.6 4.3C8.9 3.9 8 4.4 8 5.2z"/></svg>
          <svg class="pause-glyph" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h3v14H7zM14 5h3v14h-3z"/></svg>
        </button>
        <span class="audio-time audio-current">00:00</span>
        <input type="range" class="audio-seek" min="0" max="100" value="0" aria-label="Audio playback position">
        <span class="audio-time audio-duration">00:00</span>
        <button class="audio-volume-button" type="button" aria-label="Toggle mute">⌁</button>
        <button class="audio-more-button" type="button" aria-label="Audio options">⋮</button>
      </div>`;
    audio.parentNode.insertBefore(player, audio);
    player.appendChild(audio);
    const play = player.querySelector('.audio-play-button');
    const seek = player.querySelector('.audio-seek');
    const current = player.querySelector('.audio-current');
    const duration = player.querySelector('.audio-duration');
    const volume = player.querySelector('.audio-volume-button');
    const sync = () => {
      const total = audio.duration || 0;
      const percent = total ? (audio.currentTime / total) * 100 : 0;
      seek.value = percent;
      seek.style.setProperty('--seek-progress', `${percent}%`);
      current.textContent = formatTime(audio.currentTime);
      duration.textContent = formatTime(total);
      player.classList.toggle('audio-playing', !audio.paused);
    };
    play.addEventListener('click', () => audio.paused ? audio.play() : audio.pause());
    seek.addEventListener('input', () => {
      if (audio.duration) audio.currentTime = (parseFloat(seek.value) / 100) * audio.duration;
      sync();
    });
    volume.addEventListener('click', () => {
      audio.muted = !audio.muted;
      volume.classList.toggle('muted', audio.muted);
    });
    ['loadedmetadata', 'timeupdate', 'play', 'pause', 'ended'].forEach(eventName => audio.addEventListener(eventName, sync));
    sync();
  });
}

// =============================================================================
// 1. THEME MANAGEMENT
// =============================================================================
function initTheme() {
  const savedTheme = localStorage.getItem('vh_theme') || 'dark';
  applyTheme(savedTheme);

  themeToggleBtn.addEventListener('click', () => {
    const current = docRoot.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });
}

function applyTheme(theme) {
  docRoot.setAttribute('data-theme', theme);
  localStorage.setItem('vh_theme', theme);
  themeText.textContent = theme === 'dark' ? 'Dark Obsidian' : 'Crisp Light';
}

// =============================================================================
// 2. INITIALIZATION & HARDWARE DETECTION (OpenVox Style Splash Loading)
// =============================================================================
window.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initSharedAudioPreviews();
  initSettings();
  setupEventListeners();
  initSilenceRemover();
  initTimelineAligner();
  initZoomTransitions();
  initAppLoadingSequence();
});

async function initAppLoadingSequence() {
  function updateSplash(percent, text) {
    if (splashFill) splashFill.style.width = `${percent}%`;
    if (splashStatusText) splashStatusText.textContent = text;
  }

  updateSplash(20, "Connecting to local backend engines...");

  let initialized = false;

  async function checkBackend() {
    if (initialized) return;

    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.initialize_app === 'function') {
      try {
        updateSplash(50, "Verifying FFmpeg hardware acceleration...");
        const res = await window.pywebview.api.initialize_app();
        
        updateSplash(80, "Checking NVIDIA RTX 3060 12GB NVENC...");
        if (res && res.gpu) {
          gpuLabel.textContent = `${res.gpu} • Ready`;
        }

        await new Promise(r => setTimeout(r, 300));
        updateSplash(100, "Hardware Acceleration Ready!");

        await new Promise(r => setTimeout(r, 400));
        dismissSplashScreen();
        suggestDefaultOutputPath();
        initialized = true;
        return;
      } catch (err) {
        console.error("Initialization error:", err);
      }
    }

    if (!initialized) {
      setTimeout(checkBackend, 100);
    }
  }

  window.addEventListener('pywebviewready', () => {
    checkBackend();
  });

  checkBackend();
}

function dismissSplashScreen() {
  if (splashScreen && !splashScreen.classList.contains('fade-out')) {
    splashScreen.classList.add('fade-out');
    setTimeout(() => {
      splashScreen.style.display = 'none';
    }, 550);
  }
}

function suggestDefaultOutputPath() {
  if (!outputPathInput) return;
  const fmt = getSelectedFormat();
  const timestamp = new Date().toISOString().slice(0, 10);
  const defaultPath = `master_audio_${timestamp}.${fmt}`;
  if (!outputPathInput.value) {
    outputPathInput.value = defaultPath;
  }
}

// =============================================================================
// 3. SETTINGS & PARAMETERS
// =============================================================================

// Keeps the .active highlight on radio cards in sync with the real checked
// state. Listens to click as well as change, because clicking an
// already-selected radio fires neither change nor any restore path — the card
// could otherwise stay unhighlighted. Syncs once on setup so no handler can
// leave a group without a visible selection. Scoped to the group's own view
// panel so it never strips highlights from other engines' radio cards.
function setupRadioCardGroup(name) {
  const firstRadio = document.querySelector(`input[name="${name}"]`);
  if (!firstRadio) return;
  const panel = firstRadio.closest('.view-panel') || document;
  const sync = () => {
    panel.querySelectorAll('.radio-card').forEach(card => {
      const input = card.querySelector(`input[name="${name}"]`);
      if (input) card.classList.toggle('active', input.checked);
    });
  };
  panel.querySelectorAll(`input[name="${name}"]`).forEach(radio => {
    radio.addEventListener('click', sync);
    radio.addEventListener('change', sync);
  });
  sync();
}

function initSettings() {
  // Gap slider & chips sync
  gapSlider.addEventListener('input', (e) => {
    updateGapValue(parseFloat(e.target.value));
  });

  gapChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const val = parseFloat(chip.getAttribute('data-gap'));
      gapSlider.value = val;
      updateGapValue(val);
    });
  });

  // Format selection
  formatRadios.forEach(radio => {
    radio.addEventListener('change', () => {
      updateOutputPathExtension(radio.value);
    });
  });
  setupRadioCardGroup('outFormat');

  // Browse output destination
  if (browseDestBtn) browseDestBtn.addEventListener('click', async () => {
    sendClientLog('info', 'User clicked Browse Destination');
    if (!window.pywebview || !window.pywebview.api) {
      sendClientLog('warn', 'pywebview API not ready yet for browse destination');
      return;
    }
    const currentName = outputPathInput.value.split(/[\\/]/).pop() || "master_audio.mp3";
    try {
      const selected = await window.pywebview.api.select_save_destination(currentName);
      if (selected) {
        outputPathInput.value = selected;
        sendClientLog('info', `Destination selected: ${selected}`);
      }
    } catch (err) {
      sendClientLog('error', `Error in select_save_destination: ${err}`);
      alert("Could not open save dialog: " + err);
    }
  });
}

function updateGapValue(val) {
  gapDisplay.textContent = `${val.toFixed(2)}s`;
  gapChips.forEach(chip => {
    const chipVal = parseFloat(chip.getAttribute('data-gap'));
    if (Math.abs(chipVal - val) < 0.04) {
      chip.classList.add('active');
    } else {
      chip.classList.remove('active');
    }
  });
  updateStats();
}

function getSelectedFormat() {
  const checked = document.querySelector('input[name="outFormat"]:checked');
  return checked ? checked.value : 'mp3';
}

function updateOutputPathExtension(ext) {
  if (!outputPathInput) return;
  let cur = outputPathInput.value;
  if (cur) {
    const dotIdx = cur.lastIndexOf('.');
    if (dotIdx !== -1) {
      outputPathInput.value = cur.substring(0, dotIdx + 1) + ext;
    } else {
      outputPathInput.value = cur + '.' + ext;
    }
  }
}

// =============================================================================
// 4. CLIP LIST MANAGEMENT & RENDERING
// =============================================================================
function setupEventListeners() {
  // Prevent browser default file open behavior across the whole window
  window.addEventListener('dragover', (e) => e.preventDefault(), false);
  window.addEventListener('drop', (e) => e.preventDefault(), false);

  // Sidebar navigation tab switcher
  document.querySelectorAll('.nav-item[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.classList.contains('disabled')) return;
      const tab = btn.getAttribute('data-tab');
      switchTab(tab);
    });
  });

  // File add triggers
  addFilesBtn.addEventListener('click', openAddFilesDialog);
  dropzone.addEventListener('click', openAddFilesDialog);

  // HTML File Input fallback listener
  if (htmlAudioInput) {
    htmlAudioInput.addEventListener('change', async (e) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        const directPaths = Array.from(files).map(file => file.path).filter(Boolean);
        let importedDirectly = false;
        if (directPaths.length === files.length && window.pywebview && window.pywebview.api) {
          try {
            const metadata = await window.pywebview.api.inspect_dropped_files(directPaths);
            if (metadata.length === files.length && metadata.every(item => !item.error)) {
              appendClips(metadata);
              importedDirectly = true;
            }
          } catch (err) {
            console.warn("Direct audio inspection failed; using batched upload:", err);
          }
        }
        if (!importedDirectly) await uploadFilesToServer(files);
        htmlAudioInput.value = ''; // Reset so the same file can be selected again
      }
    });
  }

  // Clear all
  clearAllBtn.addEventListener('click', () => {
    if (audioClips.length === 0) return;
    if (confirm("Remove all clips from the stitcher list?")) {
      stopPlayback();
      audioClips = [];
      renderClips();
    }
  });

  // Natural Sort
  sortNaturalBtn.addEventListener('click', () => {
    if (audioClips.length <= 1) return;
    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
    audioClips.sort((a, b) => collator.compare(a.filename, b.filename));
    renderClips();
  });

  // Drag and drop onto dropzone
  ['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('drag-over');
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('drag-over');
    });
  });

  dropzone.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const files = e.dataTransfer.files;
    if (!files || files.length === 0) return;

    // Check if files contain a direct filesystem path (pywebview / desktop environment)
    const paths = [];
    for (let i = 0; i < files.length; i++) {
      if (files[i].path) {
        paths.push(files[i].path);
      }
    }

    if (paths.length === files.length && window.pywebview && window.pywebview.api) {
      try {
        const metadataList = await window.pywebview.api.inspect_dropped_files(paths);
        if (metadataList.length === files.length && metadataList.every(item => !item.error)) {
          appendClips(metadataList);
          return;
        }
      } catch (err) {
        console.warn("inspect_dropped_files failed, falling back to upload:", err);
      }
    }

    // Standard web Drag & Drop (WebView2 security hides absolute path) -> Upload directly
    await uploadFilesToServer(files);
  });

  // Start stitch
  startStitchBtn.addEventListener('click', executeStitch);

  // Cancel stitch
  cancelStitchBtn.addEventListener('click', async () => {
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.cancel_stitch();
    }
  });

  // Success actions
  openFolderBtn.addEventListener('click', async () => {
    if (lastStitchedResult && window.pywebview && window.pywebview.api) {
      const destination = await window.pywebview.api.select_save_destination(
        lastStitchedResult.filename || 'stitched_master.mp3'
      );
      if (!destination) return;
      const saved = await window.pywebview.api.copy_media_to_destination(lastStitchedResult.path, destination);
      if (!saved?.success) alert(`Could not download audio: ${saved?.error || 'Unknown error'}`);
    }
  });

  const sendStitchToTimelineBtn = document.getElementById('sendStitchToTimelineBtn');
  if (sendStitchToTimelineBtn) {
    sendStitchToTimelineBtn.addEventListener('click', () => {
      if (!lastStitchedResult) return;
      setTimelineActiveAudio(lastStitchedResult);
      switchTab('timeline');
      successCard.style.display = 'none';
      if (masterAudioPlayer) {
        masterAudioPlayer.pause();
      }
    });
  }

  dismissSuccessBtn.addEventListener('click', () => {
    successCard.style.display = 'none';
    if (masterAudioPlayer) {
      masterAudioPlayer.pause();
    }
  });
}

async function openAddFilesDialog() {
  sendClientLog("info", "User triggered Add Audio Clips / Dropzone click");
  if (htmlAudioInput) {
    htmlAudioInput.click();
  }
}

const MAX_MULTIPART_BATCH_BYTES = 48 * 1024 * 1024;

function splitFilesBySize(fileList, maxBytes = MAX_MULTIPART_BATCH_BYTES) {
  const batches = [];
  let batch = [];
  let batchBytes = 0;
  for (const file of Array.from(fileList || [])) {
    const fileBytes = Number(file.size) || 0;
    if (batch.length && batchBytes + fileBytes > maxBytes) {
      batches.push(batch);
      batch = [];
      batchBytes = 0;
    }
    batch.push(file);
    batchBytes += fileBytes;
    // Isolate a single oversized file; Bottle will spool it after its memory threshold.
    if (batchBytes >= maxBytes) {
      batches.push(batch);
      batch = [];
      batchBytes = 0;
    }
  }
  if (batch.length) batches.push(batch);
  return batches;
}

async function uploadInBatches(files, endpoint, fieldName, resultKey, onProgress) {
  const batches = splitFilesBySize(files);
  const imported = [];
  let completedFiles = 0;
  for (const batch of batches) {
    try {
      const formData = new FormData();
      batch.forEach(file => formData.append(fieldName, file, file.name));
      const response = await fetch(endpoint, { method: 'POST', body: formData });
      const rawText = await response.text();
      let data;
      try {
        data = JSON.parse(rawText);
      } catch (_) {
        throw new Error(`Server status ${response.status}: ${rawText.slice(0, 150)}`);
      }
      if (!response.ok || !data.success) {
        throw new Error(data.error || `Upload failed with status ${response.status}`);
      }
      imported.push(...(data[resultKey] || []));
      completedFiles += batch.length;
      if (onProgress) onProgress(completedFiles, files.length);
    } catch (error) {
      return { items: imported, error, completedFiles };
    }
  }
  return { items: imported, error: null, completedFiles };
}

async function uploadFilesToServer(files) {
  const validCount = files ? files.length : 0;
  if (validCount === 0) return;
  const importProgress = document.getElementById('audioImportProgress');
  const importText = document.getElementById('audioImportProgressText');
  const importBar = document.getElementById('audioImportProgressBar');
  importProgress.hidden = false;
  importText.textContent = `Importing ${validCount} audio file(s)...`;
  importBar.value = 0;
  try {
    const result = await uploadInBatches(files, '/upload_audio', 'audio_files', 'clips', (done, total) => {
      importText.textContent = `Imported ${done} of ${total} audio files...`;
      importBar.value = total ? (done / total) * 100 : 100;
    });
    if (result.items.length) appendClips(result.items);
    if (result.error) throw result.error;
  } catch (err) {
    console.error("Upload error:", err);
    alert("Could not import audio files:\n" + err.message);
  } finally {
    importProgress.hidden = true;
    importBar.value = 0;
  }
}

function appendClips(newClips) {
  newClips.forEach(clip => {
    if (!clip.error) {
      audioClips.push(clip);
    }
  });
  renderClips();
}

function renderClips() {
  if (audioClips.length === 0) {
    dropzone.style.display = 'block';
    clipsList.style.display = 'none';
    clipsList.replaceChildren();
  } else {
    dropzone.style.display = 'none';
    clipsList.style.display = 'flex';
    const fragment = document.createDocumentFragment();
    audioClips.forEach((clip, index) => {
      const card = createClipCard(clip, index);
      fragment.appendChild(card);
    });
    clipsList.replaceChildren(fragment);
  }

  updateStats();
}

function createClipCard(clip, index) {
  const card = document.createElement('div');
  card.className = 'clip-card';
  card.draggable = true;
  card.dataset.index = index;

  const isPlaying = (currentPlayingPath === clip.path);

  card.innerHTML = `
    <div class="drag-handle" title="Drag to reorder sequence">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
        <circle cx="9" cy="6" r="1"></circle>
        <circle cx="15" cy="6" r="1"></circle>
        <circle cx="9" cy="12" r="1"></circle>
        <circle cx="15" cy="12" r="1"></circle>
        <circle cx="9" cy="18" r="1"></circle>
        <circle cx="15" cy="18" r="1"></circle>
      </svg>
    </div>
    <div class="clip-index-badge">#${index + 1}</div>
    <div class="clip-info">
      <div class="clip-name" title="${clip.path}">${clip.filename}</div>
      <div class="clip-meta-row">
        <span class="meta-pill">${clip.format_name}</span>
        <span class="meta-pill">${clip.duration_formatted}</span>
        <span class="meta-pill">${(clip.sample_rate / 1000).toFixed(1)} kHz</span>
        <span class="meta-pill">${clip.size_formatted}</span>
      </div>
    </div>
    <div class="clip-controls">
      <button class="clip-play-btn" data-action="play" data-index="${index}" title="${isPlaying ? 'Pause' : 'Play Preview'}">
        ${isPlaying ? `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="6" y="4" width="4" height="16"></rect>
            <rect x="14" y="4" width="4" height="16"></rect>
          </svg>
        ` : `
          <svg viewBox="0 0 24 24" fill="currentColor">
            <polygon points="5 3 19 12 5 21 5 3"></polygon>
          </svg>
        `}
      </button>
      <button class="clip-order-btn" data-action="up" data-index="${index}" title="Move Up" ${index === 0 ? 'disabled style="opacity:0.3"' : ''}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="18 15 12 9 6 15"></polyline>
        </svg>
      </button>
      <button class="clip-order-btn" data-action="down" data-index="${index}" title="Move Down" ${index === audioClips.length - 1 ? 'disabled style="opacity:0.3"' : ''}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>
      <button class="clip-del-btn" data-action="delete" data-index="${index}" title="Remove Clip">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    </div>
  `;

  // Attach button events
  card.querySelector('[data-action="play"]').addEventListener('click', (e) => {
    e.stopPropagation();
    togglePlayClip(clip);
  });

  const upBtn = card.querySelector('[data-action="up"]');
  if (upBtn) {
    upBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      moveClip(index, index - 1);
    });
  }

  const downBtn = card.querySelector('[data-action="down"]');
  if (downBtn) {
    downBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      moveClip(index, index + 1);
    });
  }

  card.querySelector('[data-action="delete"]').addEventListener('click', (e) => {
    e.stopPropagation();
    removeClip(index);
  });

  // Drag to reorder
  setupDragReordering(card, index);

  return card;
}

function moveClip(fromIdx, toIdx) {
  if (toIdx < 0 || toIdx >= audioClips.length) return;
  const item = audioClips.splice(fromIdx, 1)[0];
  audioClips.splice(toIdx, 0, item);
  renderClips();
}

function removeClip(idx) {
  if (idx < 0 || idx >= audioClips.length) return;
  if (currentPlayingPath === audioClips[idx].path) {
    stopPlayback();
  }
  audioClips.splice(idx, 1);
  renderClips();
}

// Drag & Drop reordering between cards
let draggedCardIndex = null;

function setupDragReordering(card, index) {
  card.addEventListener('dragstart', (e) => {
    draggedCardIndex = index;
    card.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });

  card.addEventListener('dragend', () => {
    card.classList.remove('dragging');
    draggedCardIndex = null;
  });

  card.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  });

  card.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (draggedCardIndex !== null && draggedCardIndex !== index) {
      moveClip(draggedCardIndex, index);
    }
  });
}

// =============================================================================
// 5. AUDIO PLAYBACK PREVIEW
// =============================================================================
function togglePlayClip(clip) {
  if (currentPlayingPath === clip.path && activeAudioObj && !activeAudioObj.paused) {
    stopPlayback();
    renderClips();
    return;
  }

  stopPlayback();

  const streamUrl = `/stream_audio?path=${encodeURIComponent(clip.path)}`;
  activeAudioObj = new Audio(streamUrl);
  currentPlayingPath = clip.path;

  activeAudioObj.play().catch(err => {
    console.error("Playback error:", err);
    stopPlayback();
  });

  activeAudioObj.onended = () => {
    stopPlayback();
    renderClips();
  };

  renderClips();
}

function stopPlayback() {
  if (activeAudioObj) {
    activeAudioObj.pause();
    activeAudioObj = null;
  }
  currentPlayingPath = null;
}

// =============================================================================
// 6. STATS CALCULATION
// =============================================================================
function updateStats() {
  statClipCount.textContent = audioClips.length;

  if (audioClips.length === 0) {
    statTotalDuration.textContent = "00:00";
    return;
  }

  const gap = parseFloat(gapSlider.value) || 0;
  const clipDurations = audioClips.reduce((sum, c) => sum + (c.duration || 0), 0);
  const totalGaps = Math.max(0, audioClips.length - 1) * gap;
  const totalSec = Math.round(clipDurations + totalGaps);

  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  statTotalDuration.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

// =============================================================================
// 7. STITCH EXECUTION & BACKGROUND CALLBACKS
// =============================================================================
async function executeStitch() {
  if (audioClips.length === 0) {
    alert("Please add at least one audio clip to stitch.");
    return;
  }

  stopPlayback();

  const filePaths = audioClips.map(c => c.path);
  const gapSec = parseFloat(gapSlider.value) || 0.0;
  const normalize = normalizeCheckbox.checked;
  lastStitchNormalizationApplied = normalize;
  const format = getSelectedFormat();
  const sampleRate = parseInt(sampleRateSelect.value, 10) || 48000;
  const bitrate = 320;

  // Show progress card
  progressCard.style.display = 'flex';
  successCard.style.display = 'none';
  startStitchBtn.disabled = true;
  progressBarFill.style.width = '5%';
  progressPercent.textContent = '5%';
  progressStatusText.textContent = "Initializing stitching pipeline...";

  try {
    await window.pywebview.api.start_stitch_internal(
      filePaths,
      gapSec,
      normalize,
      sampleRate,
      format,
      bitrate
    );
  } catch (err) {
    alert("Error starting stitch: " + err);
    resetProgress();
  }
}

// Global callbacks evaluated from Python ApiBridge
window.onStitchProgress = function(percent, statusMsg) {
  progressBarFill.style.width = `${percent}%`;
  progressPercent.textContent = `${Math.round(percent)}%`;
  progressStatusText.textContent = statusMsg;
};

window.onStitchComplete = function(result) {
  resetProgress();
  lastStitchedResult = {
    ...result,
    normalization_applied: lastStitchNormalizationApplied
  };

  // Show success card
  successCard.style.display = 'block';
  successDetails.textContent = `Total: ${result.duration_formatted} • ${result.size_formatted} • ${result.filename}`;

  // Load preview in master player
  const streamUrl = `/stream_audio?path=${encodeURIComponent(result.path)}&t=${Date.now()}`;
  masterAudioPlayer.src = streamUrl;
  masterAudioPlayer.load();
};

window.onStitchCancelled = function() {
  resetProgress();
  alert("Audio stitching was cancelled.");
};

window.onStitchError = function(errMsg) {
  resetProgress();
  alert("Stitching Error:\n" + errMsg);
};

function resetProgress() {
  progressCard.style.display = 'none';
  startStitchBtn.disabled = false;
  progressBarFill.style.width = '0%';
  progressPercent.textContent = '0%';
}

// =============================================================================
// 8. TAB NAVIGATION
// =============================================================================
function switchTab(tabName) {
  document.querySelectorAll('.nav-item[data-tab]').forEach(btn => {
    if (btn.getAttribute('data-tab') === tabName) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  const stitcherView = document.getElementById('stitcherView');
  const silenceView = document.getElementById('silenceView');
  const timelineView = document.getElementById('timelineView');
  const zoomView = document.getElementById('zoomView');

  if (stitcherView) stitcherView.style.display = (tabName === 'stitcher') ? 'flex' : 'none';
  if (silenceView) silenceView.style.display = (tabName === 'silence') ? 'flex' : 'none';
  if (timelineView) timelineView.style.display = (tabName === 'timeline') ? 'flex' : 'none';
  if (zoomView) zoomView.style.display = (tabName === 'zoom') ? 'flex' : 'none';
}

// =============================================================================
// 9. SILENCE REMOVER CONTROLLER
// =============================================================================
let silenceActiveFile = null;
let lastSilenceResult = null;
let silenceAnalysisDebounce = null;

// DOM Elements
let silenceDropzone, silenceFileInput, silenceAudioCard, silenceFileName;
let silenceFileFmt, silenceFileDur, silenceFileSr, silenceFileSize;
let changeSilenceFileBtn, silenceOriginalPlayer, silenceOriginalVideoPlayer;
let silenceCountStat, silenceSavedStat, silenceNewDurStat;
let silenceTelemetryCard, silenceBadgeStats, silenceListContainer;
let thresholdSlider, threshDisplay, threshChips;
let minDurationSlider, minDurDisplay, durChips;
let marginSlider, marginDisplay, marginChips;
let silenceNormalize, silenceFormatRadios, silenceOutputPathInput, browseSilenceDestBtn;
let analyzeSilencesBtn, startSilenceRemovalBtn;
let silenceProgressCard, silenceProgressStatusText, silenceProgressPercent, silenceProgressBarFill, cancelSilenceBtn;
let silenceSuccessCard, silenceSuccessDetails, silenceCleanPlayer, silenceCleanVideoPlayer;
let openSilenceFolderBtn, sendToStitcherBtn, dismissSilenceSuccessBtn;
let sendSilenceVideoToZoomBtn, saveSilenceVideoBtn, silenceAudioOutputSettings, silenceVideoOutputNote;

function initSilenceRemover() {
  silenceDropzone = document.getElementById('silenceDropzone');
  silenceFileInput = document.getElementById('silenceFileInput');
  silenceAudioCard = document.getElementById('silenceAudioCard');
  silenceFileName = document.getElementById('silenceFileName');
  silenceFileFmt = document.getElementById('silenceFileFmt');
  silenceFileDur = document.getElementById('silenceFileDur');
  silenceFileSr = document.getElementById('silenceFileSr');
  silenceFileSize = document.getElementById('silenceFileSize');
  changeSilenceFileBtn = document.getElementById('changeSilenceFileBtn');
  silenceOriginalPlayer = document.getElementById('silenceOriginalPlayer');
  silenceOriginalVideoPlayer = document.getElementById('silenceOriginalVideoPlayer');

  silenceCountStat = document.getElementById('silenceCountStat');
  silenceSavedStat = document.getElementById('silenceSavedStat');
  silenceNewDurStat = document.getElementById('silenceNewDurStat');

  silenceTelemetryCard = document.getElementById('silenceTelemetryCard');
  silenceBadgeStats = document.getElementById('silenceBadgeStats');
  silenceListContainer = document.getElementById('silenceListContainer');

  thresholdSlider = document.getElementById('thresholdSlider');
  threshDisplay = document.getElementById('threshDisplay');
  threshChips = document.querySelectorAll('.chip[data-thresh]');

  minDurationSlider = document.getElementById('minDurationSlider');
  minDurDisplay = document.getElementById('minDurDisplay');
  durChips = document.querySelectorAll('.chip[data-dur]');

  marginSlider = document.getElementById('marginSlider');
  marginDisplay = document.getElementById('marginDisplay');
  marginChips = document.querySelectorAll('.chip[data-margin]');

  silenceNormalize = document.getElementById('silenceNormalize');
  silenceFormatRadios = document.querySelectorAll('input[name="silenceFormat"]');
  silenceOutputPathInput = document.getElementById('silenceOutputPathInput');
  browseSilenceDestBtn = document.getElementById('browseSilenceDestBtn');

  analyzeSilencesBtn = document.getElementById('analyzeSilencesBtn');
  startSilenceRemovalBtn = document.getElementById('startSilenceRemovalBtn');

  silenceProgressCard = document.getElementById('silenceProgressCard');
  silenceProgressStatusText = document.getElementById('silenceProgressStatusText');
  silenceProgressPercent = document.getElementById('silenceProgressPercent');
  silenceProgressBarFill = document.getElementById('silenceProgressBarFill');
  cancelSilenceBtn = document.getElementById('cancelSilenceBtn');

  silenceSuccessCard = document.getElementById('silenceSuccessCard');
  silenceSuccessDetails = document.getElementById('silenceSuccessDetails');
  silenceCleanPlayer = document.getElementById('silenceCleanPlayer');
  silenceCleanVideoPlayer = document.getElementById('silenceCleanVideoPlayer');
  sendSilenceVideoToZoomBtn = document.getElementById('sendSilenceVideoToZoomBtn');
  saveSilenceVideoBtn = document.getElementById('saveSilenceVideoBtn');
  silenceAudioOutputSettings = document.getElementById('silenceAudioOutputSettings');
  silenceVideoOutputNote = document.getElementById('silenceVideoOutputNote');
  openSilenceFolderBtn = document.getElementById('openSilenceFolderBtn');
  sendToStitcherBtn = document.getElementById('sendToStitcherBtn');
  dismissSilenceSuccessBtn = document.getElementById('dismissSilenceSuccessBtn');

  setupSilenceEventListeners();
}

function setupSilenceEventListeners() {
  if (!silenceDropzone) return;

  // Silence Dropzone
  silenceDropzone.addEventListener('click', () => silenceFileInput.click());
  changeSilenceFileBtn.addEventListener('click', () => silenceFileInput.click());

  silenceFileInput.addEventListener('change', async (e) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      await handleSilenceFiles(files);
      silenceFileInput.value = '';
    }
  });

  // Drag and drop for silence dropzone
  ['dragenter', 'dragover'].forEach(name => {
    silenceDropzone.addEventListener(name, (e) => {
      e.preventDefault();
      e.stopPropagation();
      silenceDropzone.classList.add('drag-over');
    });
  });

  ['dragleave', 'drop'].forEach(name => {
    silenceDropzone.addEventListener(name, (e) => {
      e.preventDefault();
      e.stopPropagation();
      silenceDropzone.classList.remove('drag-over');
    });
  });

  silenceDropzone.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      await handleSilenceFiles(files);
    }
  });

  // Slider controls: Threshold
  thresholdSlider.addEventListener('input', (e) => {
    updateSilenceThresh(parseFloat(e.target.value));
    scheduleSilenceAnalysis();
  });
  threshChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const val = parseFloat(chip.getAttribute('data-thresh'));
      thresholdSlider.value = val;
      updateSilenceThresh(val);
      scheduleSilenceAnalysis();
    });
  });

  // Slider controls: Min duration
  minDurationSlider.addEventListener('input', (e) => {
    updateSilenceMinDur(parseFloat(e.target.value));
    scheduleSilenceAnalysis();
  });
  durChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const val = parseFloat(chip.getAttribute('data-dur'));
      minDurationSlider.value = val;
      updateSilenceMinDur(val);
      scheduleSilenceAnalysis();
    });
  });

  // Slider controls: Margin
  marginSlider.addEventListener('input', (e) => {
    updateSilenceMargin(parseFloat(e.target.value));
    scheduleSilenceAnalysis();
  });
  marginChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const val = parseFloat(chip.getAttribute('data-margin'));
      marginSlider.value = val;
      updateSilenceMargin(val);
      scheduleSilenceAnalysis();
    });
  });

  // Format radios
  silenceFormatRadios.forEach(radio => {
    radio.addEventListener('change', () => {
      updateSilenceOutputPathExtension(radio.value);
    });
  });
  setupRadioCardGroup('silenceFormat');

  // Analyze pauses button
  analyzeSilencesBtn.addEventListener('click', executeDetectSilences);

  // Cut silences button
  startSilenceRemovalBtn.addEventListener('click', executeRemoveSilences);

  // Cancel button
  cancelSilenceBtn.addEventListener('click', async () => {
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.cancel_silence_removal();
    }
  });

  // Success card buttons
  openSilenceFolderBtn.addEventListener('click', async () => {
    if (lastSilenceResult && window.pywebview && window.pywebview.api) {
      await window.pywebview.api.open_folder(lastSilenceResult.path);
    }
  });

  sendToStitcherBtn.addEventListener('click', () => {
    if (!lastSilenceResult) return;
    audioClips.push(lastSilenceResult);
    renderClips();
    switchTab('stitcher');
    silenceSuccessCard.style.display = 'none';
    if (silenceCleanPlayer) silenceCleanPlayer.pause();
  });

  dismissSilenceSuccessBtn.addEventListener('click', () => {
    silenceSuccessCard.style.display = 'none';
    if (silenceCleanPlayer) silenceCleanPlayer.pause();
    if (silenceCleanVideoPlayer) silenceCleanVideoPlayer.pause();
  });

  sendSilenceVideoToZoomBtn.addEventListener('click', () => {
    const audioForZoom = lastSilenceResult?.audio_info || lastSilenceResult;
    if (!lastSilenceResult?.aligned_images?.length || !audioForZoom?.path) return;
    setZoomActiveMedia(audioForZoom, lastSilenceResult.aligned_images);
    switchTab('zoom');
  });

  saveSilenceVideoBtn.addEventListener('click', async () => {
    if (!lastSilenceResult?.path || !window.pywebview?.api) return;
    const defaultName = lastSilenceResult.filename || 'synchronized_video.mp4';
    const destination = await window.pywebview.api.select_save_destination(defaultName);
    if (!destination) return;
    const saved = await window.pywebview.api.copy_media_to_destination(lastSilenceResult.path, destination);
    if (saved?.success) {
      alert(`Synchronized video saved to:\n${saved.path}`);
    } else {
      alert(`Could not save synchronized video: ${saved?.error || 'Unknown error'}`);
    }
  });
}

function scheduleSilenceAnalysis() {
  if (silenceAnalysisDebounce) clearTimeout(silenceAnalysisDebounce);
  silenceAnalysisDebounce = setTimeout(() => {
    silenceAnalysisDebounce = null;
    executeDetectSilences();
  }, 350);
}

function updateSilenceThresh(val) {
  threshDisplay.textContent = `${val} dB`;
  threshChips.forEach(chip => {
    const chipVal = parseFloat(chip.getAttribute('data-thresh'));
    if (Math.abs(chipVal - val) < 1.0) {
      chip.classList.add('active');
    } else {
      chip.classList.remove('active');
    }
  });
}

function updateSilenceMinDur(val) {
  minDurDisplay.textContent = `${val.toFixed(2)}s`;
  durChips.forEach(chip => {
    const chipVal = parseFloat(chip.getAttribute('data-dur'));
    if (Math.abs(chipVal - val) < 0.05) {
      chip.classList.add('active');
    } else {
      chip.classList.remove('active');
    }
  });
}

function updateSilenceMargin(val) {
  marginDisplay.textContent = `${Math.round(val)} ms`;
  marginChips.forEach(chip => {
    const chipVal = parseFloat(chip.getAttribute('data-margin'));
    if (Math.abs(chipVal - val) < 5) {
      chip.classList.add('active');
    } else {
      chip.classList.remove('active');
    }
  });
}

function getSilenceSelectedFormat() {
  const checked = document.querySelector('input[name="silenceFormat"]:checked');
  return checked ? checked.value : 'mp3';
}

function updateSilenceOutputPathExtension(ext) {
  if (!silenceOutputPathInput) return;
  let cur = silenceOutputPathInput.value;
  if (cur) {
    const dotIdx = cur.lastIndexOf('.');
    if (dotIdx !== -1) {
      silenceOutputPathInput.value = cur.substring(0, dotIdx + 1) + ext;
    } else {
      silenceOutputPathInput.value = cur + '.' + ext;
    }
  }
}

async function handleSilenceFiles(files) {
  if (!files || files.length === 0) return;

  // If path is directly accessible
  if (files[0].path && window.pywebview && window.pywebview.api) {
    try {
      const metadataList = await window.pywebview.api.inspect_dropped_media([files[0].path]);
      if (metadataList && metadataList.length > 0 && !metadataList[0].error) {
        setSilenceActiveFile(metadataList[0]);
        return;
      }
    } catch (err) {
      console.warn("inspect_dropped_files failed, falling back to upload:", err);
    }
  }

  // Browser fallback: keep the file in the app's imported-media directory.
  const formData = new FormData();
  formData.append('media_files', files[0], files[0].name);

  try {
    const res = await fetch('/upload_media', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (data.success && data.path) {
      const metadata = await window.pywebview.api.inspect_dropped_media([data.path]);
      if (metadata?.length && !metadata[0].error) {
        setSilenceActiveFile(metadata[0]);
      } else {
        alert(`Could not inspect media: ${metadata?.[0]?.error || 'Unknown error'}`);
      }
    } else {
      alert("Error uploading media: " + (data.error || "Unknown"));
    }
  } catch (err) {
    alert("Could not load media file: " + err.message);
  }
}

function setSilenceActiveFile(clip) {
  silenceActiveFile = clip;
  lastSilenceResult = null;
  silenceSuccessCard.style.display = 'none';
  silenceTelemetryCard.style.display = 'none';
  const isVideo = clip.media_type === 'video' || /\.(mp4|mov|mkv|webm|avi)$/i.test(clip.filename || clip.path || '');
  const hasAlignedImages = Array.isArray(clip.aligned_images) && clip.aligned_images.length > 0;
  silenceActiveFile.media_type = isVideo ? 'video' : 'audio';
  // If this audio was mastered by Stitcher, avoid running loudnorm again by
  // default. Users can still re-enable it explicitly in this workflow.
  silenceNormalize.checked = clip.normalization_applied === true ? false : true;

  silenceFileName.textContent = clip.filename;
  silenceFileFmt.textContent = (clip.format_name || (isVideo ? 'MP4' : 'Audio')).toUpperCase();
  silenceFileDur.textContent = clip.duration_formatted || '00:00';
  silenceFileSr.textContent = `${(clip.sample_rate / 1000).toFixed(1)} kHz`;
  silenceFileSize.textContent = clip.size_formatted;

  silenceOriginalPlayer.pause();
  silenceOriginalVideoPlayer.pause();
  silenceOriginalPlayer.style.display = isVideo ? 'none' : 'block';
  silenceOriginalVideoPlayer.style.display = isVideo ? 'block' : 'none';
  if (isVideo) {
    silenceOriginalVideoPlayer.src = `/stream_video?path=${encodeURIComponent(clip.path)}&t=${Date.now()}`;
    silenceOriginalVideoPlayer.load();
    silenceFileFmt.textContent = 'MP4 VIDEO';
    silenceFileSr.textContent = `${clip.width || '—'}×${clip.height || '—'} · ${Math.round(clip.fps || 30)} FPS`;
  } else {
    silenceOriginalVideoPlayer.removeAttribute('src');
    silenceOriginalVideoPlayer.load();
    silenceOriginalPlayer.src = `/stream_audio?path=${encodeURIComponent(clip.path)}&t=${Date.now()}`;
    silenceFileFmt.textContent = (clip.format_name || 'Audio').toUpperCase();
    silenceFileSr.textContent = `${((clip.sample_rate || 48000) / 1000).toFixed(1)} kHz`;
  }

  silenceDropzone.style.display = 'none';
  silenceAudioCard.style.display = 'block';
  renderSilenceAlignedImages(clip.aligned_images || []);
  silenceAudioOutputSettings.style.display = (isVideo || hasAlignedImages) ? 'none' : 'block';
  silenceVideoOutputNote.style.display = (isVideo || hasAlignedImages) ? 'block' : 'none';
  if (hasAlignedImages && !isVideo) {
    silenceVideoOutputNote.textContent = 'The aligned image sequence will be retimed with the cleaned voiceover. No draft video is rendered or downloaded; continue to Zoom & Transitions when ready.';
  } else if (isVideo) {
    silenceVideoOutputNote.textContent = 'The synchronized MP4 stays available inside the app for preview and handoff. Saving it is optional.';
  }
  document.getElementById('silenceRemoveButtonLabel').textContent = isVideo
    ? 'Remove Silence & Retiming Video'
    : hasAlignedImages ? 'Remove Pauses & Retiming Images'
    : 'Cut Silences';
  sendSilenceVideoToZoomBtn.style.display = 'none';
  saveSilenceVideoBtn.style.display = 'none';
  document.getElementById('sendToStitcherBtn').style.display = (isVideo || hasAlignedImages) ? 'none' : '';

  if (silenceOutputPathInput) {
    const baseName = clip.filename.replace(/\.[^/.]+$/, "");
    const fmt = getSilenceSelectedFormat();
    silenceOutputPathInput.value = (isVideo || hasAlignedImages) ? '' : `${baseName}_cleaned.${fmt}`;
  }

  analyzeSilencesBtn.disabled = false;
  startSilenceRemovalBtn.disabled = false;

  // Auto trigger analysis for instant feedback
  executeDetectSilences();
}

function renderSilenceAlignedImages(items) {
  const card = document.getElementById('silenceAlignedImagesCard');
  const grid = document.getElementById('silenceAlignedImagesGrid');
  const count = document.getElementById('silenceAlignedImagesCount');
  if (!card || !grid || !count) return;
  if (!items.length) {
    card.style.display = 'none';
    grid.replaceChildren();
    return;
  }
  count.textContent = `${items.length} images`;
  const fragment = document.createDocumentFragment();
  items.forEach((item, index) => {
    const tile = document.createElement('div');
    tile.className = 'timeline-card';
    tile.innerHTML = `<div class="timeline-card-thumb-box"><img class="timeline-card-thumb" src="/thumbnail_image?path=${encodeURIComponent(item.path)}&width=360&v=${encodeURIComponent(item.thumbnail_version || '')}" alt="${item.filename}" loading="lazy" decoding="async"><span class="timeline-card-seq-badge">#${index + 1}</span></div><div class="timeline-card-body"><div class="timeline-card-title" title="${item.filename}">${item.filename}</div><div class="timeline-card-timing"><span class="timeline-pill-time">${item.start_formatted} âž” ${item.end_formatted}</span><span class="timeline-pill-dur">${item.duration_formatted}</span></div></div>`;
    fragment.appendChild(tile);
  });
  grid.replaceChildren(fragment);
  card.style.display = 'block';
}

async function executeDetectSilences() {
  if (!silenceActiveFile || !window.pywebview || !window.pywebview.api) return;

  const thresh = parseFloat(thresholdSlider.value) || -40.0;
  const minDur = parseFloat(minDurationSlider.value) || 0.4;
  const margin = Number.isFinite(parseFloat(marginSlider.value)) ? parseFloat(marginSlider.value) : 80.0;

  silenceBadgeStats.textContent = "Analyzing Audio...";
  analyzeSilencesBtn.disabled = true;

  try {
    const res = await window.pywebview.api.detect_silences(silenceActiveFile.path, thresh, minDur, margin);
    if (res.success) {
      silenceCountStat.textContent = res.silence_count;
      silenceSavedStat.textContent = `${res.total_silence_sec}s (${res.percent_saved}%)`;
      silenceNewDurStat.textContent = res.expected_trimmed_formatted;

      renderDetectedSilencesList(res.silence_segments);
      silenceTelemetryCard.style.display = 'block';
      silenceBadgeStats.textContent = `${res.silence_count} Pauses Detected`;
    } else {
      alert("Silence analysis error: " + (res.error || "Unknown"));
    }
  } catch (err) {
    console.error("detect_silences error:", err);
  } finally {
    analyzeSilencesBtn.disabled = false;
  }
}

function formatSecondsToTime(sec) {
  if (typeof sec !== 'number' || isNaN(sec)) return '00:00';
  const mins = Math.floor(sec / 60);
  const secs = (sec % 60).toFixed(2);
  return `${String(mins).padStart(2, '0')}:${secs.padStart(5, '0')}`;
}

function renderDetectedSilencesList(segments) {
  silenceListContainer.innerHTML = '';
  if (!segments || segments.length === 0) {
    silenceListContainer.innerHTML = `
      <div class="silence-empty-state">
        No silences found exceeding the duration threshold. Your audio is already tight!
      </div>
    `;
    return;
  }

  segments.forEach((seg, idx) => {
    const item = document.createElement('div');
    item.className = 'silence-item';
    const startStr = seg.start_formatted || formatSecondsToTime(seg.start);
    const endStr = seg.end_formatted || formatSecondsToTime(seg.end);
    const durSec = (typeof seg.duration === 'number') ? seg.duration : ((seg.end || 0) - (seg.start || 0));
    const durStr = seg.duration_formatted || `${durSec.toFixed(2)}s`;

    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px;">
        <span class="silence-item-num">#${idx + 1}</span>
        <span class="silence-item-range">${startStr} ➔ ${endStr}</span>
      </div>
      <span class="silence-item-dur">-${durStr}</span>
    `;
    silenceListContainer.appendChild(item);
  });
}

async function executeRemoveSilences() {
  if (!silenceActiveFile || !window.pywebview || !window.pywebview.api) return;

  const isVideo = silenceActiveFile.media_type === 'video';
  const hasAlignedImages = Array.isArray(silenceActiveFile.aligned_images) && silenceActiveFile.aligned_images.length > 0;

  const thresh = parseFloat(thresholdSlider.value) || -40.0;
  const minDur = parseFloat(minDurationSlider.value) || 0.4;
  const margin = Number.isFinite(parseFloat(marginSlider.value)) ? parseFloat(marginSlider.value) : 80.0;
  const normalize = silenceNormalize.checked;

  silenceProgressCard.style.display = 'block';
  silenceSuccessCard.style.display = 'none';
  startSilenceRemovalBtn.disabled = true;
  analyzeSilencesBtn.disabled = true;

  silenceProgressBarFill.style.width = '5%';
  silenceProgressPercent.textContent = '5%';
  silenceProgressStatusText.textContent = "Analyzing audio waveform...";

  try {
    if (isVideo) {
      await window.pywebview.api.start_remove_video_silences(
        silenceActiveFile.path,
        silenceActiveFile.aligned_images || [], thresh, minDur, margin, normalize, true
      );
    } else if (hasAlignedImages) {
      await window.pywebview.api.start_remove_aligned_audio_silences(
        silenceActiveFile.path,
        silenceActiveFile.aligned_images,
        thresh, minDur, margin, normalize
      );
    } else {
      await window.pywebview.api.start_remove_silences_internal(
        silenceActiveFile.path, thresh, minDur, margin, normalize
      );
    }
  } catch (err) {
    alert("Error starting silence removal: " + err);
    resetSilenceProgress();
  }
}

// Global Silence Callbacks from Python ApiBridge
window.onSilenceProgress = function(percent, statusMsg) {
  silenceProgressBarFill.style.width = `${percent}%`;
  silenceProgressPercent.textContent = `${Math.round(percent)}%`;
  silenceProgressStatusText.textContent = statusMsg;
};

window.onSilenceComplete = function(result) {
  resetSilenceProgress();
  const isVideo = result.media_type === 'video';
  const outputClip = result.output_info || result;
  const filePath = result.path || outputClip.path;
  const durationFormatted = result.trimmed_duration_formatted || result.duration_formatted || outputClip.duration_formatted || "00:00";
  const savedSec = result.silence_removed_sec ?? result.time_saved_sec ?? 0;
  const savedPct = result.percent_saved ?? result.time_saved_percent ?? 0;
  const pauseCount = result.silence_count ?? 0;

  lastSilenceResult = {
    path: filePath,
    filename: result.filename || outputClip.filename || (filePath ? filePath.split(/[\\/]/).pop() : "cleaned_voiceover.mp3"),
    duration: result.trimmed_duration || result.duration || outputClip.duration || 0,
    duration_formatted: durationFormatted,
    format_name: result.format_name || outputClip.format_name || "MP3",
    sample_rate: result.sample_rate || outputClip.sample_rate || 48000,
    size_formatted: result.size_formatted || outputClip.size_formatted || "0 KB",
    media_type: isVideo ? 'video' : 'audio',
    video_info: result.video_info || null,
    audio_path: result.audio_path || null,
    audio_info: result.audio_info || null,
    aligned_images: result.aligned_images || []
  };

  silenceSuccessCard.style.display = 'block';
  silenceSuccessDetails.textContent = `Saved ${savedSec}s (${savedPct}%) • ${pauseCount} pauses cut • New Duration: ${durationFormatted}`;
  if (isVideo) {
    const imageSummary = lastSilenceResult.aligned_images.length
      ? ` • ${lastSilenceResult.aligned_images.length} image segments retimed`
      : ' • source video frames trimmed in sync';
    silenceSuccessDetails.textContent = `Synchronized video ready • ${savedSec}s (${savedPct}%) removed • ${pauseCount} pauses cut • ${durationFormatted}${imageSummary}`;
  } else if (lastSilenceResult.aligned_images.length) {
    silenceSuccessDetails.textContent = `Voiceover cleaned • ${savedSec}s removed • ${pauseCount} pauses cut • ${durationFormatted} • ${lastSilenceResult.aligned_images.length} image segments retimed`;
  }
  silenceCleanPlayer.pause();
  silenceCleanVideoPlayer.pause();
  silenceCleanPlayer.style.display = isVideo ? 'none' : 'block';
  silenceCleanVideoPlayer.style.display = isVideo ? 'block' : 'none';
  if (filePath && isVideo) {
    silenceCleanVideoPlayer.src = `/stream_video?path=${encodeURIComponent(filePath)}&t=${Date.now()}`;
    silenceCleanVideoPlayer.load();
    sendSilenceVideoToZoomBtn.style.display = lastSilenceResult.aligned_images.length ? '' : 'none';
    saveSilenceVideoBtn.style.display = '';
    sendToStitcherBtn.style.display = 'none';
  } else if (filePath) {
    silenceCleanVideoPlayer.removeAttribute('src');
    silenceCleanVideoPlayer.load();
    silenceCleanPlayer.src = `/stream_audio?path=${encodeURIComponent(filePath)}&t=${Date.now()}`;
    silenceCleanPlayer.load();
    sendSilenceVideoToZoomBtn.style.display = lastSilenceResult.aligned_images.length ? '' : 'none';
    saveSilenceVideoBtn.style.display = 'none';
    sendToStitcherBtn.style.display = lastSilenceResult.aligned_images.length ? 'none' : '';
  }
  if (lastSilenceResult.aligned_images.length) {
    renderSilenceAlignedImages(lastSilenceResult.aligned_images);
  }
};

window.onSilenceCancelled = function() {
  resetSilenceProgress();
  alert("Silence removal was cancelled.");
};

window.onSilenceError = function(errMsg) {
  resetSilenceProgress();
  alert("Silence Removal Error:\n" + errMsg);
};

function resetSilenceProgress() {
  silenceProgressCard.style.display = 'none';
  startSilenceRemovalBtn.disabled = false;
  analyzeSilencesBtn.disabled = false;
  silenceProgressBarFill.style.width = '0%';
  silenceProgressPercent.textContent = '0%';
}

// =============================================================================
// 10. TIMELINE ALIGNER CONTROLLER
// =============================================================================
let timelineActiveAudio = null;
let timelineImagesList = [];
let alignedTimelineItems = [];
let lastTimelineRenderResult = null;

// DOM Elements
let timelineAudioDropzone, timelineAudioInput, timelineActiveAudioBar;
let timelineAudioName, timelineAudioMeta, timelineAudioPlayer, changeTimelineAudioBtn;
let statTimelineImgCount, statTimelineAudioDur, statTimelineCoverage;
let timelineImagesDropzone, timelineImageInput, timelineSequenceContainer;
let addMoreImagesBtn, realignTimelineBtn, exportCueSheetBtn, clearImagesBtn, sendToZoomBtn, sendTimelineAudioToSilenceBtn, timelineCardsGrid;
let timelineNvencToggle, startRenderVideoBtn;
let timelineProgressCard, timelineProgressStatusText, timelineProgressPercent, timelineProgressBarFill, cancelTimelineRenderBtn;
let timelineSuccessCard, timelineSuccessDetails, timelineVideoPlayer, openTimelineVideoFolderBtn, dismissTimelineSuccessBtn;

function initTimelineAligner() {
  timelineAudioDropzone = document.getElementById('timelineAudioDropzone');
  timelineAudioInput = document.getElementById('timelineAudioInput');
  timelineActiveAudioBar = document.getElementById('timelineActiveAudioBar');
  timelineAudioName = document.getElementById('timelineAudioName');
  timelineAudioMeta = document.getElementById('timelineAudioMeta');
  timelineAudioPlayer = document.getElementById('timelineAudioPlayer');
  changeTimelineAudioBtn = document.getElementById('changeTimelineAudioBtn');

  statTimelineImgCount = document.getElementById('statTimelineImgCount');
  statTimelineAudioDur = document.getElementById('statTimelineAudioDur');
  statTimelineCoverage = document.getElementById('statTimelineCoverage');

  timelineImagesDropzone = document.getElementById('timelineImagesDropzone');
  timelineImageInput = document.getElementById('timelineImageInput');
  timelineSequenceContainer = document.getElementById('timelineSequenceContainer');

  addMoreImagesBtn = document.getElementById('addMoreImagesBtn');
  realignTimelineBtn = document.getElementById('realignTimelineBtn');
  exportCueSheetBtn = document.getElementById('exportCueSheetBtn');
  clearImagesBtn = document.getElementById('clearImagesBtn');
  sendToZoomBtn = document.getElementById('sendToZoomBtn');
  sendTimelineAudioToSilenceBtn = document.getElementById('sendTimelineAudioToSilenceBtn');
  timelineCardsGrid = document.getElementById('timelineCardsGrid');

  timelineNvencToggle = document.getElementById('timelineNvencToggle');
  startRenderVideoBtn = document.getElementById('startRenderVideoBtn');

  timelineProgressCard = document.getElementById('timelineProgressCard');
  timelineProgressStatusText = document.getElementById('timelineProgressStatusText');
  timelineProgressPercent = document.getElementById('timelineProgressPercent');
  timelineProgressBarFill = document.getElementById('timelineProgressBarFill');
  cancelTimelineRenderBtn = document.getElementById('cancelTimelineRenderBtn');

  timelineSuccessCard = document.getElementById('timelineSuccessCard');
  timelineSuccessDetails = document.getElementById('timelineSuccessDetails');
  timelineVideoPlayer = document.getElementById('timelineVideoPlayer');
  openTimelineVideoFolderBtn = document.getElementById('openTimelineVideoFolderBtn');
  dismissTimelineSuccessBtn = document.getElementById('dismissTimelineSuccessBtn');

  setupTimelineEventListeners();
}

function setupTimelineEventListeners() {
  if (!timelineAudioDropzone) return;

  document.getElementById('timelineQuickDraftBtn').addEventListener('click', () => {
    const draftResolution = document.querySelector('input[name="videoRes"][value="720p"]');
    if (draftResolution) {
      draftResolution.checked = true;
      draftResolution.dispatchEvent(new Event('change', { bubbles: true }));
    }
    document.querySelector('#timelineView .chip[data-fps="24"]')?.click();
  });

  // Audio slot triggers
  timelineAudioDropzone.addEventListener('click', () => timelineAudioInput.click());
  changeTimelineAudioBtn.addEventListener('click', () => timelineAudioInput.click());

  timelineAudioInput.addEventListener('change', async (e) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      await handleTimelineAudioUpload(files[0]);
      timelineAudioInput.value = '';
    }
  });

  // Audio drag & drop
  ['dragenter', 'dragover'].forEach(name => {
    timelineAudioDropzone.addEventListener(name, (e) => {
      e.preventDefault();
      e.stopPropagation();
      timelineAudioDropzone.classList.add('drag-over');
    });
  });
  ['dragleave', 'drop'].forEach(name => {
    timelineAudioDropzone.addEventListener(name, (e) => {
      e.preventDefault();
      e.stopPropagation();
      timelineAudioDropzone.classList.remove('drag-over');
    });
  });
  timelineAudioDropzone.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      await handleTimelineAudioUpload(files[0]);
    }
  });

  // Images input & dropzone triggers
  timelineImagesDropzone.addEventListener('click', () => timelineImageInput.click());
  addMoreImagesBtn.addEventListener('click', () => timelineImageInput.click());

  timelineImageInput.addEventListener('change', async (e) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      await handleTimelineImagesUpload(files);
      timelineImageInput.value = '';
    }
  });

  // Images drag & drop on dropzone & workspace
  [timelineImagesDropzone, timelineSequenceContainer].forEach(target => {
    if (!target) return;
    ['dragenter', 'dragover'].forEach(name => {
      target.addEventListener(name, (e) => {
        e.preventDefault();
        e.stopPropagation();
        target.classList.add('drag-over');
      });
    });
    ['dragleave', 'drop'].forEach(name => {
      target.addEventListener(name, (e) => {
        e.preventDefault();
        e.stopPropagation();
        target.classList.remove('drag-over');
      });
    });
    target.addEventListener('drop', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const files = e.dataTransfer.files;
      if (files && files.length > 0) {
        await handleTimelineImagesUpload(files);
      }
    });
  });

  // FPS chips
  document.querySelectorAll('#timelineView .chip[data-fps]').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('#timelineView .chip[data-fps]').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      document.getElementById('fpsDisplay').textContent = `${chip.getAttribute('data-fps')} FPS`;
    });
  });

  // Resolution radio cards
  setupRadioCardGroup('videoRes');

  // Re-align button
  realignTimelineBtn.addEventListener('click', () => {
    recalculateTimelineAlignment();
  });

  // Clear images
  clearImagesBtn.addEventListener('click', () => {
    if (timelineImagesList.length === 0) return;
    if (confirm("Remove all images from timeline?")) {
      timelineImagesList = [];
      alignedTimelineItems = [];
      renderTimelineCards();
    }
  });

  // Export CSV Cue Sheet
  exportCueSheetBtn.addEventListener('click', async () => {
    if (alignedTimelineItems.length === 0) {
      alert("No images to export in cue sheet.");
      return;
    }
    const defaultCsv = "timeline_cue_sheet.csv";
    try {
      const saveDest = await window.pywebview.api.select_save_destination(defaultCsv);
      if (saveDest) {
        await window.pywebview.api.export_timeline_csv(alignedTimelineItems, saveDest);
        alert(`Cue sheet exported to:\n${saveDest}`);
      }
    } catch (err) {
      alert("Error exporting CSV: " + err);
    }
  });

  sendToZoomBtn.addEventListener('click', () => {
    if (!timelineActiveAudio || alignedTimelineItems.length === 0) {
      alert("Align an audio track and images before sending them to Zoom & Transitions.");
      return;
    }
    setZoomActiveMedia(timelineActiveAudio, alignedTimelineItems);
    switchTab('zoom');
  });

  sendTimelineAudioToSilenceBtn.addEventListener('click', () => {
    if (!timelineActiveAudio || alignedTimelineItems.length === 0) {
      alert("Load voiceover and align images before removing pauses.");
      return;
    }
    setSilenceActiveFile({
      ...timelineActiveAudio,
      media_type: 'audio',
      aligned_images: alignedTimelineItems.map(item => ({ ...item }))
    });
    switchTab('silence');
  });

  // Start Render Video button
  startRenderVideoBtn.addEventListener('click', executeTimelineRender);

  // Cancel Render button
  cancelTimelineRenderBtn.addEventListener('click', async () => {
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.cancel_timeline_video();
    }
  });

  // Success Card actions
  openTimelineVideoFolderBtn.addEventListener('click', async () => {
    if (lastTimelineRenderResult && window.pywebview && window.pywebview.api) {
      const destination = await window.pywebview.api.select_save_destination(
        lastTimelineRenderResult.filename || 'aligned_video.mp4'
      );
      if (!destination) return;
      const saved = await window.pywebview.api.copy_media_to_destination(lastTimelineRenderResult.path, destination);
      if (!saved?.success) alert(`Could not download aligned video: ${saved?.error || 'Unknown error'}`);
    }
  });

  dismissTimelineSuccessBtn.addEventListener('click', () => {
    timelineSuccessCard.style.display = 'none';
    if (timelineVideoPlayer) {
      timelineVideoPlayer.pause();
    }
  });

}

async function handleTimelineAudioUpload(file) {
  if (file.path && window.pywebview && window.pywebview.api) {
    try {
      const metadataList = await window.pywebview.api.inspect_dropped_files([file.path]);
      if (metadataList && metadataList.length > 0 && !metadataList[0].error) {
        setTimelineActiveAudio(metadataList[0]);
        return;
      }
    } catch (err) {
      console.warn("inspect_dropped_files failed for audio:", err);
    }
  }

  const formData = new FormData();
  formData.append('audio_files', file, file.name);

  try {
    const res = await fetch('/upload_audio', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.success && data.clips && data.clips.length > 0) {
      setTimelineActiveAudio(data.clips[0]);
    } else {
      alert("Error uploading audio: " + (data.error || "Unknown"));
    }
  } catch (err) {
    alert("Audio upload failed: " + err.message);
  }
}

function setTimelineActiveAudio(audioMeta) {
  timelineActiveAudio = audioMeta;

  timelineAudioName.textContent = audioMeta.filename;
  timelineAudioMeta.textContent = `${audioMeta.duration_formatted} • ${audioMeta.format_name}`;
  timelineAudioPlayer.src = `/stream_audio?path=${encodeURIComponent(audioMeta.path)}&t=${Date.now()}`;

  timelineAudioDropzone.style.display = 'none';
  timelineActiveAudioBar.style.display = 'block';
  changeTimelineAudioBtn.style.display = 'inline-block';

  statTimelineAudioDur.textContent = audioMeta.duration_formatted;

  recalculateTimelineAlignment();
}

async function handleTimelineImagesUpload(files) {
  const directPaths = Array.from(files || []).map(file => file.path).filter(Boolean);
  if (directPaths.length === files.length && window.pywebview && window.pywebview.api) {
    try {
      const inspected = await window.pywebview.api.inspect_dropped_images(directPaths);
      if (inspected && inspected.length === files.length) {
        appendTimelineImages(inspected);
        return;
      }
    } catch (err) {
      console.warn("inspect_dropped_images failed:", err);
    }
  }

  const importStatus = document.getElementById('timelineImageImportStatus');
  const importText = document.getElementById('timelineImageImportText');
  const importBar = document.getElementById('timelineImageImportBar');
  importStatus.hidden = false;
  importText.textContent = `Importing ${files.length} images...`;
  importBar.value = 0;
  try {
    const result = await uploadInBatches(files, '/upload_images', 'image_files', 'images', (done, total) => {
      importText.textContent = `Imported ${done} of ${total} images...`;
      importBar.value = total ? (done / total) * 100 : 100;
    });
    if (result.items.length) appendTimelineImages(result.items);
    if (result.error) throw result.error;
  } catch (err) {
    alert("Failed to upload images: " + err.message);
  } finally {
    importStatus.hidden = true;
    importBar.value = 0;
  }
}

function appendTimelineImages(newImages) {
  const existingPaths = new Set(timelineImagesList.map(existing => existing.path));
  newImages.forEach(img => {
    if (!existingPaths.has(img.path)) {
      timelineImagesList.push(img);
      existingPaths.add(img.path);
    }
  });
  recalculateTimelineAlignment();
}

async function recalculateTimelineAlignment() {
  if (timelineImagesList.length === 0) {
    alignedTimelineItems = [];
    renderTimelineCards();
    return;
  }

  const audioPath = timelineActiveAudio ? timelineActiveAudio.path : "";
  const imagePaths = timelineImagesList.map(img => img.path);

  if (audioPath && window.pywebview && window.pywebview.api) {
    try {
      const res = await window.pywebview.api.align_timeline(imagePaths, audioPath);
      if (res.success) {
        alignedTimelineItems = res.aligned_images;
      } else {
        console.error("align_timeline error:", res.error);
        fallbackClientAlignment();
      }
    } catch (err) {
      console.warn("API align_timeline failed:", err);
      fallbackClientAlignment();
    }
  } else {
    fallbackClientAlignment();
  }

  renderTimelineCards();
}

function fallbackClientAlignment() {
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
  const sorted = [...timelineImagesList].sort((a, b) => collator.compare(a.filename, b.filename));
  const audioDur = timelineActiveAudio ? timelineActiveAudio.duration : 60.0;
  const count = sorted.length;

  alignedTimelineItems = sorted.map((img, idx) => {
    const start = (idx / Math.max(1, count)) * audioDur;
    const end = ((idx + 1) / Math.max(1, count)) * audioDur;
    const dur = end - start;
    return {
      index: idx,
      path: img.path,
      filename: img.filename,
      thumbnail_version: img.thumbnail_version || `${img.size_bytes || 0}`,
      start_sec: start,
      end_sec: end,
      duration: dur,
      start_formatted: formatSecondsToTime(start),
      end_formatted: formatSecondsToTime(end),
      duration_formatted: `${dur.toFixed(2)}s`
    };
  });
}

function renderTimelineCards() {
  if (alignedTimelineItems.length === 0) {
    timelineImagesDropzone.style.display = 'block';
    timelineSequenceContainer.style.display = 'none';
    timelineCardsGrid.replaceChildren();
    statTimelineImgCount.textContent = '0';
    statTimelineCoverage.textContent = '0%';
    startRenderVideoBtn.disabled = true;
    return;
  }

  timelineImagesDropzone.style.display = 'none';
  timelineSequenceContainer.style.display = 'block';
  const fragment = document.createDocumentFragment();

  alignedTimelineItems.forEach((item, idx) => {
    const card = document.createElement('div');
    card.className = 'timeline-card';
    card.innerHTML = `
      <div class="timeline-card-thumb-box">
        <img class="timeline-card-thumb" src="/thumbnail_image?path=${encodeURIComponent(item.path)}&width=360&v=${encodeURIComponent(item.thumbnail_version || '')}" alt="${item.filename}" loading="lazy" decoding="async">
        <span class="timeline-card-seq-badge">#${idx + 1}</span>
        <button class="timeline-card-del-btn" title="Remove image" data-index="${idx}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      <div class="timeline-card-body">
        <div class="timeline-card-title" title="${item.filename}">${item.filename}</div>
        <div class="timeline-card-timing">
          <span class="timeline-pill-time">${item.start_formatted} ➔ ${item.end_formatted}</span>
          <span class="timeline-pill-dur">${item.duration_formatted}</span>
        </div>
      </div>
    `;

    card.querySelector('.timeline-card-del-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      timelineImagesList.splice(idx, 1);
      recalculateTimelineAlignment();
    });

    fragment.appendChild(card);
  });
  timelineCardsGrid.replaceChildren(fragment);

  statTimelineImgCount.textContent = alignedTimelineItems.length;
  statTimelineCoverage.textContent = timelineActiveAudio ? '100%' : 'No Audio';

  const ready = !!(timelineActiveAudio && alignedTimelineItems.length > 0);
  startRenderVideoBtn.disabled = !ready;
  sendToZoomBtn.disabled = !ready;
  sendTimelineAudioToSilenceBtn.disabled = !ready;
}

async function executeTimelineRender() {
  if (!timelineActiveAudio || alignedTimelineItems.length === 0) {
    alert("Please ensure both voiceover audio and images are added to the timeline.");
    return;
  }

  const resRadio = document.querySelector('input[name="videoRes"]:checked');
  const resVal = resRadio ? resRadio.value : '1080p';
  let width = 1920;
  let height = 1080;
  if (resVal === '4k') {
    width = 3840;
    height = 2160;
  } else if (resVal === '720p') {
    width = 1280;
    height = 720;
  }

  const activeFpsChip = document.querySelector('#timelineView .chip[data-fps].active');
  const fps = activeFpsChip ? parseInt(activeFpsChip.getAttribute('data-fps'), 10) : 30;
  const useNvenc = timelineNvencToggle.checked;

  timelineProgressCard.style.display = 'block';
  timelineSuccessCard.style.display = 'none';
  startRenderVideoBtn.disabled = true;

  timelineProgressBarFill.style.width = '5%';
  timelineProgressPercent.textContent = '5%';
  timelineProgressStatusText.textContent = "Initializing video rendering pipeline...";

  try {
    await window.pywebview.api.start_render_timeline_video_internal(
      alignedTimelineItems,
      timelineActiveAudio.path,
      width,
      height,
      fps,
      useNvenc,
      'none',
      0,
      'hard_cut',
      0
    );
  } catch (err) {
    alert("Error rendering video: " + err);
    resetTimelineRenderProgress();
  }
}

// Global Timeline Video Callbacks from Python ApiBridge
window.onTimelineVideoProgress = function(percent, statusMsg) {
  timelineProgressBarFill.style.width = `${percent}%`;
  timelineProgressPercent.textContent = `${Math.round(percent)}%`;
  timelineProgressStatusText.textContent = statusMsg;
};

window.onTimelineVideoComplete = function(result) {
  resetTimelineRenderProgress();
  lastTimelineRenderResult = result;

  timelineSuccessCard.style.display = 'block';
  timelineSuccessDetails.textContent = `${result.duration_formatted} • ${result.size_formatted} • ${result.resolution} @ ${result.fps}fps • ${result.image_count} images`;

  const videoStreamUrl = `/stream_video?path=${encodeURIComponent(result.path)}&t=${Date.now()}`;
  timelineVideoPlayer.src = videoStreamUrl;
  timelineVideoPlayer.load();
};

window.onTimelineVideoCancelled = function() {
  resetTimelineRenderProgress();
  alert("Timeline video render was cancelled.");
};

window.onTimelineVideoError = function(errMsg) {
  resetTimelineRenderProgress();
  alert("Timeline Video Render Error:\n" + errMsg);
};

function resetTimelineRenderProgress() {
  timelineProgressCard.style.display = 'none';
  startRenderVideoBtn.disabled = !(timelineActiveAudio && alignedTimelineItems.length > 0);
  timelineProgressBarFill.style.width = '0%';
  timelineProgressPercent.textContent = '0%';
}

// =============================================================================
// 11. ZOOM & TRANSITIONS WORKFLOW
// =============================================================================
let zoomActiveAudio = null;
let zoomAlignedItems = [];
let zoomZoomType, zoomZoomAmount, zoomTransitionType, zoomTransitionDuration;
let zoomOutputPathInput, zoomNvencToggle, zoomApplyBtn, zoomProgressCard, zoomProgressStatusText;
let zoomProgressPercent, zoomProgressBarFill, zoomSuccessCard, zoomSuccessDetails, zoomVideoPlayer;

function initZoomTransitions() {
  zoomZoomType = document.getElementById('zoomEffectType');
  zoomZoomAmount = document.getElementById('zoomEffectAmount');
  zoomTransitionType = document.getElementById('zoomTransitionType');
  zoomTransitionDuration = document.getElementById('zoomTransitionDuration');
  zoomOutputPathInput = document.getElementById('zoomOutputPathInput');
  zoomNvencToggle = document.getElementById('zoomNvencToggle');
  zoomApplyBtn = document.getElementById('applyZoomEffectsBtn');
  zoomProgressCard = document.getElementById('zoomProgressCard');
  zoomProgressStatusText = document.getElementById('zoomProgressStatusText');
  zoomProgressPercent = document.getElementById('zoomProgressPercent');
  zoomProgressBarFill = document.getElementById('zoomProgressBarFill');
  zoomSuccessCard = document.getElementById('zoomSuccessCard');
  zoomSuccessDetails = document.getElementById('zoomSuccessDetails');
  zoomVideoPlayer = document.getElementById('zoomVideoPlayer');
  const zoomMainColumn = document.querySelector('#zoomView .timeline-main-column');
  if (zoomMainColumn && zoomSuccessCard) zoomMainColumn.appendChild(zoomSuccessCard);

  document.getElementById('zoomQuickDraftBtn').addEventListener('click', () => {
    const draftResolution = document.querySelector('input[name="zoomVideoRes"][value="720p"]');
    if (draftResolution) {
      draftResolution.checked = true;
      draftResolution.dispatchEvent(new Event('change', { bubbles: true }));
    }
    document.querySelector('#zoomView .chip[data-zoom-fps="24"]')?.click();
  });

  const zoomAudioPlayer = document.getElementById('zoomAudioPlayer');
  const zoomAudioPlayBtn = document.getElementById('zoomAudioPlayBtn');
  const zoomAudioSeek = document.getElementById('zoomAudioSeek');
  const zoomAudioCurrentTime = document.getElementById('zoomAudioCurrentTime');
  const zoomAudioDurationLabel = document.getElementById('zoomAudioDurationLabel');
  const zoomAudioWaveform = document.getElementById('zoomAudioWaveform');
  const zoomAudioVolumeBtn = document.querySelector('#zoomAudioControls .audio-volume-button');

  if (zoomAudioWaveform && !zoomAudioWaveform.children.length) {
    const waveformHeights = [18, 27, 38, 24, 48, 33, 58, 42, 66, 36, 54, 72, 44, 61, 35, 52, 70, 43, 29, 47, 63, 35, 55, 74, 42, 31, 57, 68, 46, 60, 35, 51, 72, 38, 26, 48, 65, 42, 58, 31, 49, 67, 39, 55, 72, 44, 29, 50, 64, 37, 57, 75, 43, 31, 54, 68, 40, 58, 34, 50, 70, 45, 30, 48, 64];
    waveformHeights.forEach(height => {
      const bar = document.createElement('span');
      bar.style.height = `${height}%`;
      zoomAudioWaveform.appendChild(bar);
    });
  }

  const formatPlayerTime = seconds => {
    if (!Number.isFinite(seconds)) return '00:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  };
  const updateAudioTransport = () => {
    const duration = zoomAudioPlayer.duration || 0;
    const position = duration ? (zoomAudioPlayer.currentTime / duration) * 100 : 0;
    zoomAudioSeek.value = position;
    zoomAudioCurrentTime.textContent = formatPlayerTime(zoomAudioPlayer.currentTime);
    zoomAudioDurationLabel.textContent = formatPlayerTime(duration);
    zoomAudioSeek.style.setProperty('--seek-progress', `${position}%`);
    document.body.classList.toggle('zoom-audio-playing', !zoomAudioPlayer.paused);
  };
  zoomAudioPlayBtn.addEventListener('click', () => {
    if (zoomAudioPlayer.paused) zoomAudioPlayer.play();
    else zoomAudioPlayer.pause();
  });
  zoomAudioSeek.addEventListener('input', () => {
    if (zoomAudioPlayer.duration) zoomAudioPlayer.currentTime = (parseFloat(zoomAudioSeek.value) / 100) * zoomAudioPlayer.duration;
    updateAudioTransport();
  });
  zoomAudioPlayer.addEventListener('loadedmetadata', updateAudioTransport);
  zoomAudioPlayer.addEventListener('timeupdate', updateAudioTransport);
  zoomAudioPlayer.addEventListener('play', updateAudioTransport);
  zoomAudioPlayer.addEventListener('pause', updateAudioTransport);
  zoomAudioVolumeBtn.addEventListener('click', () => {
    zoomAudioPlayer.muted = !zoomAudioPlayer.muted;
    zoomAudioVolumeBtn.classList.toggle('muted', zoomAudioPlayer.muted);
  });

  document.getElementById('browseZoomDestBtn').addEventListener('click', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const currentName = zoomOutputPathInput.value.split(/[\\/]/).pop() || 'final_video.mp4';
    const selected = await window.pywebview.api.select_save_destination(currentName);
    if (selected) zoomOutputPathInput.value = selected;
  });
  document.querySelectorAll('#zoomView .chip[data-zoom-fps]').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('#zoomView .chip[data-zoom-fps]').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      document.getElementById('zoomFpsDisplay').textContent = `${chip.dataset.zoomFps} FPS`;
    });
  });
  setupRadioCardGroup('zoomVideoRes');
  zoomTransitionDuration.addEventListener('input', () => {
    document.getElementById('zoomTransitionDurationDisplay').textContent = `${parseFloat(zoomTransitionDuration.value).toFixed(2)}s`;
    const percentage = ((zoomTransitionDuration.value - zoomTransitionDuration.min) / (zoomTransitionDuration.max - zoomTransitionDuration.min)) * 100;
    zoomTransitionDuration.style.setProperty('--range-progress', `${percentage}%`);
  });
  zoomTransitionDuration.dispatchEvent(new Event('input'));
  zoomApplyBtn.addEventListener('click', executeZoomRender);
  document.getElementById('cancelZoomRenderBtn').addEventListener('click', () => window.pywebview.api.cancel_timeline_video());
  document.getElementById('openZoomFolderBtn').addEventListener('click', () => {
    if (lastZoomRenderResult) window.pywebview.api.open_folder(lastZoomRenderResult.path);
  });
  document.getElementById('dismissZoomSuccessBtn').addEventListener('click', () => {
    zoomSuccessCard.style.display = 'none';
    zoomVideoPlayer.pause();
  });
}

function setZoomActiveMedia(audioMeta, alignedItems) {
  zoomActiveAudio = audioMeta;
  zoomAlignedItems = alignedItems.map(item => ({ ...item }));
  document.getElementById('zoomSourceEmpty').style.display = 'none';
  document.getElementById('zoomSourceActive').style.display = 'block';
  document.getElementById('zoomAudioName').textContent = audioMeta.filename;
  document.getElementById('zoomAudioMeta').textContent = `${audioMeta.duration_formatted} · ${audioMeta.format_name}`;
  const zoomAudioPlayer = document.getElementById('zoomAudioPlayer');
  zoomAudioPlayer.src = `/stream_audio?path=${encodeURIComponent(audioMeta.path)}&t=${Date.now()}`;
  zoomAudioPlayer.load();
  zoomAudioPlayer.pause();
  zoomAudioPlayer.currentTime = 0;
  document.getElementById('zoomAudioSeek').value = 0;
  document.getElementById('zoomAudioSeek').style.setProperty('--seek-progress', '0%');
  zoomAudioPlayer.onerror = () => sendClientLog('error', `Zoom workflow audio playback failed for ${audioMeta.path}`);
  document.getElementById('zoomImageCount').textContent = zoomAlignedItems.length;
  document.getElementById('zoomAudioDuration').textContent = audioMeta.duration_formatted;
  document.getElementById('zoomSequenceStatus').textContent = `${zoomAlignedItems.length} images aligned`;
  zoomOutputPathInput.value = `final_video_${new Date().toISOString().slice(0, 10)}.mp4`;
  renderZoomCards();
}

function renderZoomCards() {
  const grid = document.getElementById('zoomCardsGrid');
  const fragment = document.createDocumentFragment();
  zoomAlignedItems.forEach((item, index) => {
    const card = document.createElement('div');
    card.className = 'timeline-card';
    card.innerHTML = `<div class="timeline-card-thumb-box"><img class="timeline-card-thumb" src="/thumbnail_image?path=${encodeURIComponent(item.path)}&width=360&v=${encodeURIComponent(item.thumbnail_version || '')}" alt="${item.filename}" loading="lazy" decoding="async"><span class="timeline-card-seq-badge">#${index + 1}</span></div><div class="timeline-card-body"><div class="timeline-card-title" title="${item.filename}">${item.filename}</div><div class="timeline-card-timing"><span class="timeline-pill-time">${item.start_formatted} ➔ ${item.end_formatted}</span><span class="timeline-pill-dur">${item.duration_formatted}</span></div></div>`;
    fragment.appendChild(card);
  });
  grid.replaceChildren(fragment);
}

async function executeZoomRender() {
  if (!zoomActiveAudio || zoomAlignedItems.length === 0) {
    alert('Send an aligned timeline here before exporting.');
    return;
  }
  const outPath = zoomOutputPathInput.value.trim();
  if (!outPath) {
    alert('Please specify a final video destination.');
    return;
  }
  const resVal = document.querySelector('input[name="zoomVideoRes"]:checked')?.value || '1080p';
  const sizes = { '1080p': [1920, 1080], '4k': [3840, 2160], '720p': [1280, 720] };
  const [width, height] = sizes[resVal];
  const fps = parseInt(document.querySelector('#zoomView .chip[data-zoom-fps].active')?.dataset.zoomFps || '30', 10);
  zoomProgressCard.style.display = 'block';
  zoomSuccessCard.style.display = 'none';
  zoomApplyBtn.disabled = true;
  zoomProgressStatusText.textContent = 'Applying zoom and transitions...';
  zoomProgressBarFill.style.width = '5%';
  zoomProgressPercent.textContent = '5%';
  try {
    await window.pywebview.api.start_render_zoom_video(
      zoomAlignedItems, zoomActiveAudio.path, outPath, width, height, fps,
      zoomNvencToggle.checked, zoomZoomType.value, parseFloat(zoomZoomAmount.value),
      zoomTransitionType.value, parseFloat(zoomTransitionDuration.value)
    );
  } catch (err) {
    resetZoomRenderProgress();
    alert(`Error exporting final video: ${err}`);
  }
}

let lastZoomRenderResult = null;
window.onZoomVideoProgress = function(percent, statusMsg) {
  zoomProgressBarFill.style.width = `${percent}%`;
  zoomProgressPercent.textContent = `${Math.round(percent)}%`;
  zoomProgressStatusText.textContent = statusMsg;
};
window.onZoomVideoComplete = function(result) {
  resetZoomRenderProgress();
  lastZoomRenderResult = result;
  zoomSuccessCard.style.display = 'block';
  zoomSuccessDetails.textContent = `${result.duration_formatted} · ${result.size_formatted} · ${result.resolution} @ ${result.fps}fps · ${result.image_count} images`;
  zoomVideoPlayer.src = `/stream_video?path=${encodeURIComponent(result.path)}&t=${Date.now()}`;
  zoomVideoPlayer.load();
};
window.onZoomVideoCancelled = function() {
  resetZoomRenderProgress();
  alert('Final video export was cancelled.');
};
window.onZoomVideoError = function(errMsg) {
  resetZoomRenderProgress();
  alert(`Final video export error:\n${errMsg}`);
};
function resetZoomRenderProgress() {
  zoomProgressCard.style.display = 'none';
  zoomApplyBtn.disabled = !(zoomActiveAudio && zoomAlignedItems.length > 0);
  zoomProgressBarFill.style.width = '0%';
  zoomProgressPercent.textContent = '0%';
}
