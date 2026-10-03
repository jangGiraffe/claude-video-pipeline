export type Word = { text: string; start: number; end: number; sentence: number };
export type Sentence = { text: string; start: number; end: number };
export type Timing = { duration: number; sentences: Sentence[]; words: Word[] };

/**
 * 장면 안에서 "언제"를 가리키는 값.
 *  - number : 그 문장 번호(0부터)가 시작될 때
 *  - string : 장면 시작 이후 처음 나오는, 이 글자를 포함한 어절이 발음될 때
 */
export type TimeRef = number | string;

export type Theme = {
  bg: string;
  bg2: string;
  fg: string;
  dim: string;
  accent: string;
  accent2: string;
  card: string;
};

export type SceneSpec = {
  /** 장면 시작: 문장 번호 또는 어절. 다음 장면 시작 직전까지 이어진다 */
  at: TimeRef;
  type: "hook" | "keyword" | "title" | "steps" | "terminal" | "bullets";
  props: Record<string, unknown>;
};

export type Plan = { theme?: Partial<Theme>; captions?: boolean; scenes: SceneSpec[] };

export type ExplainerProps = {
  /** video/public/ 아래 작업 폴더 이름 (voice.wav, timing.json, scenes.json 위치) */
  job: string;
  timing?: Timing;
  plan?: Plan;
};
