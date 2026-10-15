/* Timing categories for predicted stop events. Colours are set by the map. */
function classifyDelaySeconds(rawDelay, settings) {
  if (rawDelay === null || rawDelay === undefined || rawDelay === "") return "no_delay_data";
  const delay = Number(rawDelay);
  if (!Number.isFinite(delay)) return "no_delay_data";

  const earlySeconds = settings.mode === "custom" ? settings.earlyMinutes * 60 : 60;
  const lateSeconds = settings.mode === "custom" ? settings.lateMinutes * 60 : 360;
  if (delay <= -earlySeconds) return "early";
  if (delay >= lateSeconds) return "late";
  return "on_time";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { classifyDelaySeconds };
}
