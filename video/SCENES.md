# scenes.json 작성 가이드 (Claude용)

`work/<job>/timing.json`의 `sentences`(문장 번호, 시작/끝 초)를 보고 장면을 나눈다.
장면은 `at` 시점에 시작해서 **다음 장면 시작 직전까지** 이어진다. 첫 장면은 항상 0초부터.

## TimeRef (`at` 값)
- 숫자: 그 문장 번호(0부터)가 시작될 때
- 문자열: 장면 시작 이후 처음 나오는, 이 글자를 포함한 어절이 발음될 때 (예: `"위스퍼"`)

## 텍스트 강조
`*별표*`로 감싼 부분은 강조색으로 표시된다. 예: `"대본만 *쓰면 끝*"`

## 장면 종류

| type | props | 용도 |
|---|---|---|
| `hook` | `lines: [{text, at?}]` | 도입 질문, 큰 문장 여러 줄 |
| `keyword` | `text, sub?, chip?, at?` | 핵심 키워드 하나를 크게 |
| `title` | `kicker?, title, subtitle?` | 챕터 제목, 마무리 |
| `steps` | `title?, steps: [{label, sub?, icon?, at}]` | 순서·흐름. 말에 맞춰 카드가 하나씩 켜짐 (최대 5개) |
| `terminal` | `title?, lines: [{text, at?, color?}]` | 명령어, 코드, 로그 타이핑 |
| `bullets` | `title, items: [{text, at?}]` | 체크리스트, 장점 나열 |

## 최상위 옵션
- `captions`: 하단 자막 표시 (기본 true)
- `theme`: `{bg, bg2, fg, dim, accent, accent2, card}` 일부만 덮어써도 됨

## 원칙
- 한 장면은 보통 1~3문장. 10초 넘게 같은 화면이면 지루하다.
- 화면 글자는 자막을 그대로 반복하지 말고 **핵심만 짧게** 요약한다.
- 비슷한 종류의 장면을 연달아 쓰지 않는다.
