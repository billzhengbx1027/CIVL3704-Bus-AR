const test = require("node:test");
const assert = require("node:assert/strict");
const { classifyDelaySeconds } = require("../web/static/delay_rules.js");

test("reference mode uses the first/middle-stop timing window boundaries", () => {
  const settings = { mode: "reference" };
  assert.equal(classifyDelaySeconds(-60, settings), "early");
  assert.equal(classifyDelaySeconds(-59, settings), "on_time");
  assert.equal(classifyDelaySeconds(359, settings), "on_time");
  assert.equal(classifyDelaySeconds(360, settings), "late");
  assert.equal(classifyDelaySeconds(null, settings), "no_delay_data");
});

test("custom mode changes the timing window without changing raw delay", () => {
  const settings = { mode: "custom", earlyMinutes: 1.5, lateMinutes: 3 };
  assert.equal(classifyDelaySeconds(-90, settings), "early");
  assert.equal(classifyDelaySeconds(-89, settings), "on_time");
  assert.equal(classifyDelaySeconds(179, settings), "on_time");
  assert.equal(classifyDelaySeconds(180, settings), "late");
  assert.equal(classifyDelaySeconds("", settings), "no_delay_data");
});
