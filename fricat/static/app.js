const calendarEl = document.getElementById('calendar');
const recordingsEl = document.getElementById('recordings');
const selectedDateEl = document.getElementById('selected-date');
const timezoneEl = document.getElementById('timezone');
const cameraEl = document.getElementById('camera');
const monthEl = document.getElementById('month');
const playerEl = document.getElementById('player');
const playerMetaEl = document.getElementById('player-meta');

let recordings = [];
let recordingsByDay = new Map();
let selectedDayKey = null;

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
    });

    recordingsEl.appendChild(row);
  });
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

(async () => {
  setTimezoneLabel();
  setDefaultMonth();
  await fetchCameras();
  await refresh();
})();
