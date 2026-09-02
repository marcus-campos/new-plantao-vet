/** Mesmos tokens do web e dos mockups: uma identidade só. */
export const theme = {
  ground: "#F3F6F4",
  surface: "#FFFFFF",
  surfaceSubtle: "#F8FAF9",
  ink: "#17251F",
  ink2: "#4A5A52",
  ink3: "#7C8B83",
  inkMuted: "#9AA9A1",
  primary: "#0C6B58",
  primaryDark: "#0A5847",
  tint: "#E4F0EB",
  line: "#DFE7E2",
  ok: "#1F7A4D",
  okBg: "#E3F2E9",
  okEdge: "#9CCBB0",
  warn: "#8F5D0B",
  warnBg: "#F7EDD8",
  warnEdge: "#D9A84E",
  late: "#A83A31",
  lateBg: "#F9E6E3",
  lateEdge: "#E9C4BF",
} as const;

export function stateColors(state: string) {
  switch (state) {
    case "overdue":
      return { fg: theme.late, bg: theme.lateBg, edge: theme.lateEdge };
    case "due":
      return { fg: theme.warn, bg: theme.warnBg, edge: theme.warnEdge };
    case "on_time":
    case "done":
      return { fg: theme.ok, bg: theme.okBg, edge: theme.okEdge };
    default:
      return { fg: theme.ink3, bg: theme.surfaceSubtle, edge: theme.line };
  }
}

/** Alvo de toque mínimo: a mão está de luva, entre boxes. */
export const TOUCH_TARGET = 48;
