const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../fricat/static/app.js'), 'utf8');
const start = Date.parse('2026-10-04T21:00:00Z');
const timestamp = start + 1425.5 * 1000;

function recording(camera, hour = 21) {
    return { camera, start_utc: `2026-10-04T${hour}:00:00Z`, path: `${hour}_${camera}.mkv` };
}

function element() {
    return {
        style: {},
        classList: { add() {}, remove() {} },
        listeners: {},
        addEventListener(event, listener) { this.listeners[event] = listener; },
        setAttribute(name, value) { this[name] = value; }
    };
}

function setup(fetchRecordings = async camera => [recording(camera, 10), recording(camera)]) {
    const context = vm.createContext({
        window: {}, console, URLSearchParams,
        document: { getElementById: () => element() },
        fetch: async url => {
            const camera = new URL(url, 'http://localhost').searchParams.get('camera');
            return { json: async () => fetchRecordings(camera) };
        }
    });
    const App = vm.runInContext(`${source}\nFricatApp`, context);
    const app = Object.create(App.prototype);
    app.state = {
        timezone: 'America/Vancouver', currentDate: '2026-10-04', currentCamera: 'A',
        currentHour: recording('A'), playbackTimestamp: null, pendingSeek: null,
        recordings: [], playbackRate: 2
    };
    const video = {
        ...element(), currentTime: 1425.5, duration: 3600, readyState: 1,
        currentSrc: '/media/21_A.mkv', source: '/media/21_A.mkv', plays: 0,
        get src() { return this.source; },
        set src(value) {
            this.source = value;
            this.currentTime = 0;
            this.duration = NaN;
            this.readyState = 0;
        },
        async play() { this.plays += 1; }
    };
    app.elements = new Proxy({ video }, {
        get(target, key) { return target[key] ??= element(); }
    });
    app.elements.cameraBtns = ['A', 'B', 'C'].map(camera => ({ ...element(), dataset: { camera } }));
    for (const method of ['resetClip', 'updateUI', 'renderHourList', 'clearActivity',
        'updateClipMarkers', 'refreshRecordedDates', 'renderCalendar']) app[method] = () => {};
    app.dayRequestSeq = app.activityRequestSeq = 0;
    app.bindEvents();
    return {
        app, video,
        switchCamera: camera => app.elements.cameraBtns.find(btn => btn.dataset.camera === camera).listeners.click(),
        metadata(duration = 3600) {
            video.currentSrc = video.src;
            video.readyState = 1;
            video.duration = duration;
            video.onloadedmetadata();
        }
    };
}

test('camera switch restores the timestamp after metadata and retains playback speed', async () => {
    const { app, video, switchCamera, metadata } = setup();
    await switchCamera('B');
    assert.equal(app.state.currentHour.path, '21_B.mkv');
    assert.equal(app.state.playbackTimestamp, timestamp);
    metadata();
    assert.equal(video.currentTime, 1425.5);
    assert.equal(video.playbackRate, 2);
    assert.equal(video.plays, 1);
    assert.equal(app.elements.videoTimestamp.textContent, '2026-10-04 14:23:45');
});

test('missing hour keeps the timestamp for switching back', async () => {
    const { app, video, switchCamera, metadata } = setup(async camera =>
        camera === 'B' ? [recording(camera, 10)] : [recording(camera)]);
    await switchCamera('B');
    assert.equal(app.state.currentHour, null);
    assert.equal(app.elements.videoTimestamp.textContent, '2026-10-04 14:23:45');
    await switchCamera('A');
    metadata();
    assert.equal(video.currentTime, 1425.5);
});

test('rapid camera switches keep the timestamp and apply the latest response', async () => {
    let resolveB;
    const bRecordings = new Promise(resolve => { resolveB = resolve; });
    const { app, video, switchCamera, metadata } = setup(camera =>
        camera === 'B' ? bRecordings : [recording(camera)]);
    const switchB = switchCamera('B');
    await switchCamera('C');
    resolveB([recording('B')]);
    await switchB;
    assert.equal(app.state.currentHour.path, '21_C.mkv');
    metadata();
    assert.equal(video.currentTime, 1425.5);
});

test('switching before metadata preserves the pending timestamp', async () => {
    const { app, video, switchCamera, metadata } = setup();
    await switchCamera('B');
    await switchCamera('C');
    video.onloadedmetadata();
    assert.equal(app.state.playbackTimestamp, timestamp);
    metadata();
    assert.equal(app.state.currentHour.camera, 'C');
    assert.equal(video.currentTime, 1425.5);
});

test('short recordings clamp the restored offset to their duration', async () => {
    const { video, switchCamera, metadata } = setup();
    await switchCamera('B');
    metadata(1200);
    assert.equal(video.currentTime, 1200);
});

test('manual recording selection and day loading start at the selected recording', async () => {
    const { app, video, switchCamera, metadata } = setup();
    await switchCamera('B');
    app.loadRecording(recording('B', 22));
    metadata();
    assert.equal(video.currentTime, 0);
    assert.equal(app.state.playbackTimestamp, null);
    await app.loadDay();
    metadata();
    assert.equal(app.state.currentHour.path, '10_B.mkv');
    assert.equal(video.currentTime, 0);
});
