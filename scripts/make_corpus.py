"""Original controlled regression text, with authored role labels independent of any provider."""

import json
from pathlib import Path

PEOPLE = "林渊 阿宁 周雪 江荻 沈禾 叶舟 陈星 许桥 夏石 金桐 郭溪 齐韵 梅青 何西 方朔 罗茵 季桦 陆笙 唐平 杜雨".split()
SCENES = [
    ("渡口", "长桨拨开了第三道水纹。", "那是最后一条船吗？", "等我系好缆绳。"),
    ("灯市", "新灯笼上画着一尾小鱼。", "还有蓝色的纸吗？", "我给你裁一张。"),
    ("驿站", "铜铃上结了一圈白霜。", "信要在今晚送到。", "马已经备好了。"),
    ("测站", "指针停在刻度线以西。", "前一组读数是多少？", "请再校准一次。"),
    ("库房", "清单上的数字是12。", "箱底还有一本账册。", "编号能对上吗？"),
    ("档案室", "抽屉深处露出半张车票。", "这个日期被改过。", "把原件拿到灯下。"),
    ("修理铺", "小扳手落在木桌边缘。", "先关掉传动轴。", "新的轴承合适吗？"),
    ("旧巷", "梧桐的影子越过矮墙。", "桂花又开了。", "给邻居留一枝吧。"),
    ("田埂", "露水沿着稻叶慢慢滑落。", "今天的风真轻。", "小鹭还在等鱼。"),
    ("海堤", "潮声从窗缝漫进屋里。", "再坐一会儿好吗？", "我去泡一壶茶。"),
]
SHORT = ("好。", "走。", "等。", "听。", "快。", "嗯。")
ACTIONS = ("问", "轻声说", "回头答", "停下笔说", "点头说", "想了想，答")


def build():
    chapters = []
    for i, (place, description, line1, line2) in enumerate(SCENES):
        groups = []
        for j in range(3):
            a, b = PEOPLE[(i * 2 + j) % 20], PEOPLE[(i * 2 + j + 1) % 20]
            groups.append(
                (
                    "multi",
                    [
                        (a + ACTIONS[j] + "：", "narrator"),
                        ("“" + line1 + "”", a),
                        (b + ACTIONS[j + 3] + "：", "narrator"),
                        ("“" + SHORT[(i + j) % 6] + "”", b),
                    ],
                )
            )
        for j in range(9):
            who = PEOPLE[(i * 2 + j) % 20]
            phrase = (line1, line2, "我在" + place + "等你。")[j % 3]
            groups.append(
                (
                    "clear",
                    [
                        (who + ACTIONS[j % 6] + "：", "narrator"),
                        ("“" + phrase + "”", who),
                    ],
                )
            )
        for j in range(3):
            groups.append(
                (
                    "ambiguous",
                    [("“" + ("是谁？", "还在吗？", "听到了。")[j] + "”", "unknown")],
                )
            )
        for phrase in (description, place + "的路渐渐安静了。"):
            groups.append(("narration", [(phrase, "narrator")]))
        records = []
        for n, (kind, parts) in enumerate(groups):
            s = ""
            gold = []
            for words, speaker in parts:
                gold.append(
                    {"start": len(s), "end": len(s) + len(words), "speaker": speaker}
                )
                s += words
            records.append(
                {"id": f"c{i + 1}p{n + 1}", "kind": kind, "text": s, "gold": gold}
            )
        chapters.append(
            {
                "title": f"第{i + 1}章 {place}",
                "genre": ("叙事", "调查记录", "生活散文")[min(2, i // 3)],
                "parents": records,
            }
        )
    return {
        "version": 1,
        "provenance": "原创受控模板及人工指定角色；未经独立盲审。",
        "characters": PEOPLE,
        "chapters": chapters,
    }


if __name__ == "__main__":
    folder = Path(__file__).resolve().parents[1] / "fixtures"
    (folder / "regression.json").write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
