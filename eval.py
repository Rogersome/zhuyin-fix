"""測試集：把中文句子反向編碼成大千按鍵，再解碼回來，量測命中率。

python eval.py            # 印出命中率與失敗案例
"""
import sys

from zhuyin_fix import KEYMAP, TONE_KEYS, ZhuyinFixer

SENTENCES = """
你好 我是郭家穎 今天天氣很好 你吃飯了嗎 我們等一下要去哪裡 明天早上開會
這個問題很簡單 謝謝你的幫忙 晚上一起吃飯 我快到了 你在哪裡 不好意思我遲到了
這件衣服多少錢 我想喝咖啡 下課之後去圖書館 老師說明天要考試 我覺得還不錯
可以幫我看一下嗎 週末要不要去看電影 我剛剛下班 手機沒電了 我等等打給你
台北的捷運很方便 這家餐廳很好吃 我有點累了 你今天有空嗎 請問洗手間在哪裡
我們公司在找工程師 下雨了記得帶傘 生日快樂 新年快樂 我愛你 沒問題
報告寫完了嗎 我在高鐵上 先這樣 晚安 早安 辛苦了 收到 好的謝謝
研究所考試 資訊工程 演算法 作業系統 計算機網路 資料結構
""".split()

NAMES = "郭家穎 陳怡君 林志明 王小明 張雅婷 李承恩 黃冠宇".split()

INV_KEY = {v: k for k, v in KEYMAP.items()}
INV_TONE = {v: k for k, v in TONE_KEYS.items()}


def encode(reading: str) -> str:
    out = []
    for syl in reading.split():
        tone = syl[-1] if syl[-1] in "ˊˇˋ˙" else ""
        body = syl[:-1] if tone else syl
        out.append("".join(INV_KEY[c] for c in body) + INV_TONE[tone])
    return "".join(out)


def reading_of(engine, sentence: str) -> str | None:
    """以詞庫最長匹配取得句子的讀音（每個詞取詞頻最高的讀音）。"""
    # 多音字：選「這個字在該讀音裡排名最前面」的讀音（家 -> ㄐㄧㄚ 而不是 ㄍㄨ）
    best = {}
    for r, entries in engine.dict.items():
        for rank, (w, f) in enumerate(entries):
            key = (rank, -f)
            if w not in best or key < best[w][1]:
                best[w] = (r, key)
    parts, i = [], 0
    while i < len(sentence):
        for L in range(min(6, len(sentence) - i), 0, -1):
            w = sentence[i:i + L]
            if w in best:
                parts.append(best[w][0])
                i += L
                break
        else:
            return None
    return " ".join(parts)


def run(engine, items, label):
    top1 = top5 = 0
    fails = []
    for s in items:
        r = reading_of(engine, s)
        keys = encode(r)
        cands = [c.text for c in engine.decode(keys)]
        top1 += bool(cands) and cands[0] == s
        top5 += s in cands
        if not cands or cands[0] != s:
            fails.append((s, keys, cands[:3]))
    n = len(items)
    print(f"{label}: 第一候選 {top1}/{n} = {top1/n:.0%}，前五候選 {top5}/{n} = {top5/n:.0%}")
    return fails


# 漏打或多打一個鍵（手動模式的容錯）：(按鍵, 期望結果, 說明)
TYPOS = [
    ("ei ru8 u/3", "郭佳穎", "郭 漏打 ㄨ"),
    ("v,6vul4", "學校", "學 漏打 ㄩ"),
    ("v,4vu,4", "謝謝", "謝 漏打 ㄧ"),
    ("ejji ru8 u/3", "郭佳穎", "郭 多打一個 ㄨ"),
    ("su3ccl3", "你好", "好 多打一個 ㄏ"),
]

FALSE_POSITIVES = """hello world, see you tomorrow | ok | lol | my password is abc123 |
https://www.google.com | I love you | iPhone 15 | test@gmail.com | 2026-10-22 |
u3 | yes | git commit -m fix""".split("|")


def main():
    engine = ZhuyinFixer()
    fails = run(engine, SENTENCES, "日常句子")
    fails += run(engine, NAMES, "人名")
    fp = 0
    for t in FALSE_POSITIVES:
        t = t.strip()
        fixed, _ = engine.fix_text(t)
        if fixed != t:
            fp += 1
            fails.append(("[誤判]", t, [fixed]))
    print(f"誤判（英文被改掉）: {fp}/{len(FALSE_POSITIVES)}")
    ok = 0
    for keys, want, note in TYPOS:
        got = [c.text for c in engine.decode(keys)]
        ok += bool(got) and got[0] == want
        if not got or got[0] != want:
            fails.append((f"[容錯] {want}（{note}）", keys, got[:3]))
    print(f"打錯一鍵容錯: 第一候選 {ok}/{len(TYPOS)}")
    for s, k, c in fails:
        print(f"  ✗ {s}  {k!r} -> {c}")

    habit_tests()


def top(engine, keys, n=3):
    return " / ".join(c.text for c in engine.decode(keys, k=n))


def habit_tests():
    """模擬不同使用者的習慣（不寫檔，只在記憶體中）。"""
    print("\n=== 使用習慣學習 ===")

    print("\n[使用者 A] 習慣打「妳」")
    a = ZhuyinFixer()
    print(f"  學習前 su3cl3     -> {top(a, 'su3cl3')}")
    a.select("su3cl3", "妳好")
    print(f"  選一次「妳好」後  -> {top(a, 'su3cl3')}")
    print(f"  其他句子 su3ap7  -> {top(a, 'su3ap7')}   （只學「妳好」這個詞，不影響「你」的其他用法）")

    print("\n[使用者 B] 同學叫郭家穎（同音人名）")
    b = ZhuyinFixer()
    keys = "eji ru8 u/3"
    print(f"  學習前             -> {top(b, keys)}")
    b.select(keys, "郭家穎")
    print(f"  選一次後           -> {top(b, keys)}")
    print(f"  夾在句子裡         -> {b.fix_text('我同學叫eji ru8 u/3, nice to meet you')[0]}")

    print("\n[使用者 B] 習慣改變：之後改選「你好」三次")
    b.select("su3cl3", "妳好")
    print(f"  先選「妳好」一次   -> {top(b, 'su3cl3')}")
    for _ in range(3):
        b.select("su3cl3", "你好")
    print(f"  再選「你好」三次   -> {top(b, 'su3cl3')}")

    print("\n[前後文] 常說「帶傘」")
    c = ZhuyinFixer()
    k1 = "vu84m3xk7ru42k6294n03"           # 下雨了記得帶傘
    print(f"  學習前 下雨了記得帶傘 -> {top(c, k1, 1)}")
    c.select(k1, "下雨了記得帶傘")
    k2 = "go6294n03"                      # 誰帶傘（不同句子）
    print(f"  選一次後，換句「誰帶傘」 -> {top(c, k2)}")

    print("\n[刪除學錯的詞]")
    b.forget("郭家穎")
    print(f"  forget 郭家穎 後   -> {top(b, keys)}")


if __name__ == "__main__":
    main()
