// Static fail-closed regression for the former Windows coordinator publisher.
// This test never invokes PowerShell or launches a local service.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const source=fs.readFileSync('scripts/publish-stt.ps1','utf8');
const gate=source.indexOf("S7_COORDINATOR_GUARD");
const pull=source.indexOf("Updating main (fast-forward only)");
const stop=source.indexOf("Stopping application API/worker only");
assert.ok(gate>0 && pull>gate && stop>pull,'S7 role gate must precede any mutating operation');
assert.match(source, /\[switch\]\$WindowsCoordinatorConfirmed/);
assert.match(source, /if \(-not \$WindowsCoordinatorConfirmed\)/);
assert.match(source, /WINDOWS_COORDINATOR_NOT_AUTHORIZED/);
assert.match(source, /STT_HOST_API/);
assert.match(source, /docs\/QUOTA_1027_RELEASE\.md/);
console.log('Legacy Windows STT publish requires explicit coordinator role approval.');
