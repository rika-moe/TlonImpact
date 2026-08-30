import { Box, Text, useInput } from "ink";
import React, { useCallback, useEffect, useState } from "react";
import type { RestClient } from "../client/restClient.js";

/**
 * Settings view (wizard removal F): read/write the LLM env configuration
 * stored in {TLON_HOME}/.env. The API key is shown in plain text — this
 * is the local operator console's own config (user decision).
 */
const FIELDS = ["model", "api_key", "base_url", "temperature"] as const;
const LABELS: Record<string, string> = {
  model: "model",
  api_key: "api_key",
  base_url: "base_url (可选)",
  temperature: "temperature",
};

export function SettingsView({
  rest,
  onEditingChange,
}: {
  rest: RestClient;
  onEditingChange: (editing: boolean) => void;
}) {
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<Record<string, string>>({});
  const [field, setField] = useState(0);
  const [path, setPath] = useState<string | null>(null);
  const [configured, setConfigured] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await rest.getSettingsEnv();
      setPath(data.path);
      setConfigured(data.configured);
      setForm({
        model: data.values.model ?? "",
        api_key: data.values.api_key ?? "",
        base_url: data.values.base_url ?? "",
        temperature: String(data.values.temperature ?? ""),
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [rest]);

  useEffect(() => {
    void load();
  }, [load]);

  // Report editing so App gates its global view-switch keys.
  useEffect(() => {
    onEditingChange(editing);
  }, [editing, onEditingChange]);

  const submit = async () => {
    setSaving(true);
    try {
      const temp = Number.parseFloat(form["temperature"] ?? "");
      await rest.updateSettingsEnv({
        model: form["model"]?.trim() || undefined,
        api_key: form["api_key"]?.trim() || undefined,
        base_url: form["base_url"]?.trim() || undefined,
        temperature: Number.isFinite(temp) ? temp : undefined,
      });
      setSaved("已保存——重启 dream 生效（Settings 是进程级单例）");
      setEditing(false);
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  useInput((input, key) => {
    if (!editing) {
      if (input === "e") {
        setEditing(true);
        setField(0);
        setSaved(null);
        setError(null);
      } else if (input === "r") {
        void load();
      }
      return;
    }

    if (saving) return;
    if (key.escape) {
      setEditing(false);
      return;
    }
    if (key.return) {
      if (field === FIELDS.length - 1) {
        void submit();
      } else {
        setField((prev) => Math.min(FIELDS.length - 1, prev + 1));
      }
      return;
    }
    if (key.upArrow) {
      setField((prev) => Math.max(0, prev - 1));
      return;
    }
    if (key.downArrow) {
      setField((prev) => Math.min(FIELDS.length - 1, prev + 1));
      return;
    }
    if (key.backspace || key.delete) {
      const active = FIELDS[field] ?? "";
      setForm((prev) => ({ ...prev, [active]: (prev[active] ?? "").slice(0, -1) }));
      return;
    }
    if (key.tab || key.ctrl || key.meta) return;
    if (input) {
      const text = input.replace(/[\r\n]/g, "");
      if (!text) return;
      const active = FIELDS[field] ?? "";
      setForm((prev) => ({ ...prev, [active]: (prev[active] ?? "") + text }));
    }
  });

  if (loading) {
    return (
      <Box flexDirection="column">
        <Text bold>Settings</Text>
        <Text dimColor>loading…</Text>
      </Box>
    );
  }

  if (editing) {
    return (
      <Box flexDirection="column">
        <Box gap={2}>
          <Text bold>Edit LLM env config</Text>
          <Text dimColor>enter 下一字段 / 提交 · esc 取消</Text>
          {saving ? <Text color="yellow">saving…</Text> : null}
        </Box>
        <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1}>
          {FIELDS.map((name) => {
            const active = name === FIELDS[field];
            return (
              <Box key={name}>
                <Text bold={active}>{(LABELS[name] ?? name).padEnd(14)}: </Text>
                <Text color={active ? "cyan" : undefined}>{form[name] ?? ""}</Text>
                {active ? <Text inverse> </Text> : null}
              </Box>
            );
          })}
        </Box>
        {error ? <Text color="red">{error}</Text> : null}
      </Box>
    );
  }

  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text bold>Settings (LLM env)</Text>
        <Text dimColor>e edit · r refresh</Text>
        {error ? <Text color="red">{error}</Text> : null}
        {saved ? <Text color="green">{saved}</Text> : null}
      </Box>
      <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1}>
        <Box>
          <Text bold>api_key: </Text>
          <Text color={configured ? "green" : "yellow"}>
            {configured ? "已配置" : "未配置（认知成员首次调用 LLM 会失败）"}
          </Text>
        </Box>
        <Box>
          <Text bold>model: </Text>
          <Text>{form["model"] ?? ""}</Text>
        </Box>
        <Box>
          <Text bold>base_url: </Text>
          <Text>{form["base_url"] ?? ""}</Text>
        </Box>
        <Box>
          <Text bold>temperature: </Text>
          <Text>{form["temperature"] ?? ""}</Text>
        </Box>
        {path ? (
          <Box>
            <Text dimColor>写入: {path}</Text>
          </Box>
        ) : null}
      </Box>
    </Box>
  );
}
