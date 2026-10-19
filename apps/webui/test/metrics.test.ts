import assert from "node:assert/strict";
import { test } from "node:test";
import {
  formatAttributes,
  formatValue,
  groupMetrics,
  summarizeLlmUsage,
  type MetricEntry,
  type MetricsSnapshot,
} from "../src/lib/snapshot";

function counter(value: number, attributes: Record<string, string> = {}): MetricEntry {
  return { type: "counter", description: "", unit: "", points: [{ attributes, value }] };
}

const SAMPLE: MetricsSnapshot = {
  metrics: {
    "tlon.mail.sent": {
      type: "counter",
      description: "Mails sent",
      unit: "mail",
      points: [{ attributes: { agent: "gu-yanxi" }, value: 3 }],
    },
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

  // Rows carry the entry's description and unit for display.
  assert.deepEqual(
    mail.rows.map((r) => `${r.description}|${r.unit}`),
    ["|", "|", "Mails sent|mail"],
  );

  const llm = groups[2]!;
  assert.deepEqual(
    llm.rows.map((r) => `${r.metric} ${r.attributes} ${r.value}`),
    ["calls agent_id=gu-yanxi 2", "duration_ms agent_id=gu-yanxi count=2 avg=100ms min=80 max=120"],
  );
});

test("groupMetrics keeps empty families and buckets unknown prefixes into other", () => {
  const groups = groupMetrics({ metrics: { "other.thing": counter(1) } });
  assert.deepEqual(
    groups.map((g) => g.name),
    ["mail", "agent", "llm", "tool", "other"],
  );

  const other = groups[4]!;
  assert.deepEqual(
    other.rows.map((r) => `${r.metric} ${r.attributes} ${r.value}`),
    ["other.thing - 1"],
  );

  // The other bucket is omitted entirely when nothing falls into it.
  const empty = groupMetrics({});
  assert.deepEqual(
    empty.map((g) => g.name),
    ["mail", "agent", "llm", "tool"],
  );
});

const TOKEN_USAGE: MetricsSnapshot = {
  metrics: {
    "tlon.llm.prompt_tokens": {
      type: "counter",
      description: "",
      unit: "",
      points: [
        { attributes: { agent_id: "gu-yanxi", model: "deepseek-chat" }, value: 1000 },
        { attributes: { agent_id: "xie-yunshu", model: "deepseek-chat" }, value: 3000 },
      ],
    },
    "tlon.llm.completion_tokens": {
      type: "counter",
      description: "",
      unit: "",
      points: [
        { attributes: { agent_id: "gu-yanxi", model: "deepseek-chat" }, value: 200 },
        { attributes: { agent_id: "xie-yunshu", model: "deepseek-chat" }, value: 500 },
      ],
    },
    "tlon.llm.cached_tokens": {
      type: "counter",
      description: "",
      unit: "",
      points: [{ attributes: { agent_id: "xie-yunshu", model: "deepseek-chat" }, value: 2100 }],
    },
    // gu-yanxi has no cached/cache-write points: they count as zero.
    "tlon.llm.cache_write_tokens": {
      type: "counter",
      description: "",
      unit: "",
      points: [
        { attributes: { agent_id: "xie-yunshu", model: "deepseek-chat" }, value: 150 },
      ],
    },
  },
};

test("summarizeLlmUsage aggregates per agent/model, sorted by prompt desc", () => {
  const summary = summarizeLlmUsage(TOKEN_USAGE);

  assert.equal(summary.rows.length, 2);
  const [first, second] = summary.rows;
  assert.deepEqual(
    [first!.agentId, first!.promptTokens, first!.cacheWriteTokens],
    ["xie-yunshu", 3000, 150],
  );
  assert.deepEqual(
    [second!.agentId, second!.promptTokens, second!.cachedTokens, second!.cacheWriteTokens],
    ["gu-yanxi", 1000, 0, 0],
  );
  assert.ok(Math.abs(first!.cacheHitRate! - 0.7) < 1e-9);
  assert.equal(second!.cacheHitRate, 0);

  assert.deepEqual(summary.total, {
    agentId: "all",
    model: "all",
    promptTokens: 4000,
    completionTokens: 700,
    cachedTokens: 2100,
    cacheWriteTokens: 150,
    cacheHitRate: 2100 / 4000,
  });
});

test("summarizeLlmUsage reports null hit rate when prompt is zero", () => {
  const summary = summarizeLlmUsage({
    metrics: {
      "tlon.llm.cached_tokens": counter(50, { agent_id: "a", model: "m" }),
    },
  });
  assert.equal(summary.rows.length, 1);
  assert.equal(summary.rows[0]!.cacheHitRate, null);
  assert.equal(summary.total.cacheHitRate, null);
});

test("summarizeLlmUsage returns empty rows on an empty snapshot", () => {
  const summary = summarizeLlmUsage({});
  assert.deepEqual(summary.rows, []);
  assert.equal(summary.total.promptTokens, 0);
  assert.equal(summary.total.cacheHitRate, null);
});
