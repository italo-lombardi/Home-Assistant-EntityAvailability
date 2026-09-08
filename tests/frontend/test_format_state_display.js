// Smoke tests for _formatStateWithUnit and _formatStateDisplay
// Run with: node tests/frontend/test_format_state_display.js

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
// Stub — exact copy of both methods from the card.
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
    return unit ? `${raw} ${unit}` : raw;
  }

  _formatStateDisplay(entityState) {
    if (!entityState) return "unknown";
    const raw = this._formatStateWithUnit(entityState);
    if (isFinite(parseFloat(entityState.state))) return raw;
    let label;
    try { label = this.hass?.formatEntityState?.(entityState); } catch (_) { return raw; }
    if (!label) return raw;
    if (label.toLowerCase() === entityState.state.toLowerCase()) return raw;
    return `${label} · ${raw}`;
  }
}

// ---------------------------------------------------------------------------
// _formatStateWithUnit — pure raw+unit
// ---------------------------------------------------------------------------
console.log("\n_formatStateWithUnit");
{
  const card = new CardStub();
  assert("null → unknown", card._formatStateWithUnit(null), "unknown");
  assert("undefined → unknown", card._formatStateWithUnit(undefined), "unknown");
  assert("raw string state", card._formatStateWithUnit({ state: "off", attributes: {} }), "off");
  assert("raw string + unit", card._formatStateWithUnit({ state: "23.5", attributes: { unit_of_measurement: "°C" } }), "23.5 °C");
  assert("no unit", card._formatStateWithUnit({ state: "on", attributes: {} }), "on");
}

// ---------------------------------------------------------------------------
// _formatStateDisplay — null guard (first line, before any other logic)
// ---------------------------------------------------------------------------
console.log("\n_formatStateDisplay — null guard");
{
  const card = new CardStub();
  assert("null → unknown", card._formatStateDisplay(null), "unknown");
  assert("undefined → unknown", card._formatStateDisplay(undefined), "unknown");
}

// ---------------------------------------------------------------------------
// _formatStateDisplay — binary sensor translated labels
// ---------------------------------------------------------------------------
console.log("\n_formatStateDisplay — binary sensor (problem class)");
{
  const card = new CardStub({ hass: { formatEntityState: () => "Clear" } });
  assert("off → Clear · off", card._formatStateDisplay({ state: "off", attributes: {} }), "Clear · off");
}
{
  const card = new CardStub({ hass: { formatEntityState: () => "Problem" } });
  assert("on → Problem · on", card._formatStateDisplay({ state: "on", attributes: {} }), "Problem · on");
}

// ---------------------------------------------------------------------------
// _formatStateDisplay — case-insensitive guard
// ---------------------------------------------------------------------------
console.log("\n_formatStateDisplay — case-insensitive guard (no redundant labels)");
{
  const card = new CardStub({ hass: { formatEntityState: () => "Home" } });
  assert("Home/home → raw only", card._formatStateDisplay({ state: "home", attributes: {} }), "home");
}
{
  const card = new CardStub({ hass: { formatEntityState: () => "Away" } });
  assert("Away · not_home (genuinely different)", card._formatStateDisplay({ state: "not_home", attributes: {} }), "Away · not_home");
}

// ---------------------------------------------------------------------------
// _formatStateDisplay — numeric sensors bypass label path
// ---------------------------------------------------------------------------
console.log("\n_formatStateDisplay — numeric bypass (isFinite(parseFloat(state)))");
{
  const fakeLabel = () => "should not appear";
  const card = new CardStub({ hass: { formatEntityState: fakeLabel } });
  assert("integer",   card._formatStateDisplay({ state: "42",   attributes: { unit_of_measurement: "%" } }), "42 %");
  assert("float",     card._formatStateDisplay({ state: "23.5", attributes: { unit_of_measurement: "°C" } }), "23.5 °C");
  assert("zero",      card._formatStateDisplay({ state: "0",    attributes: {} }), "0");
  assert("zero float",card._formatStateDisplay({ state: "0.0",  attributes: {} }), "0.0");
  assert("scientific",card._formatStateDisplay({ state: "1e3",  attributes: {} }), "1e3");
  assert("negative",  card._formatStateDisplay({ state: "-5",   attributes: {} }), "-5");
}

// ---------------------------------------------------------------------------
// _formatStateDisplay — fallback safety
// ---------------------------------------------------------------------------
console.log("\n_formatStateDisplay — fallback safety");
{
  const card = new CardStub({ hass: {} });
  assert("no formatEntityState fn → raw", card._formatStateDisplay({ state: "unavailable", attributes: {} }), "unavailable");
}
{
  const card = new CardStub({ hass: null });
  assert("null hass → raw", card._formatStateDisplay({ state: "unavailable", attributes: {} }), "unavailable");
}
{
  const card = new CardStub({ hass: { formatEntityState: () => { throw new Error("HA error"); } } });
  assert("formatEntityState throws → raw fallback", card._formatStateDisplay({ state: "problem", attributes: {} }), "problem");
}
{
  const card = new CardStub({ hass: { formatEntityState: () => "" } });
  assert("empty label → raw", card._formatStateDisplay({ state: "off", attributes: {} }), "off");
}

// ---------------------------------------------------------------------------
// _formatStateDisplay — ISO timestamp delegates to _formatIsoState
// ---------------------------------------------------------------------------
console.log("\n_formatStateDisplay — ISO timestamp");
{
  const year = new Date().getFullYear();
  const isoState = `${year}-06-15T14:30:00.000Z`;
  const card = new CardStub();
  const result = card._formatStateDisplay({ state: isoState, attributes: {} });
  assert("ISO is reformatted (not returned raw)", result !== isoState, true);
  // toLocaleString with month:"short" produces something like "Jun 15, 2:30 PM"
  assert("contains short month name (Jun)", /\bJun\b/.test(result), true);
  assert("contains day 15", /\b15\b/.test(result), true);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
console.log(`\n${"─".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
