// Real-source extractor for card JS unit tests.
//
// The card file touches global `customElements`/`LitElement` at load, so it can't
// be imported under node. Instead we pull the EXACT source text of the pieces
// under test out of the shipped card and eval only those — so a test can never
// drift from the shipped card (the whole point: PR-#36-class regressions stay
// guarded against the real code, not a hand-copied stub).

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const cardPath = fileURLToPath(
  new URL(
    "../../custom_components/entity_availability/frontend/entity-availability-card.js",
    import.meta.url,
  ),
);

export const cardSource = readFileSync(cardPath, "utf8");

// Extract a top-level arrow/function `const NAME = (...) => {...};` and eval it.
export function extractConst(name) {
  const re = new RegExp(`const ${name} = (\\(.*?\\n\\});`, "s");
  const m = cardSource.match(re);
  if (!m) throw new Error(`could not locate const ${name} in card source`);
  // eslint-disable-next-line no-eval
  return eval(`(${m[1]})`);
}

// Extract class methods `_name(args) { ... }` by name and assemble a stub class
// that runs the REAL method bodies (with working `this`). Brace-matched so nested
// `{}` in the body don't truncate the capture.
export function extractMethods(names) {
  const proto = {};
  for (const name of names) {
    const start = cardSource.indexOf(`\n  ${name}(`);
    if (start < 0) throw new Error(`could not locate method ${name} in card source`);
    // Find the method's opening brace, then brace-match to its close.
    const open = cardSource.indexOf("{", start);
    let depth = 0;
    let end = -1;
    for (let i = open; i < cardSource.length; i++) {
      const c = cardSource[i];
      if (c === "{") depth++;
      else if (c === "}") {
        depth--;
        if (depth === 0) {
          end = i + 1;
          break;
        }
      }
    }
    if (end < 0) throw new Error(`unbalanced braces extracting ${name}`);
    const block = cardSource.slice(start, end); // "_name(args) {...}"
    const sig = block.slice(0, block.indexOf("{")).trim(); // "_name(args)"
    const argStr = sig.slice(sig.indexOf("(") + 1, sig.lastIndexOf(")"));
    const body = block.slice(block.indexOf("{") + 1, block.lastIndexOf("}"));
    // eslint-disable-next-line no-new-func
    proto[name] = new Function(argStr, body);
  }
  class CardStub {
    constructor({ hass = null, _config = {} } = {}) {
      this.hass = hass;
      this._config = _config;
    }
  }
  Object.assign(CardStub.prototype, proto);
  return CardStub;
}
