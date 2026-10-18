import type { AgentConfig, AgentPersona } from "../client/rest";

export const PERSONA_FIELDS = [
  { key: "identity", name: "IDENTITY.md", description: "我如何定义自己：名字、自称、身份叙述、当前自认的角色；这是成员自述，不代表系统认证。成员可以自行修改。" },
  { key: "profile", name: "SOUL.md", description: "我重视什么，以及如何表达和判断：价值观、性格、语气、面对不确定和分歧的倾向。成员可以自行修改。" },
  { key: "behavior", name: "RULES.md", description: "我必须遵循哪些工作规则：流程和明确约束。由人类维护，成员不能自行修改。" },
] as const;

export type AgentUpdate = { persona?: Partial<AgentPersona>; llm?: { model_name: string } };

/** Only send changed fields: an untouched file may have been self-edited meanwhile. */
export function buildAgentUpdate(
  initial: AgentConfig | null,
  persona: AgentPersona,
  modelName: string,
): AgentUpdate {
  if (!initial) throw new Error("配置尚未加载，无法保存");
  const model = modelName.trim();
  if (!model && model !== initial.llm.model_name) throw new Error("MODEL 不能为空");
  const changed: Partial<AgentPersona> = {};
  for (const { key } of PERSONA_FIELDS) {
    if (persona[key] !== initial.persona[key]) changed[key] = persona[key];
  }
  const update: AgentUpdate = {};
  if (Object.keys(changed).length > 0) update.persona = changed;
  if (model !== initial.llm.model_name) update.llm = { model_name: model };
  return update;
}
