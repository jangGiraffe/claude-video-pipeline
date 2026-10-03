import React from "react";
import { Composition, staticFile } from "remotion";
import { Explainer } from "./Explainer";
import type { ExplainerProps, Plan, Timing } from "./types";

const FPS = 30;
const TAIL_SEC = 0.8;

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Explainer"
    component={Explainer}
    width={1920}
    height={1080}
    fps={FPS}
    durationInFrames={FPS * 10}
    defaultProps={{ job: "demo" } as ExplainerProps}
    calculateMetadata={async ({ props }) => {
      const [timing, plan] = await Promise.all([
        fetch(staticFile(`${props.job}/timing.json`)).then((r) => r.json() as Promise<Timing>),
        fetch(staticFile(`${props.job}/scenes.json`)).then((r) => r.json() as Promise<Plan>),
      ]);
      return {
        durationInFrames: Math.ceil((timing.duration + TAIL_SEC) * FPS),
        props: { ...props, timing, plan },
      };
    }}
  />
);
