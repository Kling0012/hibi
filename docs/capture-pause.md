# Capture pause

Saving `capture.paused=true` stops subsequent screen acquisition and prevents
unsent screen work from entering the AI client. The recording timer remains
active so it can observe a later resume. Other application features keep running.

## Acquisition and local transport

`LiveCaptureService` reads `/api/settings/general` before each acquisition and
again before posting the JPEG to `/api/live/frame`. A paused or unavailable
policy skips that step. An acquisition already started before pause can finish,
but its image is discarded if the policy is paused or its generation changed
before local upload.

The settings response includes a runtime-only `capture_generation` token, separate
from the persisted `capture` dictionary. The token changes whenever the effective
capture settings change, including pause and resume, and when the configuration
manager is recreated. Reloading unchanged settings preserves it. Electron sends
the acquisition's token with the frame; the backend rejects a stale token even
if pause and resume both occurred while the local request was in transit. The
optional token preserves compatibility with existing local frame callers.

## Queued and dequeued work

The configuration manager publishes the final merged settings under the capture
policy lock. Reloading does not temporarily publish default settings before user
settings are merged. Publication invalidates the monitor's generation and drains
both pending frames and pending exact/interval triggers. It also resets interval,
change-detection, and summary de-duplication state so recording starts afresh.

Each admitted frame and trigger carries the monitor generation. Admission,
bookkeeping, and AI-call admission use the policy lock followed by the monitor
state lock. A frame or trigger already removed from a queue is still rejected
when its generation is stale. Resuming never assigns a new generation to old
work. Fresh frames admitted after resume use the new generation.

## AI send boundary

The Live frame push, exact image generation, and interval summarizer all recheck
`capture.paused`, generation, and the existing away conditions immediately before
starting their router client call. Python 3.12's eager task start begins the client
coroutine in the same critical section as that check. The locks are released at
its first suspension and are not held while waiting for a network response.

“Unsent” means that the router client coroutine has not started. Work admitted
before pause may already have entered the client, including its internal network
queue or provider fallback. Such a call is not cancelled or described as
retractable. A local POST already started is also not retractable; the backend's
generation check prevents its old image from being admitted after resume.

## Verification scope

Run the existing mocked reproduction from the repository root:

```sh
uv run --no-project --python 3.12 --with pyyaml --with loguru python tests/repro_capture_pause.py
```

It exercises saved/reloaded pause settings, queued Live/exact/interval work,
resume, the Live call boundary, and Electron acquisition/upload. Screenshots,
provider calls, OS metadata, HTTP, and timers are mocked. It does not establish
real-device timing, actual external delivery, or full application resume behavior.
Time, weekday, and source-selection policy changes belong to Issue #4.
