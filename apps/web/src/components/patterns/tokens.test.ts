import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const root = path.resolve("src");
const tokenFile = path.join(root, "app/globals.css");
const css = fs.readFileSync(tokenFile, "utf8");
const palette =
  /(?:bg|text|border|ring|outline|fill|stroke|divide|shadow|decoration|accent|caret|from|via|to)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b|(?:bg|text|border|ring|outline|fill|stroke|divide|shadow|decoration|from|via|to)-(?:white|black)\b/;
const literal =
  /#(?:[\da-f]{8}|[\da-f]{6}|[\da-f]{4}|[\da-f]{3})\b|\b(?:rgb|rgba|hsl|hsla|oklch|oklab)\s*\(\s*[\d.]/i;
function files(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .flatMap((e) => (e.isDirectory() ? files(path.join(dir, e.name)) : [path.join(dir, e.name)]));
}
function colors(theme: string) {
  const common = css.match(/:root,\n\.dark \{([\s\S]*?)\n\}/)?.[1] ?? "";
  const block = css.match(new RegExp(`${theme} \\{([\\s\\S]*?)\\n\\}`))?.[1];
  if (!block) throw new Error(`Missing theme ${theme}`);
  return Object.fromEntries(
    [...(block + common).matchAll(/--([\w-]+):\s*(#[\da-f]+);/gi)].map((m) => [m[1]!, m[2]!]),
  );
}
function luminance(hex: string) {
  const channels = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return channels[0]! * 0.2126 + channels[1]! * 0.7152 + channels[2]! * 0.0722;
}
function contrast(a: string, b: string) {
  const [lo, hi] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (hi! + 0.05) / (lo! + 0.05);
}

describe("semantic token contract", () => {
  it("rejects palette utilities and colour literals throughout shipped source", () => {
    const violations = files(root)
      .filter((f) => f !== tokenFile && !/\.test\.[jt]sx?$/.test(f) && /\.(?:css|[jt]sx?)$/.test(f))
      .flatMap((f) => {
        const text = fs.readFileSync(f, "utf8");
        return palette.test(text) || literal.test(text) ? [path.relative(root, f)] : [];
      });
    expect(violations).toEqual([]);
  });
  it("the guard detects raw variants, arbitrary colours and alpha palette classes", () => {
    expect(palette.test("hover:bg-indigo-600/50")).toBe(true);
    expect(palette.test("dark:text-slate-200")).toBe(true);
    expect(palette.test("bg-black/10")).toBe(true);
    expect(literal.test("border-[#ABCDEF]")).toBe(true);
    expect(literal.test("rgb(12, 40, 60)")).toBe(true);
    expect(palette.test("bg-primary text-primary-foreground ring-ring")).toBe(false);
    expect(literal.test("color: var(--primary)")).toBe(false);
  });
  for (const theme of [":root", "\\.dark"]) {
    it(`${theme} meets AA for every text and status pair`, () => {
      const values = colors(theme);
      const pairs: [[string, string], ...Array<[string, string]>] = [
        ["foreground", "background"],
        ["foreground", "card"],
        ["card-foreground", "card"],
        ["popover-foreground", "popover"],
        ["primary-foreground", "primary"],
        ["primary-foreground", "primary-hover"],
        ["secondary-foreground", "secondary"],
        ["accent-foreground", "accent"],
        ["video-foreground", "video-surface"],
        ["primary", "muted"],
        ["success", "muted"],
        ["info", "muted"],
        ["danger", "muted"],
        ["muted-foreground", "background"],
        ["muted-foreground", "card"],
        ["muted-foreground", "muted"],
        ["primary", "background"],
        ["primary", "card"],
      ];
      for (const name of ["success", "warning", "danger", "info"]) {
        pairs.push([name, `${name}-surface`], [name, "background"], [name, "card"]);
      }
      for (const [foreground, background] of pairs) {
        expect(
          contrast(values[foreground]!, values[background]!),
          `${theme}: ${foreground} on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
      for (const border of ["ring", "input"]) {
        for (const surface of ["background", "card", "muted"]) {
          expect(
            contrast(values[border]!, values[surface]!),
            `${border} against ${surface}`,
          ).toBeGreaterThanOrEqual(3);
        }
      }
    });
  }
});
