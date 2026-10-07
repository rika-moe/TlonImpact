/**
 * Member id ↔ mail address mapping (pure functions, unit-tested).
 *
 * Member ids are what `GET /participants` and the observer endpoint
 * `GET /members/{member_id}/mail` use: a bare agent id ("gu-yanxi") or a
 * prefixed human id ("human:alice"). Mail addresses drop the "human:"
 * prefix and append the internal domain (`member_mail_dir` in reverse).
 */

/** The member id of a human user ("alice" -> "human:alice"). */
export function selfMemberId(username: string): string {
  return `human:${username}`;
}

/**
 * The mail address of a member id ("human:alice" -> "alice@tlon.internal",
 * "gu-yanxi" -> "gu-yanxi@tlon.internal").
 */
export function memberAddress(memberId: string): string {
  const local = memberId.startsWith("human:") ? memberId.slice("human:".length) : memberId;
  return `${local}@tlon.internal`;
}
