import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import type { Theme, Timing } from "./types";

/** 하단 자막: 현재 문장을 보여주고, 지금 발음 중인 어절을 강조 */
export const Captions: React.FC<{ timing: Timing; theme: Theme }> = ({ timing, theme }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  const { sentences, words } = timing;
  // 다음 문장이 시작하기 전까지는 직전 문장을 유지 (문장 사이 깜빡임 방지)
  let si = -1;
  for (let i = 0; i < sentences.length; i++) if (sentences[i].start - 0.1 <= t) si = i;
  if (si < 0 || t > sentences[si].end + 0.8) return null;

  const ws = words.filter((w) => w.sentence === si);
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 70 }}>
      <div style={{ maxWidth: 1600, padding: "18px 40px", borderRadius: 18, background: "#000000aa", fontSize: 46, fontWeight: 700, lineHeight: 1.45, textAlign: "center", wordBreak: "keep-all" }}>
        {ws.map((w, i) => {
          const spoken = w.start <= t;
          const current = spoken && t < (ws[i + 1]?.start ?? w.end + 0.3);
          return (
            <span key={i} style={{ color: current ? theme.accent : spoken ? theme.fg : theme.fg + "80" }}>
              {w.text}
              {i < ws.length - 1 ? " " : ""}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
