# Cloudflare STT staging: independent Worker and preserved TTS legacy service

**NO-GO for production without #19 and operator-only #28.** This file does not authorize deployment or change the legacy Cloudflare resources. Confirmed Cloudflare [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/) provision TLS and a DNS record on deploy. A custom domain may conflict with an existing DNS record; do not delete another service's DNS to make a rename pass.

## Non-destructive design

- Legacy: `wrangler.jsonc` → `faysk-tts`, custom domain `tts.faysk.dev`, origin `tts-origin.faysk.dev`. **Preserve**, do not rename.
- New: `wrangler.stt.jsonc` → separate Worker `faysk-stt`, custom domain `stt.faysk.dev`. No second Tunnel or origin is required initially: `ORIGIN` deliberately points to existing `tts-origin.faysk.dev` **only after the operator verifies it is a suitable private API origin and can serve both Workers with authenticated headers**.
- Both Workers require their own **`EDGE_KEY` binding value identical to the backend's active edge secret**, but **never** commit, paste or log it. Rotating the existing backend secret before both workers are ready will cause 403.
- Existing `scripts/cloudflare.py` is a **legacy mutating provisioner**; don't run it for this migration. Do not change an existing Tunnel ingress during this stage.
- The new Worker uses the same `edge/worker.js` code. Frontend assets on the new Worker will have STT branding and canonical `stt.faysk.dev`; legacy Worker retains its original assets and UI for valid legacy sessions.
- New site must still link to `github.com/Faysk/tts` until issue #18 admin rename. Do not publish links to a nonexistent `Faysk/stt` repository.

## Operator checklist

1. Confirm DNS & Worker Custom Domain conflicts, Cloudflare zone ownership, access restrictions, origin auth and `EDGE_KEY` binding (presence only).
2. Use `npx wrangler deploy --config wrangler.stt.jsonc --dry-run` as an offline build validation. A dry-run cannot confirm the origin or SSL.
3. Provision new Worker via authorized Cloudflare administration, set `EDGE_KEY` privately and confirm its presence using `wrangler secret list --config wrangler.stt.jsonc`. **Warning:** `wrangler secret put` itself deploys a version; provision this new Worker in a controlled window and never target the legacy Worker.
4. Inspect `npx wrangler deploy --config wrangler.stt.jsonc` output and verify target is `faysk-stt`/`stt.faysk.dev` before approving release. This is **not run by any CI**.
5. Probe `https://stt.faysk.dev/api/health`, denied unauthorized direct origin, upload/status/download and TLS. Check `tts.faysk.dev` before/after to ensure existing sessions still work.
6. Run the two-host browser tests #28, plus #19 rollback/restore. **Do not put a blanket redirect on GET/POST /api/**, and do not move `localStorage` secrets across origins.
7. With explicit GO, keep legacy host available through the full 24h retention interval **plus operational margin** before any optional navigation redirects.

## Important warning

`https://tts-origin.faysk.dev` is an internal implementation name, not necessarily a public site. Cloudflare may route differently for proxied origins; must validate **real** bindings and HTTP behavior with authorized access.

The code-only staging workflow intentionally has **no** deploy action or token handling. Every infrastructure mutation is a manual gate in [#28](https://github.com/Faysk/tts/issues/28).
