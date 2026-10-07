import assert from "node:assert/strict";
import { test } from "node:test";
import { memberAddress, selfMemberId } from "../src/lib/members";

test("selfMemberId prefixes the username with human:", () => {
  assert.equal(selfMemberId("alice"), "human:alice");
});

test("memberAddress strips the human: prefix and appends the domain", () => {
  assert.equal(memberAddress("human:alice"), "alice@tlon.internal");
});

test("memberAddress passes bare agent ids through", () => {
  assert.equal(memberAddress("gu-yanxi"), "gu-yanxi@tlon.internal");
});

test("selfMemberId and memberAddress round-trip", () => {
  assert.equal(memberAddress(selfMemberId("alice")), "alice@tlon.internal");
});
