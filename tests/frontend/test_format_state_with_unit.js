// Smoke tests for _formatStateWithUnit
// Run with: node tests/frontend/test_format_state_with_unit.js
// No framework, no imports — plain JS assertions.

let passed = 0;
let failed = 0;

function assert(desc, actual, expected) {
  if (actual === expected) {
    console.log(`  PASS  ${desc}`);
    passed++;
  } else {
    console.error(`  FAIL  ${desc}`);
    console.error(`        expected: ${JSON.stringify(expected)}`);
    console.error(`        actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

// ---------------------------------------------------------------------------
// Minimal stub of the card — only the two methods under test.
// ---------------------------------------------------------------------------
class CardStub {
  constructor({ hass = null } = {}) {
    this.hass = hass;
  }

  _formatIsoState(stateValue) {
    if (!/^\d{4}-\d{2}-\d{2}T/.test(stateValue)) return stateValue;
    const date = new Date(stateValue);
    if (isNaN(date.getTime())) return stateValue;
    const sameYear = date.getFullYear() === new Date().getFullYear();
    return date.toLocaleString(undefined, {
      month: "short", day: "numeric",
      ...(sameYear ? {} : { year: "numeric" }),
      hour: "2-digit", minute: "2-digit",
    });
  }

  _formatStateWithUnit(entityState) {
    if (!entityState) return "unknown";
    const raw = this._formatIsoState(entityState.state);
    const unit = entityState.attributes?.unit_of_measurement;
    const label = this.hass?.formatEntityState?.(entityState);
    const display = label && label !== entityState.state ? `${label} (${raw})` : raw;
    return unit ? `${display} ${unit}` : display;
  }
}

// ---------------------------------------------------------------------------
// Test 1: null entityState → "unknown"
// ---------------------------------------------------------------------------
console.log("\n1. null/undefined entityState → \"unknown\"");
{
  const card = new CardStub();
  assert("null returns unknown", card._formatStateWithUnit(null), "unknown");
  assert("undefined returns unknown", card._formatStateWithUnit(undefined), "unknown");
}

// ---------------------------------------------------------------------------
// Test 2: formatEntityState returns label different from raw state
//         → "Label (raw)"
// ---------------------------------------------------------------------------
console.log("\n2. formatEntityState returns different label → \"Label (raw)\"");
{
  const entityState = { state: "on", attributes: {} };
  const card = new CardStub({
    hass: { formatEntityState: () => "On" },
  });
  assert("on → On (on)", card._formatStateWithUnit(entityState), "On (on)");
}

// ---------------------------------------------------------------------------
// Test 3: formatEntityState returns same string as raw state → raw only
// ---------------------------------------------------------------------------
console.log("\n3. formatEntityState returns same as raw → no redundant parens");
{
  const entityState = { state: "off", attributes: {} };
  const card = new CardStub({
    hass: { formatEntityState: () => "off" },
  });
  assert("off → off (no parens)", card._formatStateWithUnit(entityState), "off");
}

// ---------------------------------------------------------------------------
// Test 4: hass.formatEntityState is undefined → falls back to raw
// ---------------------------------------------------------------------------
console.log("\n4. hass.formatEntityState missing → fallback to raw");
{
  const entityState = { state: "unavailable", attributes: {} };
  // hass present but formatEntityState not a function
  const card = new CardStub({ hass: {} });
  assert("no formatEntityState fn → raw", card._formatStateWithUnit(entityState), "unavailable");
}

// ---------------------------------------------------------------------------
// Test 5: numeric state where label === raw → raw only (no redundancy)
// ---------------------------------------------------------------------------
console.log("\n5. numeric state, label === raw → raw only");
{
  const entityState = { state: "23.5", attributes: {} };
  const card = new CardStub({
    hass: { formatEntityState: () => "23.5" },
  });
  assert("23.5 → 23.5", card._formatStateWithUnit(entityState), "23.5");
}

// ---------------------------------------------------------------------------
// Test 6: unit_of_measurement present → unit appended
// ---------------------------------------------------------------------------
console.log("\n6. unit_of_measurement → unit appended");
{
  const entityState = { state: "23.5", attributes: { unit_of_measurement: "°C" } };

  // 6a: no formatEntityState (no hass)
  const card = new CardStub();
  assert("23.5 °C (no label fn)", card._formatStateWithUnit(entityState), "23.5 °C");

  // 6b: label different from state
  const card2 = new CardStub({ hass: { formatEntityState: () => "23.5 °C" } });
  // label "23.5 °C" !== state "23.5", so display = "23.5 °C (23.5)", then unit appended
  assert("label≠state with unit", card2._formatStateWithUnit(entityState), "23.5 °C (23.5) °C");

  // 6c: label equals state → raw = "23.5", display = "23.5", result = "23.5 °C"
  const card3 = new CardStub({ hass: { formatEntityState: () => "23.5" } });
  assert("label===state with unit", card3._formatStateWithUnit(entityState), "23.5 °C");
}

// ---------------------------------------------------------------------------
// Test 7: ISO timestamp state → delegates to _formatIsoState (formatted date)
// ---------------------------------------------------------------------------
console.log("\n7. ISO timestamp state → formatted via _formatIsoState");
{
  // Use a fixed ISO string in the current year so sameYear=true branch fires.
  const year = new Date().getFullYear();
  const isoState = `${year}-06-15T14:30:00.000Z`;
  const entityState = { state: isoState, attributes: {} };
  const card = new CardStub();
  const result = card._formatStateWithUnit(entityState);

  // Result must NOT be the raw ISO string — _formatIsoState reformatted it.
  assert("ISO string is reformatted (not raw)", result !== isoState, true);
  // Result must contain "Jun" or locale-equivalent short month name
  // (toLocaleString with month:"short" on most platforms → "Jun").
  // We test the shape rather than the exact locale output.
  assert("result is a non-empty string", typeof result === "string" && result.length > 0, true);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
console.log(`\n${"─".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
