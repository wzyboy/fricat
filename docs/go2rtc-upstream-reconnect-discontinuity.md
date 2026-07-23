# go2rtc upstream reconnect can corrupt a persistent RTSP consumer timeline

## Summary

When an upstream RTSP camera briefly becomes unavailable, go2rtc reconnects the
producer and moves the existing downstream consumers to replacement tracks.
The replacement tracks can have unrelated RTP sequence numbers, timestamps, and
SSRCs. Those values are forwarded unchanged into RTSP sessions that remained
connected throughout the outage.

Long-lived FFmpeg consumers can consequently stall, exit, or continue with a
corrupt audio timeline. In the latter case, Frigate records segments containing
one AAC packet with an enormous duration until its recording process is
restarted.

Expected behavior is a short recording gap followed by recovery. A transient
upstream outage should not permanently corrupt a downstream session.

## Observed environment

- Frigate 0.17.2
- Bundled go2rtc 1.9.10
- FFmpeg 7.0.2
- Eight TP-Link Tapo RTSP cameras
- Camera audio: PCMA at 8000 Hz
- Frigate record input: go2rtc RTSP restream
- Frigate record output: copied H.264 and AAC-transcoded audio

The relevant replacement behavior is unchanged in go2rtc 1.9.14.

## Evidence

During several incidents, go2rtc logged upstream `i/o timeout` errors for
multiple cameras. FFmpeg subsequently logged errors such as:

```text
RTP: PT=60: bad cseq ... expected=...
RTP: PT=61: bad cseq ... expected=...
Non-monotonic DTS
```

Two downstream failure modes were observed after the same type of reconnect:

1. The Frigate recorder stopped creating valid segments. Frigate's 120-second
   watchdog eventually restarted that camera's recording process.
2. The recorder remained alive and created short segments containing one
   33-byte AAC packet. That packet's declared duration increased at the same
   rate as wall-clock time. This continued indefinitely until Frigate was
   restarted.

A fresh FFmpeg consumer connected to the same go2rtc stream during an incident
recorded healthy audio. For example, it produced 95 AAC packets over 12 seconds.
This isolates the bad state to the persistent downstream session rather than
the camera's current stream.

## Suspected mechanism

`internal/streams/producer.go` reconnects a producer, obtains new tracks, and
calls `receiver.Replace(track)`.

`pkg/core/track.go` implements `Receiver.Replace` by moving the old receiver's
children to the new receiver. It does not signal a discontinuity or normalize
RTP state.

`pkg/rtsp/consumer.go` copies the upstream packet's sequence number, timestamp,
and SSRC into the persistent downstream RTSP connection.

The result is a new RTP epoch spliced into an established RTP session.

## Desired fix

The safest recovery behavior may be to terminate affected downstream RTSP
consumers when an upstream producer is replaced with a discontinuous track.
Frigate/FFmpeg can then reconnect with a fresh RTP session and timeline.

An alternative is to maintain an independent downstream RTP identity and rebase
sequence numbers and timestamps across producer replacement. That is more
complex because audio and video use different clock rates and video can contain
multiple RTP packets per timestamp.

Useful questions for further research:

- Does the replacement camera track always change SSRC?
- If SSRC is reused, which sequence-number or timestamp threshold safely
  distinguishes reconnects from ordinary packet loss?
- Should producer replacement expose an explicit discontinuity event to
  consumers?
- Can only RTSP consumers be disconnected without disrupting unrelated WebRTC
  or internal consumers?
- Would an `RTP-Info` response help only at initial `PLAY`, or is reconnecting
  the downstream session still required?

## Current mitigation experiment

One Frigate canary camera uses `preset-rtsp-generic` for its go2rtc record
input. This adds generated/wall-clock timestamp handling, corrupt-packet
discarding, and negative-timestamp normalization. Other cameras retain
`preset-rtsp-restream` so behavior can be compared during the next natural
network interruption.

This mitigation may prevent FFmpeg from trusting the discontinuous RTP clock,
but it is not a substitute for correcting or explicitly signaling producer
replacement in go2rtc.
