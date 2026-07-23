#!/usr/bin/env bash

set -euo pipefail

recordings_root=${FRICAT_RECORDINGS_ROOT:-/fastpool/frigate/recordings}
container=${FRIGATE_CONTAINER:-frigate}
sample_seconds=${FRICAT_SAMPLE_SECONDS:-12}
log_since=${FRICAT_LOG_SINCE:-30m}
canary_camera=${FRICAT_CANARY_CAMERA-CAM8}
timestamp=$(date --utc +%Y%m%dT%H%M%SZ)
output_dir=${FRICAT_DIAG_OUTPUT_DIR:-/tmp/fricat-audio-diagnostic-$timestamp}
ffmpeg=/usr/lib/ffmpeg/7.0/bin/ffmpeg

usage() {
  cat <<'EOF'
Usage: confirm_audio_reconnect.sh [CAMERA ...]

Run this while malformed audio is occurring, before restarting Frigate.

Affected cameras are read from the UNHEALTHY lines printed by
`fricat check-segments`. Pass camera names to test additional cameras. The
CAM8 canary is always included by default, even when it is healthy.

Environment variables:
  FRICAT_RECORDINGS_ROOT   Recording archive (default: /fastpool/frigate/recordings)
  FRIGATE_CONTAINER       Docker container name (default: frigate)
  FRICAT_SAMPLE_SECONDS   Fresh sample duration (default: 12)
  FRICAT_LOG_SINCE        Docker log lookback (default: 30m)
  FRICAT_CANARY_CAMERA    Canary to always test (default: CAM8; empty disables)
  FRICAT_DIAG_OUTPUT_DIR  Evidence bundle directory (default: /tmp/fricat-audio-diagnostic-TIMESTAMP)

The script only reads diagnostics and creates short samples. It does not kill
processes, restart Frigate, or modify the recording archive.
EOF
}

if [[ ${1:-} == '-h' || ${1:-} == '--help' ]]; then
  usage
  exit 0
fi

for command in fricat docker ffprobe; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $command" >&2
    exit 2
  fi
done

if [[ ! -d $recordings_root ]]; then
  echo "ERROR: recording archive does not exist: $recordings_root" >&2
  exit 2
fi

mkdir -p "$output_dir"

echo "Evidence directory: $output_dir"
echo "Running segment health check..."
set +e
fricat check-segments "$recordings_root" 2>&1 | tee "$output_dir/check-segments.txt"
check_status=${PIPESTATUS[0]}
set -e

mapfile -t cameras < <(
  {
    awk '$1 == "UNHEALTHY" { sub(/:$/, "", $2); print $2 }' \
      "$output_dir/check-segments.txt"
    printf '%s\n' "$@"
    if [[ -n $canary_camera ]]; then
      printf '%s\n' "$canary_camera"
    fi
  } | awk 'NF && !seen[$0]++'
)

if (( ${#cameras[@]} == 0 )); then
  if (( check_status == 0 )); then
    echo 'No unhealthy or explicitly selected cameras found.'
    exit 0
  fi
  echo 'ERROR: health check failed, and no unhealthy cameras could be identified.' >&2
  exit 2
fi

for camera in "${cameras[@]}"; do
  if [[ ! $camera =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "ERROR: unsafe camera name: $camera" >&2
    exit 2
  fi
done

echo "Capturing Frigate logs from the last $log_since..."
if ! docker logs --timestamps --since "$log_since" "$container" 2>&1 | \
  sed -E 's#(rtsp://)[^/@[:space:]]+:[^/@[:space:]]+@#\1REDACTED@#g' \
    >"$output_dir/frigate.log"; then
  echo 'WARNING: unable to capture Frigate logs' >&2
fi

probe_file() {
  local path=$1
  local report=$2

  {
    echo "File: $path"
    echo 'Audio stream summary:'
    ffprobe -v error -select_streams a:0 -count_packets \
      -show_entries stream=codec_name,sample_rate,duration,nb_read_packets \
      -of default=noprint_wrappers=1 "$path"
    echo 'First three audio packets:'
    ffprobe -v error -select_streams a:0 -read_intervals '%+#3' \
      -show_entries packet=pts_time,dts_time,duration_time,size \
      -of compact=p=0:nk=0 "$path"
  } >"$report" 2>&1
}

for camera in "${cameras[@]}"; do
  echo
  echo "=== $camera ==="
  camera_dir="$output_dir/$camera"
  mkdir -p "$camera_dir"

  docker exec "$container" pgrep -af "$camera@%Y%m%d%H%M%S%z.mp4" \
    >"$camera_dir/recorder-process.txt" 2>&1 || true

  newest_segment=$(
    find "$recordings_root" -type f -path "*/$camera/*.mp4" -mmin +0.25 \
      -printf '%T@ %p\n' 2>/dev/null | \
      awk '$1 > newest { newest = $1; $1 = ""; sub(/^ /, ""); path = $0 } END { print path }'
  )
  if [[ -n $newest_segment ]]; then
    if probe_file "$newest_segment" "$camera_dir/existing-segment.txt"; then
      echo "Existing segment: $newest_segment"
    else
      echo "WARNING: unable to probe existing segment for $camera" >&2
    fi
  else
    echo 'WARNING: no completed existing segment found' | \
      tee "$camera_dir/existing-segment.txt" >&2
  fi

  container_sample="/tmp/fricat-audio-diagnostic-$camera-$timestamp.mp4"
  host_sample="$camera_dir/fresh-sample.mp4"
  echo "Recording a fresh ${sample_seconds}s consumer sample..."
  if timeout "$((sample_seconds + 20))" docker exec "$container" "$ffmpeg" \
    -nostdin -hide_banner -loglevel warning \
    -rtsp_transport tcp -timeout 10000000 \
    -i "rtsp://127.0.0.1:8554/$camera" \
    -t "$sample_seconds" -c:v copy -c:a aac -y "$container_sample" \
    >"$camera_dir/fresh-recording.log" 2>&1; then
    if docker cp "$container:$container_sample" "$host_sample" \
      >>"$camera_dir/fresh-recording.log" 2>&1; then
      if probe_file "$host_sample" "$camera_dir/fresh-sample.txt"; then
        echo "Fresh sample:    $host_sample"
      else
        echo "WARNING: unable to probe fresh sample for $camera" >&2
      fi
    else
      echo 'WARNING: unable to copy fresh sample from the container' >&2
    fi
  else
    echo "WARNING: fresh recording failed for $camera" >&2
  fi
done

echo
echo "Done. Evidence is in $output_dir"
echo 'Compare each existing-segment.txt with fresh-sample.txt.'
echo 'A malformed existing segment plus a healthy fresh sample confirms that the'
echo 'long-lived Frigate recorder is poisoned while a new go2rtc consumer is healthy.'
echo 'If existing segments are stale rather than malformed, frigate.log captures the'
echo 'upstream reconnect and recorder-watchdog recovery timeline.'
