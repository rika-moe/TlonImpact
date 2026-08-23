import assert from "node:assert/strict";
import { test } from "node:test";
import type { SessionInfo } from "../src/client/rest";
import { formatConnectedAt, formatFilters, sortSessions } from "../src/lib/sessions";

function session(overrides: Partial<SessionInfo>): SessionInfo {
  return {
    id: "webui-0001",
    client_type: "webui",
    connected_at: "2026-08-23T10:00:00",
    mode: "chat",
    filters: {},
    ...overrides,
  };
}

test("formatConnectedAt shows time only for sessions connected today", () => {
  const now = new Date("2026-08-23T12:00:00");
  assert.equal(formatConnectedAt("2026-08-23T10:05:07", now), "10:05:07");
});

test("formatConnectedAt prefixes the date for older sessions", () => {
  const now = new Date("2026-08-23T12:00:00");
  assert.equal(formatConnectedAt("2026-08-21T09:01:02", now), "08-21 09:01:02");
});

test("formatConnectedAt echoes unparseable input", () => {
  assert.equal(formatConnectedAt("not-a-date"), "not-a-date");
  assert.equal(formatConnectedAt(""), "??:??:??");
});

test("formatFilters renders empty filters as a dash, otherwise JSON", () => {
  assert.equal(formatFilters({}), "—");
  assert.equal(formatFilters({ event_types: ["mail.delivered"] }), '{"event_types":["mail.delivered"]}');
});

test("sortSessions orders newest-connected first and does not mutate", () => {
  const older = session({ id: "tui-0001", connected_at: "2026-08-23T09:00:00" });
  const newer = session({ id: "webui-0002", connected_at: "2026-08-23T11:00:00" });
  const input = [older, newer];
  assert.deepEqual(
    sortSessions(input).map((s) => s.id),
    ["webui-0002", "tui-0001"],
  );
  assert.equal(input[0]?.id, "tui-0001");
});
