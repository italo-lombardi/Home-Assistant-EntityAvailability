// node --test suite for the card's collapsed-row count math (_collapsedCounts).
// Runs the REAL helper source (extracted from the shipped card) — can't drift.
// Run: node --test tests/test_card_counts.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { extractConst } from "./frontend/_cardsrc.mjs";

const _collapsedCounts = extractConst("_collapsedCounts");

test("_collapsedCounts", () => {
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
});
