import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ServerHealthBadge } from "../src/components/ServerHealthBadge";
import { RestClient } from "../src/client/rest";

test("minimal public health response renders healthy server as running", async (t) => {
  const health = { status: "healthy", version: "0.2.0" };
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify(health)));
  const received = await new RestClient().health();
  assert.deepEqual(received, health);
  const markup = renderToStaticMarkup(createElement(ServerHealthBadge, { state: { kind: "ok", health: received } }));
  assert.match(markup, /RUNNING/);
  assert.match(markup, /badge--connected/);
  assert.doesNotMatch(markup, /STOPPED/);
});
