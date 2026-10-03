import React from "react";
import { AbsoluteFill, Audio, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/NotoSansKR";
import { Captions } from "./Captions";
import { SCENES } from "./scenes";
import { resolveSec, SceneContext } from "./timing";
import type { ExplainerProps, Theme } from "./types";

const { fontFamily } = loadFont("normal", {
  weights: ["400", "700", "800", "900"],
  subsets: ["korean", "latin"],
  ignoreTooManyRequestsWarning: true,
});

export const DEFAULT_THEME: Theme = {
  bg: "#0b1020",
  bg2: "#141b34",
  fg: "#f4f6fb",
  dim: "#8b93ad",
  accent: "#5eead4",
  accent2: "#a78bfa",
  card: "#161d36",
};

const Background: React.FC<{ theme: Theme }> = ({ theme }) => {
  const frame = useCurrentFrame();
  const x = 50 + 20 * Math.sin(frame / 90);
  const y = 40 + 15 * Math.cos(frame / 110);
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at ${x}% ${y}%, ${theme.accent2}22, transparent 45%),
                     radial-gradient(circle at ${100 - x}% ${100 - y}%, ${theme.accent}18, transparent 40%),
                     linear-gradient(160deg, ${theme.bg}, ${theme.bg2})`,
      }}
    >
      <AbsoluteFill
        style={{
          backgroundImage: "linear-gradient(#ffffff08 1px, transparent 1px), linear-gradient(90deg, #ffffff08 1px, transparent 1px)",
          backgroundSize: "80px 80px",
        }}
      />
    </AbsoluteFill>
  );
};

/** 장면 전환: 앞 6프레임 페이드인 */
const FadeIn: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{ opacity: interpolate(frame, [0, 6], [0, 1], { extrapolateRight: "clamp" }) }}>{children}</AbsoluteFill>;
};

const ProgressBar: React.FC<{ theme: Theme }> = ({ theme }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return (
    <div style={{ position: "absolute", top: 0, left: 0, height: 6, width: `${(frame / durationInFrames) * 100}%`, background: `linear-gradient(90deg, ${theme.accent}, ${theme.accent2})` }} />
  );
};

export const Explainer: React.FC<ExplainerProps> = ({ job, timing, plan }) => {
  const { fps, durationInFrames } = useVideoConfig();
  if (!timing || !plan) return null;
  const theme = { ...DEFAULT_THEME, ...plan.theme };

  // 장면 시작 시각(초). 첫 장면은 0초부터, 각 장면은 다음 장면 시작 직전까지 (문장 사이 빈 화면 방지)
  const starts = plan.scenes.map((s, i) => (i === 0 ? 0 : resolveSec(timing, s.at)));
  const frames = starts.map((s) => Math.round(s * fps));

  return (
    <AbsoluteFill style={{ fontFamily, color: theme.fg }}>
      <Background theme={theme} />
      <Audio src={staticFile(`${job}/voice.wav`)} />
      {plan.scenes.map((scene, i) => {
        const Comp = SCENES[scene.type] as React.FC<any>;
        if (!Comp) throw new Error(`알 수 없는 장면 type: ${scene.type}`);
        const from = frames[i];
        const to = i + 1 < frames.length ? frames[i + 1] : durationInFrames;
        return (
          <Sequence key={i} from={from} durationInFrames={Math.max(1, to - from)} name={`${i}:${scene.type}`}>
            <SceneContext.Provider value={{ timing, sceneStartSec: starts[i], theme }}>
              <FadeIn>
                <Comp {...scene.props} />
              </FadeIn>
            </SceneContext.Provider>
          </Sequence>
        );
      })}
      {plan.captions !== false && <Captions timing={timing} theme={theme} />}
      <ProgressBar theme={theme} />
    </AbsoluteFill>
  );
};
