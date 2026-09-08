// Smoke tests for _formatStateWithUnit and _formatStateDisplay
// Run with: node tests/frontend/test_format_state_with_unit.js

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
// Stub — exact copy of the two methods from the card.
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
    const raw = this._formatStateWithUnit(entityState);
    if (!entityState) return raw;
    const isNumeric = !isNaN(parseFloat(entityState.state)) && isFinite(entityState.state);
    if (isNumeric) return raw;
    let label;
    try { label = this.hass?.formatEntityState?.(entityState); } catch (_) { return raw; }
    if (!label) return raw;
    if (label.toLowerCase() === entityState.state.toLowerCase()) return raw;
    return `${label} · ${raw}`;
  }
}

// ---------------------------------------------------------------------------
// _formatStateWithUnit — pure raw+unit formatter
// ---------------------------------------------------------------------------
console.log("\n_formatStateWithUnit");

{
  const card = new CardStub();
  assert("null → unknown", card._formatStateWithUnit(null), "unknown");
  assert("undefined → unknown", card._formatStateWithUnit(undefined), "unknown");
}
{
  const card = new CardStub();
  assert("raw string state", card._formatStateWithUnit({ state: "off", attributes: {} }), "off");
  assert("raw string + unit", card._formatStateWithUnit({ state: "23.5", attributes: { unit_of_measurement: "°C" } }), "23.5 °C");
  assert("no unit", card._formatStateWithUnit({ state: "on", attributes: {} }), "on");
}

// ---------------------------------------------------------------------------
// _formatStateDisplay — label logic
// ---------------------------------------------------------------------------
console.log("\n_formatStateDisplay — binary sensor (problem class)");
{
  const es = { state: "off", attributes: {} };
  const card = new CardStub({ hass: { formatEntityState: () => "Clear" } });
  assert("off → Clear · off", card._formatStateDisplay(es), "Clear · off");
}
{
  const es = { state: "on", attributes: {} };
  const card = new CardStub({ hass: { formatEntityState: () => "Problem" } });
  assert("on → Problem · on", card._formatStateDisplay(es), "Problem · on");
}

console.log("\n_formatStateDisplay — case-insensitive guard (no redundant labels)");
{
  // "Home" vs "home" — same when lowercased → raw only
  const es = { state: "home", attributes: {} };
  const card = new CardStub({ hass: { formatEntityState: () => "Home" } });
  assert("Home/home → raw only (no redundancy)", card._formatStateDisplay(es), "home");
}
{
  // "Away" vs "not_home" — genuinely different → show label
  const es = { state: "not_home", attributes: {} };
  const card = new CardStub({ hass: { formatEntityState: () => "Away" } });
  assert("Away · not_home", card._formatStateDisplay(es), "Away · not_home");
}

console.log("\n_formatStateDisplay — numeric sensors skip label");
{
  const es = { state: "42", attributes: { unit_of_measurement: "%" } };
  const card = new CardStub({ hass: { formatEntityState: () => "42 %" } });
  assert("numeric → raw+unit only, no label", card._formatStateDisplay(es), "42 %");
}
{
  const es = { state: "23.5", attributes: { unit_of_measurement: "°C" } };
  const card = new CardStub({ hass: { formatEntityState: () => "23.5 °C" } });
  assert("float sensor → raw+unit only", card._formatStateDisplay(es), "23.5 °C");
}

console.log("\n_formatStateDisplay — fallback safety");
{
  const es = { state: "unavailable", attributes: {} };
  const card = new CardStub({ hass: {} });
  assert("no formatEntityState fn → raw", card._formatStateDisplay(es), "unavailable");
}
{
  const es = { state: "unavailable", attributes: {} };
  const card = new CardStub({ hass: null });
  assert("null hass → raw", card._formatStateDisplay(es), "unavailable");
}
{
  const es = { state: "problem", attributes: {} };
  const card = new CardStub({ hass: { formatEntityState: () => { throw new Error("HA error"); } } });
  assert("formatEntityState throws → raw fallback", card._formatStateDisplay(es), "problem");
}
{
  const es = { state: "off", attributes: {} };
  const card = new CardStub({ hass: { formatEntityState: () => "" } });
  assert("empty label → raw", card._formatStateDisplay(es), "off");
}

console.log("\n_formatStateDisplay — ISO timestamp (delegates to _formatIsoState)");
{
  const year = new Date().getFullYear();
  const isoState = `${year}-06-15T14:30:00.000Z`;
  const es = { state: isoState, attributes: {} };
  const card = new CardStub();
  const result = card._formatStateDisplay(es);
  assert("ISO reformatted (not raw)", result !== isoState, true);
  assert("ISO result is non-empty string", typeof result === "string" && result.length > 0, true);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
console.log(`\n${"─".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
