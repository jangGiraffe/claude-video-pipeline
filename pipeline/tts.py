"""대본 텍스트 → Gemini TTS → WAV.

사용법:
  python pipeline/tts.py 대본.txt 출력.wav [--voice Kore] [--style "차분하고 친근하게"]
"""
import argparse
import base64
import io
import sys
import wave
from pathlib import Path

from google import genai

from common import get_api_key

MODEL = "gemini-3.8-flash-tts"


def synthesize(text: str, out_path: Path, voice: str, style: str | None) -> None:
    client = genai.Client(api_key=get_api_key())
    content = {"type": "text", "text": text}
    if style:
        # 말투 지시는 본문에 섞으면 소리 내어 읽어버린다 → speech_metadata.style로 분리
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    interaction = client.interactions.create(
        model=MODEL,
        input=[{"type": "user_input", "content": [content]}],
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    data = base64.b64decode(interaction.output_audio.data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if data[:4] == b"RIFF":
        out_path.write_bytes(data)
    else:
        # 헤더 없는 PCM(24kHz, mono, 16bit)이면 WAV 헤더를 붙인다
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(data)
        out_path.write_bytes(buf.getvalue())
    print(f"saved {out_path} ({len(data)} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="대본 txt 파일 (또는 --text)")
    ap.add_argument("out", help="출력 wav 경로")
    ap.add_argument("--voice", default="Kore")
    ap.add_argument("--style", default=None, help="말투 지시 (예: '차분하고 친근한 유튜버 톤으로 읽어줘')")
    ap.add_argument("--text", action="store_true", help="script 인자를 파일이 아닌 텍스트로 취급")
    a = ap.parse_args()
    text = a.script if a.text else Path(a.script).read_text(encoding="utf-8-sig")
    synthesize(text.strip(), Path(a.out), a.voice, a.style)


if __name__ == "__main__":
    sys.exit(main())
