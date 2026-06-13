/**
 * Fricat Design 2.0 - Forensic NVR Viewer
 */

class FricatApp {
    constructor() {
        this.state = {
            currentDate: document.getElementById('date-picker')?.value || this.getTodayDateString(),
            currentCamera: null,
            currentHour: null,
            recordings: [],
            isPlaying: false,
            autoplay: true
        };

        // Custom calendar state
        this.recordedDates = [];
        this.calendarDate = this.parseDateString(this.state.currentDate);

        this.elements = {
            video: document.getElementById('main-video'),
            datePicker: document.getElementById('date-picker'),
            currentDateLabel: document.getElementById('current-date-label'),
            hourList: document.getElementById('hour-list'),
            videoTimestamp: document.getElementById('video-timestamp'),
            copyTimestampBtn: document.getElementById('copy-timestamp-btn'),
            playPauseBtn: document.getElementById('play-pause-btn'),
            motionCanvas: document.getElementById('motion-canvas'),
            soundCanvas: document.getElementById('sound-canvas'),
            seekerLine: document.getElementById('seeker-line'),
            autoplayToggle: document.getElementById('autoplay-toggle'),
            cameraSelector: document.querySelector('.camera-selector'),
            cameraBtns: []
        };

        this.init();
    }

    async init() {
        await this.loadCameras();
        this.bindEvents();
        this.initCalendar();
        await this.loadRecordedDates();
        this.initializeCurrentDate();
        await this.loadDay();
        this.renderCalendar();
        this.updateUI();
    }

    bindEvents() {
        this.elements.datePicker.addEventListener('change', (e) => {
            this.state.currentDate = e.target.value;
            this.calendarDate = this.parseDateString(this.state.currentDate);
            this.loadDay();
            this.renderCalendar();
        });

        this.elements.cameraBtns.forEach(btn => {
            btn.addEventListener('click', async () => {
                this.elements.cameraBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.state.currentCamera = btn.dataset.camera;
                await this.loadRecordedDates();
                await this.loadDay();
                this.renderCalendar();
            });
        });

        this.elements.playPauseBtn.addEventListener('click', () => this.togglePlay());
        
        // Seek Buttons
        document.getElementById('rew-10s').onclick = () => this.seek(-10);
        document.getElementById('ff-10s').onclick = () => this.seek(10);
        document.getElementById('rew-60s').onclick = () => this.seek(-60);
        document.getElementById('ff-60s').onclick = () => this.seek(60);
        document.getElementById('rew-5m').onclick = () => this.seek(-300);
        document.getElementById('ff-5m').onclick = () => this.seek(300);

        document.getElementById('next-hour').onclick = () => this.navigateHour(1);
        document.getElementById('prev-hour').onclick = () => this.navigateHour(-1);

        this.elements.autoplayToggle.onchange = (e) => this.state.autoplay = e.target.checked;

        this.elements.video.ontimeupdate = () => this.onTimeUpdate();
        this.elements.video.onended = () => {
            if (this.state.autoplay) this.navigateHour(1);
        };

        this.elements.copyTimestampBtn.onclick = () => this.copyTimestamp();

        document.getElementById('screenshot-btn').onclick = () => this.takeScreenshot();
        document.getElementById('fullscreen-btn').onclick = () => {
            if (this.elements.video.requestFullscreen) this.elements.video.requestFullscreen();
        };

        // Activity Seeker Interaction
        document.getElementById('activity-charts').onclick = (e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const percent = x / rect.width;
            if (this.elements.video.duration) {
                this.elements.video.currentTime = this.elements.video.duration * percent;
            }
        };
    }

    async loadCameras() {
        if (!this.elements.cameraSelector) return;

        try {
            const res = await fetch('/api/cameras');
            const cameras = res.ok ? await res.json() : [];
            this.elements.cameraSelector.innerHTML = '';
            this.elements.cameraBtns = [];
            this.state.currentCamera = null;

            if (!Array.isArray(cameras)) return;

            cameras.forEach((camera, index) => {
                const btn = document.createElement('button');
                btn.className = `camera-btn${index === 0 ? ' active' : ''}`;
                btn.dataset.camera = camera;
                btn.textContent = camera;
                this.elements.cameraSelector.appendChild(btn);
                this.elements.cameraBtns.push(btn);
            });

            if (cameras.length > 0) {
                this.state.currentCamera = cameras[0];
            }
        } catch (err) {
            console.error('Failed to load cameras', err);
            this.elements.cameraSelector.innerHTML = '';
            this.elements.cameraBtns = [];
            this.state.currentCamera = null;
        }
    }

    getLocalParts(date) {
        const formatter = new Intl.DateTimeFormat('en-US', {
            timeZone: 'America/Vancouver',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hourCycle: 'h23'
        });
        const parts = formatter.formatToParts(new Date(date));
        const p = {};
        for (const part of parts) {
            p[part.type] = part.value;
        }
        return p;
    }

    getTodayDateString() {
        const parts = this.getLocalParts(new Date());
        return `${parts.year}-${parts.month}-${parts.day}`;
    }

    parseDateString(dateStr) {
        const [year, month, day] = dateStr.split('-').map(Number);
        return new Date(year, month - 1, day);
    }

    initializeCurrentDate() {
        const latestRecordedDate = this.recordedDates.at(-1);
        this.state.currentDate = latestRecordedDate || this.getTodayDateString();
        this.elements.datePicker.value = this.state.currentDate;
        this.calendarDate = this.parseDateString(this.state.currentDate);
    }

    async loadRecordedDates() {
        if (!this.state.currentCamera) {
            this.recordedDates = [];
            return;
        }

        try {
            const res = await fetch(`/api/recorded_dates?camera=${this.state.currentCamera}`);
            if (res.ok) {
                const data = await res.json();
                this.recordedDates = Array.isArray(data) ? data : [];
            } else {
                this.recordedDates = [];
            }
        } catch (err) {
            console.error('Failed to load recorded dates', err);
            this.recordedDates = [];
        }
    }

    initCalendar() {
        const prevBtn = document.getElementById('cal-prev-month');
        const nextBtn = document.getElementById('cal-next-month');
        if (prevBtn) {
            prevBtn.onclick = () => {
                this.calendarDate.setMonth(this.calendarDate.getMonth() - 1);
                this.renderCalendar();
            };
        }
        if (nextBtn) {
            nextBtn.onclick = () => {
                this.calendarDate.setMonth(this.calendarDate.getMonth() + 1);
                this.renderCalendar();
            };
        }
    }

    renderCalendar() {
        const calMonthYear = document.getElementById('cal-month-year');
        const calDaysGrid = document.getElementById('calendar-days-grid');
        if (!calMonthYear || !calDaysGrid) return;

        const year = this.calendarDate.getFullYear();
        const month = this.calendarDate.getMonth();

        // Update header month & year
        const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
        calMonthYear.textContent = `${monthNames[month]} ${year}`;

        // Clear grid
        calDaysGrid.innerHTML = '';

        // Day of week of the first of the month
        const firstDay = new Date(year, month, 1).getDay();
        // Number of days in the month
        const numDays = new Date(year, month + 1, 0).getDate();

        // Empty cells for preceding weekdays
        for (let i = 0; i < firstDay; i++) {
            const cell = document.createElement('div');
            cell.className = 'cal-day-cell empty';
            calDaysGrid.appendChild(cell);
        }

        // Selected date parts
        const selectedParts = this.state.currentDate.split('-');
        const selYear = parseInt(selectedParts[0]);
        const selMonth = parseInt(selectedParts[1]) - 1;
        const selDay = parseInt(selectedParts[2]);

        // Add cells for all days in the month
        for (let day = 1; day <= numDays; day++) {
            const cell = document.createElement('button');
            cell.className = 'cal-day-cell';
            cell.textContent = day;

            const dateStr = `${year}-${(month + 1).toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;

            // Check if selected
            if (year === selYear && month === selMonth && day === selDay) {
                cell.classList.add('active');
            }

            // Check if it has recordings
            if (Array.isArray(this.recordedDates) && this.recordedDates.includes(dateStr)) {
                cell.classList.add('has-recordings');
            }

            cell.onclick = () => {
                this.state.currentDate = dateStr;
                this.elements.datePicker.value = dateStr;
                
                // Dispatch event so any existing listeners trigger
                const event = new Event('change');
                this.elements.datePicker.dispatchEvent(event);
                
                this.renderCalendar();
            };

            calDaysGrid.appendChild(cell);
        }
    }

    async loadDay() {
        if (!this.state.currentCamera) {
            this.state.recordings = [];
            this.renderHourList();
            this.clearVideo();
            return;
        }

        const parts = this.state.currentDate.split('-');
        const start = Date.UTC(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2])) / 1000;
        // Fetch 12 hours before and 12 hours after the UTC day range to ensure we capture all files corresponding to this local day
        const queryStart = start - 43200;
        const queryEnd = start + 86400 + 43200;
        
        try {
            const res = await fetch(`/api/recordings?start=${queryStart}&end=${queryEnd}&camera=${this.state.currentCamera}`);
            const allRecordings = await res.json();
            
            // Filter recordings whose start time in local Vancouver time is on the current date
            this.state.recordings = allRecordings.filter(r => {
                const lp = this.getLocalParts(r.start_utc);
                const localDateStr = `${lp.year}-${lp.month}-${lp.day}`;
                return localDateStr === this.state.currentDate;
            });

            this.renderHourList();
            
            // Auto-load first recording if available
            if (this.state.recordings.length > 0) {
                this.loadRecording(this.state.recordings[0]);
            } else {
                this.clearVideo();
            }
        } catch (err) {
            console.error('Failed to load recordings', err);
        }
    }

    renderHourList() {
        this.elements.hourList.innerHTML = '';
        const dateObj = this.parseDateString(this.state.currentDate);
        this.elements.currentDateLabel.textContent = dateObj.toLocaleDateString('en-US', { 
            weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' 
        });

        // 24 Hour Map
        for (let h = 0; h < 24; h++) {
            const hourStr = h.toString().padStart(2, '0');
            const recording = this.state.recordings.find(r => {
                const lp = this.getLocalParts(r.start_utc);
                return parseInt(lp.hour) === h;
            });

            const daylightClass = this.getDaylightClass(h);

            const item = document.createElement('div');
            item.className = `hour-item ${recording ? 'available' : 'empty'} ${daylightClass}`;
            if (recording && recording.path === this.state.currentHour?.path) item.classList.add('active');
            
            let miniBarHTML = '';
            if (recording && recording.profile) {
                const motionBars = recording.profile.motion.map((val, i) => {
                    const x = (i / 24) * 100;
                    const height = (val / 100) * 100;
                    return `<rect x="${x}%" y="${100 - height}%" width="3%" height="${height}%" fill="#F97316" opacity="0.6"/>`;
                }).join('');

                const soundBars = recording.profile.sound.map((val, i) => {
                    const x = (i / 24) * 100;
                    const height = (val / 100) * 100;
                    return `<rect x="${x}%" y="${100 - height}%" width="3%" height="${height}%" fill="#38BDF8" opacity="0.4"/>`;
                }).join('');

                miniBarHTML = `
                    <svg width="100%" height="100%" preserveAspectRatio="none">
                        ${motionBars}
                        ${soundBars}
                    </svg>
                `;
            }

            item.innerHTML = `
                <span class="hour-label">${hourStr}:00</span>
                <div class="mini-activity-bar">
                    ${miniBarHTML}
                </div>
            `;

            if (recording) {
                item.onclick = () => this.loadRecording(recording);
            }
            this.elements.hourList.appendChild(item);
        }
    }

    getDaylightClass(hour) {
        if (hour >= 21 || hour < 5) return 'daylight-night';
        if (hour >= 5 && hour < 8) return 'daylight-dawn';
        if (hour >= 18 && hour < 21) return 'daylight-dawn'; // Dusk is similar to dawn
        if (hour === 12) return 'daylight-noon';
        return 'daylight-day';
    }

    async loadRecording(recording) {
        this.state.currentHour = recording;
        this.elements.video.src = `/media/${recording.path}`;
        this.state.isPlaying = false;
        this.updateUI();
        this.renderHourList();
        this.playVideo();

        // Load Activity Meta
        if (recording.has_meta) {
            this.loadActivity(recording.path);
        } else {
            this.clearActivity();
        }
    }

    async playVideo() {
        try {
            await this.elements.video.play();
            this.state.isPlaying = true;
        } catch (err) {
            console.warn('Playback failed', err);
            this.state.isPlaying = false;
        }
        this.updateUI();
    }

    async loadActivity(path) {
        try {
            const res = await fetch(`/api/meta?path=${path}`);
            const data = await res.json();
            this.drawActivity(data);
        } catch (e) {
            console.error('Meta load failed', e);
        }
    }

    drawActivity(data) {
        const motionCanvas = this.elements.motionCanvas;
        const soundCanvas = this.elements.soundCanvas;
        const motionCtx = motionCanvas.getContext('2d');
        const soundCtx = soundCanvas.getContext('2d');
        const w = motionCanvas.width = motionCanvas.offsetWidth;
        const h = motionCanvas.height = motionCanvas.offsetHeight;
        soundCanvas.width = w;
        soundCanvas.height = h;

        motionCtx.clearRect(0, 0, w, h);
        soundCtx.clearRect(0, 0, w, h);

        const duration = 3600; // 1 hour focus

        if (!data || !data.segments) return;

        // Motion (Orange) - Tactical Bar Chart
        motionCtx.fillStyle = 'rgba(249, 115, 22, 0.4)'; // Transparent orange fill
        motionCtx.strokeStyle = '#F97316'; // Bright orange border
        motionCtx.lineWidth = 1;

        data.segments.forEach(seg => {
            const x = (seg.offset / duration) * w;
            const width = (seg.duration / duration) * w;
            const val = seg.motion || 0; // 0 to 100
            const barHeight = (val / 100) * (h - 20);
            const y = h - 10 - barHeight;
            
            motionCtx.fillRect(x, y, Math.max(width, 1), barHeight);
            motionCtx.strokeRect(x, y, Math.max(width, 1), barHeight);
        });

        // Sound (Blue) - Continuous glowing waveform
        soundCtx.strokeStyle = '#38BDF8';
        soundCtx.lineWidth = 1.5;
        soundCtx.beginPath();

        data.segments.forEach((seg, i) => {
            const x = ((seg.offset + seg.duration / 2) / duration) * w;
            const val = seg.audio_dbfs || -80;
            const normalized = Math.max(0, (val + 80) / 80); // Normalize dBFS -80 to 0 as 0 to 1
            const y = h - 10 - (normalized * (h - 20));

            if (i === 0) {
                soundCtx.moveTo(x, y);
            } else {
                soundCtx.lineTo(x, y);
            }
        });
        soundCtx.stroke();

        // Glowing gradient under sound line
        if (data.segments.length > 0) {
            soundCtx.lineTo(w, h - 10);
            soundCtx.lineTo(0, h - 10);
            soundCtx.closePath();
            const grad = soundCtx.createLinearGradient(0, 0, 0, h);
            grad.addColorStop(0, 'rgba(56, 189, 248, 0.15)');
            grad.addColorStop(1, 'rgba(56, 189, 248, 0)');
            soundCtx.fillStyle = grad;
            soundCtx.fill();
        }
    }


    onTimeUpdate() {
        const v = this.elements.video;
        if (!v.duration) return;

        // Update Seeker
        const percent = (v.currentTime / v.duration) * 100;
        this.elements.seekerLine.style.left = `${percent}%`;

        // Update Timestamp
        if (this.state.currentHour) {
            const baseTime = new Date(this.state.currentHour.start_utc);
            const currentTime = new Date(baseTime.getTime() + v.currentTime * 1000);
            const lp = this.getLocalParts(currentTime);
            this.elements.videoTimestamp.textContent = `${lp.year}-${lp.month}-${lp.day} ${lp.hour}:${lp.minute}:${lp.second}`;
        }
    }

    async togglePlay() {
        const v = this.elements.video;
        if (v.paused) {
            await this.playVideo();
        } else {
            v.pause();
            this.state.isPlaying = false;
            this.updateUI();
        }
    }

    seek(seconds) {
        this.elements.video.currentTime += seconds;
    }

    navigateHour(delta) {
        if (!this.state.currentHour) return;
        const currentIdx = this.state.recordings.findIndex(r => r.path === this.state.currentHour.path);
        const nextIdx = currentIdx + delta;
        if (nextIdx >= 0 && nextIdx < this.state.recordings.length) {
            this.loadRecording(this.state.recordings[nextIdx]);
        }
    }

    copyTimestamp() {
        const ts = this.elements.videoTimestamp.textContent;
        navigator.clipboard.writeText(ts).then(() => {
            const oldText = this.elements.copyTimestampBtn.textContent;
            this.elements.copyTimestampBtn.textContent = 'Copied!';
            setTimeout(() => this.elements.copyTimestampBtn.textContent = oldText, 2000);
        });
    }

    takeScreenshot() {
        const v = this.elements.video;
        const canvas = document.createElement('canvas');
        canvas.width = v.videoWidth;
        canvas.height = v.videoHeight;
        canvas.getContext('2d').drawImage(v, 0, 0);
        const link = document.createElement('a');
        link.download = `fricat_${this.elements.videoTimestamp.textContent.replace(/[: ]/g, '_')}.jpg`;
        link.href = canvas.toDataURL('image/jpeg', 0.9);
        link.click();
    }

    updateUI() {
        this.elements.playPauseBtn.innerHTML = this.state.isPlaying ? 
            '<svg viewBox="0 0 16 16"><path fill-rule="evenodd" d="M6.103 1.005A1 1 0 0 1 7 2v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h2l.103.005ZM4 14h2V2H4v12Zm8.102-12.995A1 1 0 0 1 13 2v12a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h2l.102.005ZM10 14h2V2h-2v12Z" clip-rule="evenodd"/></svg>' : 
            '<svg viewBox="0 0 16 16"><path fill-rule="evenodd" d="M3 2a1 1 0 0 1 1.514-.858l10 6a1 1 0 0 1 0 1.715l-10 6A1 1 0 0 1 3 14V2Zm1 12 10-6L4 2v12Z" clip-rule="evenodd"/></svg>';
    }

    clearVideo() {
        this.elements.video.src = '';
        this.elements.videoTimestamp.textContent = '--:--:--';
        this.state.isPlaying = false;
        this.updateUI();
        this.clearActivity();
    }

    clearActivity() {
        const motionCtx = this.elements.motionCanvas.getContext('2d');
        const soundCtx = this.elements.soundCanvas.getContext('2d');
        motionCtx.clearRect(0, 0, this.elements.motionCanvas.width, this.elements.motionCanvas.height);
        soundCtx.clearRect(0, 0, this.elements.soundCanvas.width, this.elements.soundCanvas.height);
    }
}

window.onload = () => {
    window.app = new FricatApp();
};
