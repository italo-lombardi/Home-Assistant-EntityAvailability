// node --test suite for _formatStateWithUnit / _formatStateDisplay / _formatIsoState.
// Runs the REAL card method bodies (extracted from the shipped source) — no stub
// copy to drift. Run: node --test tests/frontend/test_format_state_display.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { extractMethods } from "./_cardsrc.mjs";

const CardStub = extractMethods([
  "_formatIsoState",
  "_formatStateWithUnit",
  "_formatStateDisplay",
]);

test("_formatStateWithUnit — pure raw+unit", () => {
  const card = new CardStub();
  assert.equal(card._formatStateWithUnit(null), "unknown");
  assert.equal(card._formatStateWithUnit(undefined), "unknown");
  assert.equal(card._formatStateWithUnit({ state: "off", attributes: {} }), "off");
  assert.equal(
    card._formatStateWithUnit({ state: "23.5", attributes: { unit_of_measurement: "°C" } }),
    "23.5 °C",
  );
  assert.equal(card._formatStateWithUnit({ state: "on", attributes: {} }), "on");
});

test("_formatStateDisplay — null guard", () => {
  const card = new CardStub();
  assert.equal(card._formatStateDisplay(null), "unknown");
  assert.equal(card._formatStateDisplay(undefined), "unknown");
});

test("_formatStateDisplay — binary sensor translated labels", () => {
  assert.equal(
    new CardStub({ hass: { formatEntityState: () => "Clear" } })._formatStateDisplay({
      state: "off",
      attributes: {},
    }),
    "Clear (off)",
  );
  assert.equal(
    new CardStub({ hass: { formatEntityState: () => "Problem" } })._formatStateDisplay({
      state: "on",
      attributes: {},
    }),
    "Problem (on)",
  );
});

test("_formatStateDisplay — case-insensitive guard (no redundant labels)", () => {
  assert.equal(
    new CardStub({ hass: { formatEntityState: () => "Home" } })._formatStateDisplay({
      state: "home",
      attributes: {},
    }),
    "home",
  );
  assert.equal(
    new CardStub({ hass: { formatEntityState: () => "Away" } })._formatStateDisplay({
      state: "not_home",
      attributes: {},
    }),
    "Away (not_home)",
  );
});

test("_formatStateDisplay — numeric sensors bypass label path", () => {
  const card = new CardStub({ hass: { formatEntityState: () => "should not appear" } });
  assert.equal(
    card._formatStateDisplay({ state: "42", attributes: { unit_of_measurement: "%" } }),
    "42 %",
  );
  assert.equal(
    card._formatStateDisplay({ state: "23.5", attributes: { unit_of_measurement: "°C" } }),
    "23.5 °C",
  );
  assert.equal(card._formatStateDisplay({ state: "0", attributes: {} }), "0");
  assert.equal(card._formatStateDisplay({ state: "0.0", attributes: {} }), "0.0");
  assert.equal(card._formatStateDisplay({ state: "1e3", attributes: {} }), "1e3");
  assert.equal(card._formatStateDisplay({ state: "-5", attributes: {} }), "-5");
  assert.equal(card._formatStateDisplay({ state: "", attributes: {} }), "");
});

test("_formatStateDisplay — fallback safety", () => {
  assert.equal(
    new CardStub({ hass: {} })._formatStateDisplay({ state: "unavailable", attributes: {} }),
    "unavailable",
  );
  assert.equal(
    new CardStub({ hass: null })._formatStateDisplay({ state: "unavailable", attributes: {} }),
    "unavailable",
  );
  assert.equal(
    new CardStub({
      hass: { formatEntityState: () => { throw new Error("HA error"); } },
    })._formatStateDisplay({ state: "problem", attributes: {} }),
    "problem",
  );
  assert.equal(
    new CardStub({ hass: { formatEntityState: () => "" } })._formatStateDisplay({
      state: "off",
      attributes: {},
    }),
    "off",
  );
});

test("_formatStateDisplay — ISO timestamp delegates to _formatIsoState", () => {
  const year = new Date().getFullYear();
  const isoState = `${year}-06-15T14:30:00.000Z`;
  const result = new CardStub()._formatStateDisplay({ state: isoState, attributes: {} });
  assert.notEqual(result, isoState); // reformatted, not returned raw
  assert.match(result, /\bJun\b/); // month:"short" → "Jun"
  assert.match(result, /\b15\b/);
});
