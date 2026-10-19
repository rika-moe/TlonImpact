import assert from "node:assert/strict";
import { test } from "node:test";
import {
  formatAttributes,
  formatValue,
  groupMetrics,
  type MetricEntry,
  type MetricsSnapshot,
} from "../src/metrics/snapshot.js";

function counter(value: number, attributes: Record<string, string> = {}): MetricEntry {
  return { type: "counter", description: "", unit: "", points: [{ attributes, value }] };
}

const SAMPLE: MetricsSnapshot = {
  metrics: {
    "tlon.mail.sent": counter(3, { agent: "gu-yanxi" }),
    "tlon.mail.delivered": {
      type: "counter",
      description: "",
      unit: "",
      points: [
        { attributes: { kind: "agent" }, value: 2 },
        { attributes: { kind: "human" }, value: 1 },
      ],
    },
    "tlon.agent.started": counter(1, { agent_id: "gu-yanxi" }),
    "tlon.llm.calls": counter(2, { agent_id: "gu-yanxi" }),
    "tlon.llm.duration_ms": {
      type: "histogram",
      description: "",
      unit: "ms",
      points: [{ attributes: { agent_id: "gu-yanxi" }, count: 2, sum: 200, min: 80, max: 120 }],
    },
    "tlon.tool.executions": counter(5, { tool_name: "mail-send" }),
  },
};

test("formatAttributes renders sorted key=value pairs", () => {
  assert.equal(formatAttributes({ b: "2", a: "1" }), "a=1 b=2");
  assert.equal(formatAttributes({}), "-");
});

test("formatValue renders counters plainly and histograms as stats", () => {
  assert.equal(formatValue(counter(7), { attributes: {}, value: 7 }), "7");
  const histogram = (SAMPLE.metrics as Record<string, MetricEntry>)["tlon.llm.duration_ms"];
  assert.equal(
    formatValue(histogram, histogram.points[0]!),
    "count=2 avg=100ms min=80 max=120",
  );
});

test("groupMetrics buckets metrics into the four families", () => {
  const groups = groupMetrics(SAMPLE);
  assert.deepEqual(
    groups.map((g) => g.name),
    ["mail", "agent", "llm", "tool"],
  );

  const mail = groups[0]!;
  assert.deepEqual(
    mail.rows.map((r) => `${r.metric} ${r.attributes} ${r.value}`),
    [
      "delivered kind=agent 2",
      "delivered kind=human 1",
      "sent agent=gu-yanxi 3",
    ],
  );

  const llm = groups[2]!;
  assert.deepEqual(
    llm.rows.map((r) => `${r.metric} ${r.attributes} ${r.value}`),
    ["calls agent_id=gu-yanxi 2", "duration_ms agent_id=gu-yanxi count=2 avg=100ms min=80 max=120"],
  );
});

test("groupMetrics keeps empty families and drops unknown prefixes", () => {
  const groups = groupMetrics({ metrics: { "other.thing": counter(1) } });
  assert.equal(groups.length, 4);
  assert.ok(groups.every((g) => g.rows.length === 0));

  const empty = groupMetrics({});
  assert.deepEqual(
    empty.map((g) => g.name),
    ["mail", "agent", "llm", "tool"],
  );
});
