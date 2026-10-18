import assert from "node:assert/strict";
import { test } from "node:test";
import type { ParticipantInfo } from "../src/types";
import { commitRecipients, searchRecipients } from "../src/lib/recipients";
import type { RecipientField } from "../src/lib/recipients";

const participants: ParticipantInfo[] = [
  { id: "ada", name: "Ada Lovelace" },
  { id: "human:Alan", name: "Alan Turing", type: "human" },
  { id: "Grace", name: "Grace Hopper" },
  { id: "张三", name: "张三" },
];

function draft(text: string): RecipientField {
  return { recipients: [], draft: text };
}

test("search finds surnames, mixed case, IDs, and full addresses", () => {
  for (const query of ["lovelace", "  ADA  ", "ADA@TLON.INTERNAL"]) {
    assert.deepEqual(searchRecipients(participants, query, []).map((entry) => entry.id), ["ada"]);
  }
  assert.equal(searchRecipients(participants, "human:alan", [])[0].address, "Alan@tlon.internal");
  assert.equal(searchRecipients(participants, "张", [])[0].label, "张三");
});

test("search returns all results and excludes selected canonical addresses and aliases", () => {
  const directory = Array.from({ length: 12 }, (_, index): ParticipantInfo => ({ id: `person-${index}` }));
  assert.equal(searchRecipients(directory, "", []).length, 12);
  assert.equal(searchRecipients(directory, "person", [
    { address: "PERSON-1@TLON.INTERNAL", label: "One" },
    { address: "person-2", label: "Two" },
  ]).length, 10);
});

test("prefix results rank before substring results with stable order within each group", () => {
  const directory = [
    { id: "one", name: "Mary Ann" },
    { id: "two", name: "Ann Smith" },
    { id: "three", name: "Ann Jones" },
    { id: "four", name: "Joanne" },
  ];
  assert.deepEqual(searchRecipients(directory, "ann", []).map((entry) => entry.id), ["two", "three", "one", "four"]);
});

test("commit resolves names, IDs, and addresses to stable addresses with original case", () => {
  const result = commitRecipients(draft("ada lovelace; HUMAN:alan; grace@TLON.INTERNAL，张三"), participants);
  assert.equal(result.error, undefined);
  assert.deepEqual(result.field, { recipients: [
    { address: "ada@tlon.internal", label: "Ada Lovelace" },
    { address: "Alan@tlon.internal", label: "Alan Turing" },
    { address: "Grace@tlon.internal", label: "Grace Hopper" },
    { address: "张三@tlon.internal", label: "张三" },
  ], draft: "" });
});

test("commit supports all separators, deduplicates aliases, and preserves existing selections", () => {
  const existing = { address: "ADA@tlon.internal", label: "Existing label" };
  const result = commitRecipients({ recipients: [existing], draft: "Ada Lovelace,ada；ADA@tlon.internal\nGrace\r\n张三;Grace" }, participants);
  assert.equal(result.error, undefined);
  assert.equal(result.field.recipients.length, 3);
  assert.equal(result.field.recipients[0], existing);
  assert.equal(result.field.draft, "");
});

test("commit normalizes a selected known alias for duplicate detection", () => {
  const existing = { address: "human:Alan", label: "Alan" };
  assert.deepEqual(commitRecipients({ recipients: [existing], draft: "Alan@TLON.INTERNAL" }, participants).field.recipients, [existing]);
});

test("ambiguous names are rejected atomically, while exact unique addresses remain selectable", () => {
  const directory = [{ id: "sam-a", name: "Sam" }, { id: "sam-b", name: "SAM" }];
  const field = draft("sam-a;sam");
  const result = commitRecipients(field, directory);
  assert.equal(result.field, field);
  assert.match(result.error ?? "", /Multiple recipients/);
  assert.equal(commitRecipients(draft("sam-b@tlon.internal"), directory).field.recipients[0].address, "sam-b@tlon.internal");
});

test("duplicate directory records for one address are not ambiguous", () => {
  const directory = [{ id: "sam", name: "Sam" }, { id: "sam", name: "Sam" }];
  assert.equal(commitRecipients(draft("Sam"), directory).error, undefined);
  assert.equal(searchRecipients(directory, "sam", []).length, 1);
});

test("unknown partials are rejected without losing draft or selected recipients", () => {
  const field = { recipients: [{ address: "chosen@elsewhere", label: "Chosen" }], draft: "Ada Lovelace;Al" };
  const result = commitRecipients(field, participants);
  assert.equal(result.field, field);
  assert.match(result.error ?? "", /Choose a recipient or enter a complete address/);
  for (const token of ["@domain", "person@", "person @domain", "a@b@c"]) {
    assert.ok(commitRecipients(draft(token), participants).error);
  }
});

test("manual addresses preserve text and support internal-like external domains", () => {
  const result = commitRecipients(draft("Issue@GitHub;ISSUE@github;User@Example.COM"), participants);
  assert.deepEqual(result.field.recipients, [
    { address: "Issue@GitHub", label: "Issue@GitHub" },
    { address: "ISSUE@github", label: "ISSUE@github" },
    { address: "User@Example.COM", label: "User@Example.COM" },
  ]);
});

test("case-distinct member identities remain independently selectable and deduplicated", () => {
  const directory = [{ id: "Sam", name: "First Sam" }, { id: "sam", name: "Second Sam" }];
  assert.deepEqual(searchRecipients(directory, "sam", []).map((entry) => entry.address), ["Sam@tlon.internal", "sam@tlon.internal"]);
  assert.deepEqual(searchRecipients(directory, "sam", [{ address: "Sam@tlon.internal", label: "First" }]).map((entry) => entry.address), ["sam@tlon.internal"]);
  const result = commitRecipients(draft("Sam;sam;Sam@tlon.internal;sam@tlon.internal"), directory);
  assert.equal(result.error, undefined);
  assert.deepEqual(result.field.recipients.map((entry) => entry.address), ["Sam@tlon.internal", "sam@tlon.internal"]);
  assert.match(commitRecipients(draft("SAM"), directory).error ?? "", /Multiple recipients/);
  assert.match(commitRecipients(draft("SAM@tlon.internal"), directory).error ?? "", /Multiple recipients/);
});

test("exact IDs and canonical addresses outrank conflicting display names", () => {
  const directory = [
    { id: "intruder", name: "ada" },
    { id: "intruder-two", name: "ada@tlon.internal" },
    { id: "ada", name: "Ada Lovelace" },
  ];
  const result = commitRecipients(draft("ada;ada@tlon.internal"), directory);
  assert.equal(result.error, undefined);
  assert.deepEqual(result.field.recipients, [{ address: "ada@tlon.internal", label: "Ada Lovelace" }]);
});

test("complete external addresses cannot be redirected by display names or IDs", () => {
  const directory = [
    { id: "intruder", name: "issue@github" },
    { id: "user@elsewhere", name: "Another participant" },
  ];
  const result = commitRecipients(draft("issue@github;user@elsewhere"), directory);
  assert.equal(result.error, undefined);
  assert.deepEqual(result.field.recipients, [
    { address: "issue@github", label: "issue@github" },
    { address: "user@elsewhere", label: "user@elsewhere" },
  ]);
});

test("empty drafts are unchanged and separator-only drafts commit without recipients", () => {
  const field = draft("  ");
  assert.equal(commitRecipients(field, participants).field, field);
  assert.deepEqual(commitRecipients(draft(",；\n"), participants).field, draft(""));
});
