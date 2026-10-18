import type { ParticipantInfo } from "../types";
import { memberAddress } from "./members";

export interface Recipient {
  address: string;
  label: string;
}

export interface RecipientField {
  recipients: Recipient[];
  draft: string;
}

export interface RecipientCandidate extends Recipient {
  id: string;
  type: string;
}

function candidate(participant: ParticipantInfo): RecipientCandidate {
  return {
    id: participant.id,
    address: memberAddress(participant.id),
    label: participant.name ?? participant.id,
    type: participant.type ?? "agent",
  };
}

function aliases(participant: ParticipantInfo): string[] {
  return [participant.id, participant.name ?? "", memberAddress(participant.id)]
    .filter(Boolean)
    .map((value: string): string => value.toLowerCase());
}

/** Distinct addresses, so duplicate directory records do not create ambiguity. */
function exactMatches(
  token: string,
  participants: readonly ParticipantInfo[],
): RecipientCandidate[] {
  const normalized = token.toLowerCase();
  // Complete addresses are identities, never display-name aliases. Exact IDs
  // likewise outrank names so another participant cannot shadow an identity.
  const isAddress = token.includes("@");
  const exactIdentities = participants.filter((participant: ParticipantInfo): boolean =>
    memberAddress(participant.id) === token || (!isAddress && participant.id === token),
  );
  const exactNames = isAddress ? [] : participants.filter(
    (participant: ParticipantInfo): boolean => participant.name === token,
  );
  const possibleMatches = exactIdentities.length ? exactIdentities
    : exactNames.length ? exactNames
    : participants.filter((participant: ParticipantInfo): boolean => isAddress
      ? memberAddress(participant.id).toLowerCase() === normalized
      : aliases(participant).includes(normalized));
  const matches = new Map<string, RecipientCandidate>();
  for (const participant of possibleMatches) {
    const entry = candidate(participant);
    const key = entry.address;
    if (!matches.has(key)) matches.set(key, entry);
  }
  return [...matches.values()];
}

function addressKey(address: string, participants: readonly ParticipantInfo[]): string {
  const matches = exactMatches(address, participants);
  return matches.length === 1 ? matches[0].address : address;
}

/** Case-insensitive substring search, with prefix matches first and no result cap. */
export function searchRecipients(
  participants: readonly ParticipantInfo[],
  query: string,
  selected: readonly Recipient[],
): RecipientCandidate[] {
  const normalized = query.trim().toLowerCase();
  const selectedAddresses = new Set(
    selected.map((entry: Recipient): string => addressKey(entry.address, participants)),
  );
  const matches: { entry: RecipientCandidate; rank: number; index: number }[] = [];
  const seen = new Set<string>();
  participants.forEach((participant: ParticipantInfo, index: number): void => {
    const entry = candidate(participant);
    const key = entry.address;
    if (selectedAddresses.has(key) || seen.has(key)) return;
    const values = aliases(participant);
    if (!values.some((value: string): boolean => value.includes(normalized))) return;
    const rank = values.some((value: string): boolean => value.startsWith(normalized)) ? 0 : 1;
    seen.add(key);
    matches.push({ entry, rank, index });
  });
  matches.sort((left, right): number => left.rank - right.rank || left.index - right.index);
  return matches.map(({ entry }): RecipientCandidate => entry);
}

/** Commit the whole draft atomically; invalid tokens leave all input intact. */
export function commitRecipients(
  field: RecipientField,
  participants: readonly ParticipantInfo[],
): { field: RecipientField; error?: string } {
  if (!field.draft.trim()) return { field };
  const tokens = field.draft.split(/[,;，；\r\n]+/).map((token: string): string => token.trim()).filter(Boolean);
  const additions: Recipient[] = [];
  const selectedAddresses = new Set(
    field.recipients.map((entry: Recipient): string => addressKey(entry.address, participants)),
  );
  for (const token of tokens) {
    const matches = exactMatches(token, participants);
    if (matches.length > 1) {
      return { field, error: `Multiple recipients match "${token}". Choose a recipient from the list.` };
    }
    let entry: Recipient;
    if (matches.length === 1) {
      entry = { address: matches[0].address, label: matches[0].label };
    } else if (/^[^\s@,;，；]+@[^\s@,;，；]+$/.test(token)) {
      entry = { address: token, label: token };
    } else {
      return { field, error: `Cannot add "${token}". Choose a recipient or enter a complete address.` };
    }
    const key = entry.address;
    if (selectedAddresses.has(key)) continue;
    selectedAddresses.add(key);
    additions.push(entry);
  }
  return { field: { recipients: [...field.recipients, ...additions], draft: "" } };
}
