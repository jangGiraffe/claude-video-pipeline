import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { enter, pop } from "./anim";
import { Emph, useAt, useScene } from "./timing";
import type { TimeRef } from "./types";

const Center: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: "80px 140px 220px" }}>
    {children}
  </AbsoluteFill>
);

/* ───────── hook: 큰 질문/문장 여러 줄이 차례로 등장 ───────── */
type HookProps = { lines: { text: string; at?: TimeRef }[] };
export const Hook: React.FC<HookProps> = ({ lines }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const at = useAt();
  return (
    <Center>
      <div style={{ display: "flex", flexDirection: "column", gap: 24, alignItems: "center" }}>
        {lines.map((l, i) => (
          <div key={i} style={{ fontSize: 120, fontWeight: 900, letterSpacing: -3, lineHeight: 1.1, ...enter(frame, fps, at(l.at) + i * 4) }}>
            <Emph text={l.text} />
          </div>
        ))}
      </div>
    </Center>
  );
};

/* ───────── keyword: 큰 키워드 하나 + 보조 문구(선택) + 칩(선택) ───────── */
type KeywordProps = { text: string; sub?: string; chip?: string; at?: TimeRef };
export const Keyword: React.FC<KeywordProps> = ({ text, sub, chip, at: start }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { theme } = useScene();
  const t0 = useAt()(start);
  return (
    <Center>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 36 }}>
        {chip && (
          <div style={{ fontFamily: "Consolas, monospace", fontSize: 40, padding: "14px 32px", borderRadius: 16, background: theme.card, border: `2px solid ${theme.accent}55`, color: theme.accent, ...enter(frame, fps, t0) }}>
            {chip}
          </div>
        )}
        <div style={{ fontSize: 150, fontWeight: 900, letterSpacing: -4, ...pop(frame, fps, t0 + 4) }}>
          <Emph text={text} />
        </div>
        {sub && <div style={{ fontSize: 48, color: theme.dim, ...enter(frame, fps, t0 + 14) }}>{sub}</div>}
      </div>
    </Center>
  );
};

/* ───────── title: 작은 머리말 + 큰 제목 + 밑줄 + 부제 ───────── */
type TitleProps = { kicker?: string; title: string; subtitle?: string };
export const Title: React.FC<TitleProps> = ({ kicker, title, subtitle }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { theme } = useScene();
  const bar = interpolate(frame, [8, 30], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Center>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 28 }}>
        {kicker && <div style={{ fontSize: 40, fontWeight: 700, color: theme.accent, letterSpacing: 6, ...enter(frame, fps, 0) }}>{kicker}</div>}
        <div style={{ fontSize: 120, fontWeight: 900, letterSpacing: -3, textAlign: "center", lineHeight: 1.15, ...enter(frame, fps, 4) }}>
          <Emph text={title} />
        </div>
        <div style={{ height: 10, width: 520 * bar, borderRadius: 5, background: `linear-gradient(90deg, ${theme.accent}, ${theme.accent2})` }} />
        {subtitle && <div style={{ fontSize: 48, color: theme.dim, ...enter(frame, fps, 16) }}>{subtitle}</div>}
      </div>
    </Center>
  );
};

/* ───────── steps: 단계 카드가 말에 맞춰 하나씩 켜지는 흐름도 ───────── */
type Step = { label: string; sub?: string; icon?: string; at: TimeRef };
type StepsProps = { title?: string; steps: Step[] };
export const Steps: React.FC<StepsProps> = ({ title, steps }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { theme } = useScene();
  const at = useAt();
  const starts = steps.map((s) => at(s.at));
  const active = starts.reduce((acc, s, i) => (frame >= s ? i : acc), -1);
  return (
    <Center>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 70, width: "100%" }}>
        {title && <div style={{ fontSize: 64, fontWeight: 800, ...enter(frame, fps, 0) }}><Emph text={title} /></div>}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 18 }}>
          {steps.map((s, i) => {
            const shown = frame >= starts[i];
            const isActive = i === active;
            const glow = isActive ? interpolate(Math.sin(frame / 6), [-1, 1], [0.5, 1]) : 0;
            return (
              <React.Fragment key={i}>
                {i > 0 && (
                  <div style={{ fontSize: 56, color: shown ? theme.accent : theme.dim, opacity: shown ? 1 : 0.25, transition: "none" }}>→</div>
                )}
                <div style={shown ? enter(frame, fps, starts[i], 30) : { opacity: 0.28 }}>
                  <div
                    style={{
                      width: 250, padding: "40px 16px", borderRadius: 28, textAlign: "center",
                      background: theme.card,
                      border: `3px solid ${isActive ? theme.accent : shown ? theme.accent + "55" : "#ffffff14"}`,
                      boxShadow: isActive ? `0 0 ${60 * glow}px ${theme.accent}66` : "none",
                      transform: `scale(${isActive ? 1.08 : 1})`,
                    }}
                  >
                    {s.icon && <div style={{ fontSize: 76, marginBottom: 12 }}>{s.icon}</div>}
                    <div style={{ fontSize: s.label.length > 8 ? 36 : 44, fontWeight: 800, whiteSpace: "nowrap", color: shown ? theme.fg : theme.dim }}>{s.label}</div>
                    {s.sub && <div style={{ fontSize: 28, marginTop: 10, color: theme.dim }}>{s.sub}</div>}
                  </div>
                </div>
              </React.Fragment>
            );
          })}
        </div>
      </div>
    </Center>
  );
};

/* ───────── terminal: 줄마다 타이핑되는 터미널 창 ───────── */
type TerminalProps = { title?: string; lines: { text: string; at?: TimeRef; color?: string }[] };
export const Terminal: React.FC<TerminalProps> = ({ title = "terminal", lines }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { theme } = useScene();
  const at = useAt();
  return (
    <Center>
      <div style={{ width: 1400, borderRadius: 24, overflow: "hidden", background: "#0a0e17", border: "2px solid #ffffff18", boxShadow: "0 40px 120px #0008", ...enter(frame, fps, 0) }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "20px 28px", background: "#ffffff0a" }}>
          {["#ff5f57", "#febc2e", "#28c840"].map((c) => <div key={c} style={{ width: 20, height: 20, borderRadius: 10, background: c }} />)}
          <div style={{ marginLeft: 16, fontSize: 26, color: theme.dim }}>{title}</div>
        </div>
        <div style={{ padding: "36px 44px", fontFamily: "Consolas, 'Noto Sans KR', monospace", fontSize: 40, lineHeight: 1.7, minHeight: 300 }}>
          {lines.map((l, i) => {
            const s = at(l.at) + i * 3;
            const n = Math.max(0, Math.floor((frame - s) * 1.6));
            if (frame < s) return null;
            const typing = n < l.text.length;
            return (
              <div key={i} style={{ color: l.color ?? theme.fg, whiteSpace: "pre" }}>
                {l.text.slice(0, n)}
                {typing && <span style={{ background: theme.accent, color: theme.accent }}>_</span>}
              </div>
            );
          })}
        </div>
      </div>
    </Center>
  );
};

/* ───────── bullets: 제목 + 항목이 말에 맞춰 체크되며 등장 ───────── */
type BulletsProps = { title: string; items: { text: string; at?: TimeRef }[] };
export const Bullets: React.FC<BulletsProps> = ({ title, items }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { theme } = useScene();
  const at = useAt();
  return (
    <Center>
      <div style={{ display: "flex", flexDirection: "column", gap: 40, minWidth: 1100 }}>
        <div style={{ fontSize: 76, fontWeight: 900, ...enter(frame, fps, 0) }}><Emph text={title} /></div>
        {items.map((it, i) => {
          const s = at(it.at) + i * 2;
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 28, fontSize: 52, fontWeight: 700, ...enter(frame, fps, s, 30) }}>
              <div style={{ width: 56, height: 56, borderRadius: 16, background: theme.accent, color: theme.bg, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 38, fontWeight: 900, ...pop(frame, fps, s + 4) }}>✓</div>
              <Emph text={it.text} />
            </div>
          );
        })}
      </div>
    </Center>
  );
};

export const SCENES = { hook: Hook, keyword: Keyword, title: Title, steps: Steps, terminal: Terminal, bullets: Bullets } as const;
