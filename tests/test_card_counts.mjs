// Dependency-free check of the card's collapsed-row count math (_collapsedCounts).
// The card file can't be imported under node (it touches global customElements at
// load), so we extract the pure helper's source text and eval just that — the test
// runs the REAL source, so it can't drift from the shipped card.
//
// Run: node tests/test_card_counts.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const cardPath = fileURLToPath(
  new URL(
    "../custom_components/entity_availability/frontend/entity-availability-card.js",
    import.meta.url,
  ),
);
const src = readFileSync(cardPath, "utf8");

const m = src.match(/const _collapsedCounts = (\(.*?\n\});/s);
assert.ok(m, "could not locate _collapsedCounts in the card source");
// eslint-disable-next-line no-eval
const _collapsedCounts = eval(`(${m[1]})`);

// A device with one offline member: 1 offline, rest online.
assert.deepEqual(_collapsedCounts(3, 1), { offlineCount: 1, onlineCount: 2 });
// Floor: offlineMemberCount 0/undefined still reports 1 offline (row IS offline).
assert.deepEqual(_collapsedCounts(3, 0), { offlineCount: 1, onlineCount: 2 });
assert.deepEqual(_collapsedCounts(3, undefined), { offlineCount: 1, onlineCount: 2 });
// Single-member row: 1 offline, 0 online -> no online suffix upstream.
assert.deepEqual(_collapsedCounts(1, 1), { offlineCount: 1, onlineCount: 0 });
// All members offline: online clamps at 0, never negative.
assert.deepEqual(_collapsedCounts(2, 2), { offlineCount: 2, onlineCount: 0 });
// Undercount ceiling (ponytail: backend offline list is rep-collapsed): even if a
// device has 2 offline members, offlineMemberCount arrives as 1 and online = rest.
assert.deepEqual(_collapsedCounts(4, 1), { offlineCount: 1, onlineCount: 3 });

console.log("test_card_counts: 6 assertions passed");
