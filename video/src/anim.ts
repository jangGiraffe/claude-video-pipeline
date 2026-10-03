import { interpolate, spring } from "remotion";

/** 등장 애니메이션: start 프레임부터 아래에서 떠오르며 나타남 */
export const enter = (frame: number, fps: number, start = 0, distance = 40) => {
  const p = spring({ frame: frame - start, fps, config: { damping: 200 }, durationInFrames: 18 });
  return {
    opacity: interpolate(frame - start, [0, 10], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
    transform: `translateY(${(1 - p) * distance}px)`,
  };
};

/** 튀어나오는 팝 (키워드용) */
export const pop = (frame: number, fps: number, start = 0) => {
  const p = spring({ frame: frame - start, fps, config: { damping: 12, stiffness: 160 } });
  return { opacity: Math.min(1, Math.max(0, (frame - start) / 6)), transform: `scale(${0.6 + 0.4 * p})` };
};
