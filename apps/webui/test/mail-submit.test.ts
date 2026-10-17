import assert from "node:assert/strict";
import { test } from "node:test";
import { MailSendError, RestClient } from "../src/client/rest.js";

test("mail receipt preserves failed gateway status without claiming delivery", async () => {
  const originalFetch = globalThis.fetch;
  const receipt = {
    mail_id: "mail-1", request_id: "stable", local_recipients: ["alice"],
    gateways: [{ address: "issue@github", status: "failed", error: "unavailable" }],
    message: "Alice 已入箱；网关投递失败",
  };
  globalThis.fetch = (async (_url, init) => {
    assert.equal(JSON.parse(String(init?.body)).request_id, "stable");
    return new Response(JSON.stringify(receipt), { status: 202 });
  }) as typeof fetch;
  try {
    const client = new RestClient();
    assert.deepEqual(await client.sendMail("sender", {
      request_id: "stable", to: ["alice", "issue@github"], content: "hello",
    }), receipt);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("lost response exposes original request id for safe retry", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => { throw new TypeError("connection lost"); }) as typeof fetch;
  try {
    await assert.rejects(
      new RestClient().sendMail("sender", { request_id: "stable", to: ["alice"], content: "hello" }),
      (error: unknown) => error instanceof MailSendError
        && error.reason === "submission_unknown" && error.requestId === "stable",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
