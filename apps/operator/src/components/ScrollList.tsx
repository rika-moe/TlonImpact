import { Box, Text, useStdout } from "ink";
import React, { type ReactNode } from "react";

/** Rows reserved for app chrome (header, footer, borders, view title). */
const CHROME_ROWS = 8;

export function useListHeight(): number {
  const { stdout } = useStdout();
  const rows = stdout?.rows ?? 24;
  return Math.max(3, rows - CHROME_ROWS);
}

/** Compute the window offset so the selected index stays visible. */
export function windowOffset(selected: number, offset: number, height: number, count: number): number {
  if (selected < offset) return selected;
  if (selected >= offset + height) return Math.min(selected - height + 1, Math.max(0, count - height));
  return Math.min(offset, Math.max(0, count - height));
}

/** A windowed scroll list: renders only the visible slice around the selection. */
export function ScrollList({
  items,
  selected,
  height,
  renderItem,
}: {
  items: readonly unknown[];
  selected: number;
  height: number;
  renderItem: (item: unknown, index: number, isSelected: boolean) => ReactNode;
}) {
  const offset = windowOffset(selected, 0, height, items.length);
  const visible = items.slice(offset, offset + height);
  return (
    <Box flexDirection="column" height={height}>
      {visible.length === 0 ? (
        <Text dimColor>(empty)</Text>
      ) : (
        visible.map((item, i) => {
          const index = offset + i;
          return (
            <Text key={index} wrap="truncate">
              {renderItem(item, index, index === selected)}
            </Text>
          );
        })
      )}
    </Box>
  );
}

/** Render a value as pretty-printed JSON lines, truncated to the given height. */
export function JsonDetail({ value, height }: { value: unknown; height: number }) {
  let lines: string[];
  if (value === null || value === undefined) {
    lines = ["(no detail)"];
  } else {
    try {
      lines = JSON.stringify(value, null, 2).split("\n");
    } catch {
      lines = [String(value)];
    }
  }
  const truncated = lines.length > height;
  const visible = truncated ? lines.slice(0, Math.max(1, height - 1)) : lines;
  return (
    <Box flexDirection="column" height={height}>
      {visible.map((line, i) => (
        <Text key={i} wrap="truncate">
          {line}
        </Text>
      ))}
      {truncated ? <Text dimColor>… ({lines.length - visible.length} more lines)</Text> : null}
    </Box>
  );
}
