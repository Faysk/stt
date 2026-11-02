# Incident review: Whisper tail timestamps rejected by API (2026-10-30)

Tracking: [#69](https://github.com/Faysk/stt/issues/69); fix proposed in PR #71.

## Scope and privacy

The diagnostic sample was synthetic operational testing. **Do not restore or
reprocess the historical job.** The source media, transcript text, credentials,
leases, device paths and outbox payloads are not part of this review. The private
outbox remains subject to the existing 24-hour retention policy.

## Observed behavior (all times UTC)

- 18:50:56: anonymous transcription job submitted.
- 18:51:00: attempt 1 started; the inference process returned 147 segments.
- 18:51:17: attempt 1 result rejected with HTTP 422 and quarantined.
- 18:57-19:01: two separate OS shutdown/restart sequences took place **after**
  the first 422 rejection. They are not the cause of the original rejection.
- 19:17:43: server requeued after missing heartbeat.
- 19:17:58-19:18:22: attempt 2 produced 147 segments, rejected with HTTP 422.
- 19:21:15: server requeued again after missing heartbeat.
- 19:21:18-19:21:34: attempt 3 produced 147 segments, rejected with HTTP 422.
- 19:24:47: server reported `heartbeat_timeout` after exhausting three tries.

The operator's read-only offline Pydantic probe found the **same failure on
all three quarantined results**: `end_outside_declared_duration`, affecting
the final two segments (0-based indices 145-146). No transcript text was
collected by this report.

## Root cause and contributing defect

`server/infer.py` previously reported `info.duration` supplied by the ASR
model and unmodified model-generated segment endpoints. Faster-Whisper can
output segment ends past the reported duration near the audio tail. The API
contract intentionally rejects result segments with
`segment.end > duration + 0.5` rather than generating invalid subtitles.

The worker correctly persisted each rejected result privately, but treated
HTTP 422 as a terminal *delivery* quarantine without reporting a terminal
*job* outcome. Since heartbeat stopped after inference, the server eventually
classified the job as disconnected and retried an already-failed delivery.
This wasted inference cycles and gave an inaccurate operational explanation.

The observed outcome does not establish a GPU, CUDA, network or driver failure.

## Prevention (PR #71)

1. **Timing normalization at the inference boundary.** Use the decoded audio
   frame duration already measured during preflight, not `info.duration`.
   If only a segment end exceeds the physical tail and its start remains
   valid, clip that endpoint to the decoded duration without modifying text.
   When timestamps are otherwise unrepresentable, preserve complete text as
   `timing=none`, without fabricated timestamps or SRT.
2. **Keep API validation strict.** Do not raise the server's segment tolerance,
   lower global checks, or accept a duration beyond the 2-hour limit.
3. **Terminal HTTP 422 handling.** A worker with a valid, unexpired current
   lease reports `result_schema_rejected` via authenticated
   `POST /api/worker/{jid}/reject`. The API records a failed job and a
   specific operational event, instead of waiting for the 180-second timeout.
   The rejection notice contains no transcript text or invalid payload data.
4. **Transport recovery.** Keep quarantined results protected under the
   existing retention policy. If the rejection notice cannot reach the API,
   retry with bounded backoff until acknowledged or lease expiration; never
   publish the invalid result to another job.
5. **Regression checks.** Cover 147 synthetic segments with two invalid tail
   endpoints, impossible timestamps, silence, limits, API lease fencing,
   rejecting unknown reasons and unauthorized notices, and a transient error
   during the rejection report.

## Rollout and acceptance

- Deploy the API and worker together, or update the API **before** the worker
  so that the reject route is available when the worker first sends a notice.
- Run the full test suite and security workflow, then use an authorized
  **synthetic or intentionally test-only** audio sample for E2E validation.
- Confirm a successful ASR job delivers one valid receipt and publishes its
  text; use SRT only when timestamps are sound.
- Force a deliberately invalid synthetic result and confirm a single, explicit
  `result_schema_rejected` failure, with `attempts == 1`, no artifact
  publication, no future retries, and a clear UI error.
- Record that the historical test job remains failed; no recovery is needed.
- Close #69 only after the above is verified, including the Windows/NVIDIA
  executor.

## Future diagnostics

Avoid unstructured HTTP response bodies in logs: validation errors may embed
private user-supplied text. Store only bounded error codes and safe Pydantic
field-location/type metadata if additional server-side telemetry is needed.
