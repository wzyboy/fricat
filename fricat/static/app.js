const calendarEl = document.getElementById('calendar');
const recordingsEl = document.getElementById('recordings');
const selectedDateEl = document.getElementById('selected-date');
const timezoneEl = document.getElementById('timezone');
const cameraEl = document.getElementById('camera');
const monthEl = document.getElementById('month');
const playerEl = document.getElementById('player');
const playerMetaEl = document.getElementById('player-meta');
const heatbarEl = document.getElementById('heatbar');

let recordings = [];
let recordingsByDay = new Map();
let selectedDayKey = null;
let activeSegments = [];
let heatbarBins = [];

function pad(value) {
  return String(value).padStart(2, '0');
}

function localDayKey(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function formatLocalTime(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatLocalDate(date) {
  return date.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
}

function monthStart(date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function monthEnd(date) {
  return new Date(date.getFullYear(), date.getMonth() + 1, 1);
}

function setTimezoneLabel() {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  timezoneEl.textContent = tz ? `Local time: ${tz}` : 'Local time';
}

async function fetchCameras() {
  const response = await fetch('/api/cameras');
  const cameras = await response.json();
  cameraEl.innerHTML = '<option value="">All cameras</option>';
  cameras.forEach((camera) => {
    const option = document.createElement('option');
    option.value = camera;
    option.textContent = camera;
    cameraEl.appendChild(option);
  });
}

async function fetchRecordingsForMonth(date) {
  const start = monthStart(date);
  const end = monthEnd(date);
  const params = new URLSearchParams({
    start: (start.getTime() / 1000).toString(),
    end: (end.getTime() / 1000).toString(),
  });
  if (cameraEl.value) {
    params.set('camera', cameraEl.value);
  }
  const response = await fetch(`/api/recordings?${params.toString()}`);
  recordings = await response.json();
  recordingsByDay = new Map();
  recordings.forEach((rec) => {
    const recDate = new Date(rec.start_utc);
    const key = localDayKey(recDate);
    if (!recordingsByDay.has(key)) {
      recordingsByDay.set(key, []);
    }
    recordingsByDay.get(key).push(rec);
  });
  recordingsByDay.forEach((dayRecs) =>
    dayRecs.sort((a, b) => new Date(a.start_utc) - new Date(b.start_utc))
  );
}

function renderCalendar(date) {
  calendarEl.innerHTML = '';
  const start = monthStart(date);
  const daysInMonth = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  const leadingEmpty = (start.getDay() + 6) % 7; // Monday first

  for (let i = 0; i < leadingEmpty; i += 1) {
    const empty = document.createElement('div');
    empty.className = 'day empty';
    calendarEl.appendChild(empty);
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    const cellDate = new Date(date.getFullYear(), date.getMonth(), day);
    const key = localDayKey(cellDate);
    const dayRecs = recordingsByDay.get(key) || [];

    const cell = document.createElement('div');
    cell.className = 'day';
    if (key === selectedDayKey) {
      cell.classList.add('active');
    }
    const header = document.createElement('div');
    header.className = 'day-header';
    header.textContent = day;
    cell.appendChild(header);

    if (dayRecs.length > 0) {
      const badge = document.createElement('div');
      badge.className = 'day-count';
      badge.textContent = `${dayRecs.length} hr`;
      cell.appendChild(badge);
    }

    cell.addEventListener('click', () => {
      if (dayRecs.length === 0) {
        return;
      }
      selectedDayKey = key;
      renderCalendar(date);
      renderRecordingsForDay(key, cellDate);
    });

    calendarEl.appendChild(cell);
  }
}

function renderRecordingsForDay(key, date) {
  selectedDateEl.textContent = formatLocalDate(date);
  recordingsEl.innerHTML = '';
  const dayRecs = recordingsByDay.get(key) || [];
  if (dayRecs.length === 0) {
    recordingsEl.textContent = 'No recordings.';
    return;
  }
  dayRecs.forEach((rec) => {
    const row = document.createElement('div');
    row.className = 'recording';

    const time = document.createElement('div');
    time.className = 'recording-time';
    const recDate = new Date(rec.start_utc);
    time.textContent = formatLocalTime(recDate);

    const camera = document.createElement('div');
    camera.className = 'recording-camera';
    camera.textContent = rec.camera;

    row.appendChild(time);
    row.appendChild(camera);

    row.addEventListener('click', () => {
      const src = `/media/${rec.path}`;
      playerEl.src = src;
      playerEl.play();
      playerMetaEl.textContent = `${rec.camera} • ${formatLocalDate(recDate)} ${formatLocalTime(recDate)}`;
      loadSidecar(rec.path);
    });

    recordingsEl.appendChild(row);
  });
}

function buildHeatbarBins(segments) {
  const bins = new Array(360).fill(0);
  segments.forEach((segment) => {
    const motion = Number(segment.motion ?? 0);
    if (!Number.isFinite(segment.offset) || !Number.isFinite(segment.duration)) {
      return;
    }
    const start = Math.max(0, segment.offset);
    const end = Math.min(3600, segment.offset + segment.duration);
    const startBin = Math.floor(start / 10);
    const endBin = Math.min(359, Math.floor((end - 0.001) / 10));
    for (let i = startBin; i <= endBin; i += 1) {
      bins[i] = Math.max(bins[i], motion);
    }
  });
  return bins;
}

function renderHeatbar() {
  const bins = heatbarBins;
  const ctx = heatbarEl.getContext('2d');
  const width = heatbarEl.clientWidth;
  const height = heatbarEl.clientHeight;
  const scale = window.devicePixelRatio || 1;
  heatbarEl.width = Math.floor(width * scale);
  heatbarEl.height = Math.floor(height * scale);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(scale, scale);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#e5ddcf';
  ctx.fillRect(0, 0, width, height);

  if (!bins.length) {
    return;
  }
  const nonZero = bins.filter((value) => value > 0);
  if (nonZero.length === 0) {
    return;
  }
  const scaled = bins.map((value) => Math.log1p(value));
  const scaledNonZero = scaled.filter((value) => value > 0).sort((a, b) => a - b);
  const maxValue = scaledNonZero[scaledNonZero.length - 1];
  const percentileIndex = Math.floor(0.85 * (scaledNonZero.length - 1));
  const floorValue = scaledNonZero[Math.max(0, percentileIndex)];
  const absoluteFloor = Math.log1p(50);
  const threshold = Math.max(floorValue, absoluteFloor);
  const normalized = scaled.map((value) => (value >= threshold ? value / maxValue : 0));

  const smoothWindow = 5;
  const smoothed = normalized.map((_, index) => {
    let sum = 0;
    let count = 0;
    for (let offset = -Math.floor(smoothWindow / 2); offset <= Math.floor(smoothWindow / 2); offset += 1) {
      const idx = index + offset;
      if (idx < 0 || idx >= normalized.length) {
        continue;
      }
      sum += normalized[idx];
      count += 1;
    }
    return count > 0 ? sum / count : 0;
  });

  const step = width / (smoothed.length - 1);
  ctx.beginPath();
  smoothed.forEach((value, index) => {
    const x = index * step;
    const y = height - value * height;
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.strokeStyle = 'rgba(42, 93, 159, 0.9)';
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.lineTo(width, height);
  ctx.lineTo(0, height);
  ctx.closePath();
  ctx.fillStyle = 'rgba(42, 93, 159, 0.18)';
  ctx.fill();
}

function seekFromHeatbar(event) {
  if (!playerEl.src) {
    return;
  }
  const rect = heatbarEl.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  const duration = Number.isFinite(playerEl.duration) ? playerEl.duration : 3600;
  playerEl.currentTime = ratio * duration;
  playerEl.play();
}

async function loadSidecar(path) {
  try {
    const response = await fetch(`/api/meta?path=${encodeURIComponent(path)}`);
    if (!response.ok) {
      heatbarBins = [];
      renderHeatbar();
      return;
    }
    const data = await response.json();
    activeSegments = Array.isArray(data.segments) ? data.segments : [];
    heatbarBins = buildHeatbarBins(activeSegments);
    renderHeatbar();
  } catch (error) {
    heatbarBins = [];
    renderHeatbar();
  }
}

async function refresh() {
  const value = monthEl.value;
  const [year, month] = value.split('-').map((part) => Number(part));
  const current = new Date(year, month - 1, 1);
  await fetchRecordingsForMonth(current);
  renderCalendar(current);
  selectedDateEl.textContent = 'Select a day';
  recordingsEl.innerHTML = '';
}

function setDefaultMonth() {
  const now = new Date();
  monthEl.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}`;
}

cameraEl.addEventListener('change', refresh);
monthEl.addEventListener('change', refresh);
window.addEventListener('resize', renderHeatbar);
heatbarEl.addEventListener('click', seekFromHeatbar);

(async () => {
  setTimezoneLabel();
  setDefaultMonth();
  await fetchCameras();
  await refresh();
})();
