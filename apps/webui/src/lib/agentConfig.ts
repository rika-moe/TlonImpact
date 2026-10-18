import type { AgentConfig, AgentPersona } from "../client/rest";

export const PERSONA_FIELDS = [
  { key: "identity", name: "IDENTITY.md", description: "How I define myself: name, self-reference, identity narrative, and current self-described role. This is a member's own account, not a system certification. Members can edit this file." },
  { key: "profile", name: "SOUL.md", description: "What I value and how I express myself and make judgments: values, personality, tone, and responses to uncertainty and disagreement. Members can edit this file." },
  { key: "behavior", name: "RULES.md", description: "The working rules I must follow: procedures and explicit constraints. Maintained by humans; members cannot edit this file." },
] as const;

export type AgentUpdate = { persona?: Partial<AgentPersona>; llm?: { model_name: string } };

/** Only send changed fields: an untouched file may have been self-edited meanwhile. */
export function buildAgentUpdate(
  initial: AgentConfig | null,
  persona: AgentPersona,
  modelName: string,
): AgentUpdate {
  if (!initial) throw new Error("Configuration has not loaded; cannot save");
  const model = modelName.trim();
  if (!model && model !== initial.llm.model_name) throw new Error("MODEL is required");
  const changed: Partial<AgentPersona> = {};
  for (const { key } of PERSONA_FIELDS) {
    if (persona[key] !== initial.persona[key]) changed[key] = persona[key];
  }
  const update: AgentUpdate = {};
  if (Object.keys(changed).length > 0) update.persona = changed;
  if (model !== initial.llm.model_name) update.llm = { model_name: model };
  return update;
}
