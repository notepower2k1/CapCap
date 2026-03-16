import requests
import os
from dotenv import load_dotenv

# Load environment variables from project root
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(base_dir, '.env')
load_dotenv(env_path)

API_KEY = os.getenv("TRANSLATOR_API_KEY")
url = os.getenv("TRANSLATOR_URL")

subtitle = """1
00:00:00,000 --> 00:00:03,800
终于找到了一款颜值高 而且还是全玻璃的养生壶
2
00:00:03,800 --> 00:00:06,240
这样就不用担心有水过和胶水嘛
3
00:00:06,240 --> 00:00:09,160
整个湖身都是通透耐热的高朋规玻璃
4
00:00:09,160 --> 00:00:10,920
而且它还带了一个水龙头
5
00:00:10,920 --> 00:00:13,840
平时用它煮好花茶果茶 拧开就能接水
6
00:00:13,840 --> 00:00:16,760
有八大功能选择 操作也特别简单
7
00:00:16,760 --> 00:00:19,360
煮好之后自动保温24小时
8
00:00:19,360 --> 00:00:21,440
这样随时都可以喝到一杯热茶
9
00:00:21,440 --> 00:00:24,360
还带了一个滤网 满足不同泡茶需求
10
00:00:24,360 --> 00:00:27,840
真的太方便了 喜欢你也赶紧入手一台吧
"""

def split_srt_blocks(srt_text):
    return [b.strip() for b in srt_text.strip().split("\n\n") if b.strip()]

def chunk_blocks(blocks, size):
    for i in range(0, len(blocks), size):
        yield blocks[i:i+size]

blocks = split_srt_blocks(subtitle)

translated_blocks = []

for chunk in chunk_blocks(blocks, 5):

    sub = "\n\n".join(chunk)

    prompt = f"""
Translate the Chinese subtitles into Vietnamese.

Rules:
- The subtitles are in SRT format
- Keep index numbers unchanged
- Keep timestamps unchanged
- Only translate subtitle text
- Keep the exact same SRT structure
- Each subtitle block must remain exactly the same except the text line.
Subtitles:
{sub}
"""

    r = requests.post(
        url,
        headers={
            "x-api-key": API_KEY,
            "Content-Type": "application/json"
        },
        json={"text": prompt}
    )

    data = r.json()
    text = data.get("text") or data.get("response")

    if text:
        translated_blocks.append(text.strip())

final_subtitle = "\n\n".join(translated_blocks)

print(final_subtitle)