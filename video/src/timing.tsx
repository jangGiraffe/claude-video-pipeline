import React, { createContext, useContext } from "react";
import { useVideoConfig } from "remotion";
import type { Theme, TimeRef, Timing } from "./types";

type Ctx = { timing: Timing; sceneStartSec: number; theme: Theme };
export const SceneContext = createContext<Ctx | null>(null);

export const useScene = () => {
  const c = useContext(SceneContext);
  if (!c) throw new Error("SceneContext 없음");
  return c;
};

const norm = (s: string) => s.replace(/[\s\p{P}\p{S}]/gu, "").toLowerCase();

/** TimeRef → 절대 초 */
export const resolveSec = (timing: Timing, ref: TimeRef, afterSec = 0): number => {
  if (typeof ref === "number") return timing.sentences[ref]?.start ?? 0;
  const q = norm(ref);
  const w = timing.words.find((w) => w.start >= afterSec - 0.05 && norm(w.text).includes(q));
  if (!w) {
    console.warn(`TimeRef '${ref}' 을(를) 찾지 못함 → 장면 시작으로 대체`);
    return afterSec;
  }
  return w.start;
};

/** 현재 장면(Sequence) 기준 상대 프레임으로 변환 */
export const useAt = () => {
  const { timing, sceneStartSec } = useScene();
  const { fps } = useVideoConfig();
  return (ref: TimeRef | undefined) =>
    ref === undefined ? 0 : Math.round((resolveSec(timing, ref, sceneStartSec) - sceneStartSec) * fps);
};

/** "몇 *시간씩*?" 처럼 *별표*로 감싼 부분을 강조색으로 */
export const Emph: React.FC<{ text: string }> = ({ text }) => {
  const { theme } = useScene();
  return (
    <>
      {text.split(/(\*[^*]+\*)/g).map((part, i) =>
        part.startsWith("*") && part.endsWith("*") ? (
          <span key={i} style={{ color: theme.accent }}>
            {part.slice(1, -1)}
          </span>
        ) : (
          <React.Fragment key={i}>{part}</React.Fragment>
        ),
      )}
    </>
  );
};
